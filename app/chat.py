"""Boss <-> Project Manager / employee chat. Answers are computed from the real database
(the same functions the dashboard and the Sheet use) — the LLM only helps understand the question
and phrase open-ended answers, never invents numbers."""
import json
import re

from sqlalchemy import func
from datetime import datetime, timedelta

from . import llm, metrics
from .bridges import integrations as ix
from .db import (Task, Lead, Post, Ticket, ChatMessage, utcnow, fmt, local_today, local_to_utc, TZ, day_start_utc)
from .team import BY_KEY, KEYS, resolve_agent, system_prompt, label

CREATE_RE = re.compile(r"\b(assign|create|add|give|schedule|delegate)\b.*\b(task|to)\b", re.I)


# ---------------- helpers ----------------
def parse_deadline(text):
    t = (text or "").lower()
    today = local_today()
    d = None
    if "tomorrow" in t:
        d = today + timedelta(days=1)
    elif "today" in t or "tonight" in t:
        d = today
    else:
        m = re.search(r"in (\d+) days?", t)
        if m:
            d = today + timedelta(days=int(m.group(1)))
        m2 = re.search(r"(20\d\d-\d\d-\d\d)", t)
        if m2:
            d = datetime.strptime(m2.group(1), "%Y-%m-%d").date()
        for i, wd in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]):
            if wd in t:
                d = today + timedelta(days=(i - today.weekday()) % 7 or 7)
    if not d:
        return None
    return local_to_utc(datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ))


def parse_priority(text):
    t = (text or "").lower()
    for p in ("urgent", "high", "low", "medium"):
        if p in t:
            return p.title()
    return "Medium"


def create_task_from_text(db, text, default_agent=None, by="Boss (chat)"):
    agent = default_agent if default_agent and default_agent != "atlas" else None
    found = resolve_agent(re.sub(r"\b(assign|task|to)\b", " ", text, flags=re.I))
    if found:
        agent = found
    title, desc = None, text
    try:
        data = llm.ask_json("You extract task details for a task manager.",
                            f"Boss message: {text}\nEmployees: " + ", ".join(f"{k}={label(k)}" for k in KEYS)
                            + '\nJSON: {"agent":"<key>","title":"short imperative title","description":"full details"}')
        if data.get("agent") in KEYS and not found:
            agent = data["agent"]
        title = (data.get("title") or "").strip() or None
        desc = (data.get("description") or text).strip()
    except Exception:
        pass
    agent = agent or "atlas"
    if not title:
        m = re.search(r":\s*(.+)", text)
        title = (m.group(1) if m else text)
        title = re.split(r",?\s*due\b", title, flags=re.I)[0].strip()[:150]
    t = Task(assigned_by=by, agent=agent, title=title[:290], description=desc[:3000],
             priority=parse_priority(text), deadline=parse_deadline(text))
    db.add(t)
    db.commit()
    ix.sheet_upsert_async(t.id)
    from .engine import process_task_later
    process_task_later(t.id, 6)
    return t


# ---------------- data answers ----------------
def ans_running(db):
    c = metrics.status_counts(db)
    running = db.query(Task).filter(Task.status == "In Progress").all()
    lines = [f"Right now: **{c['In Progress']} in progress**, {c['Queued']} queued, {c['Blocked']} blocked "
             f"(total active: {c['In Progress'] + c['Queued'] + c['Blocked']}). "
             f"Completed today: {metrics.completed_between(db, day_start_utc()).count()}, all-time: {c['Completed']}."]
    for t in running:
        lines.append(f"- {t.code} · {BY_KEY[t.agent]['name']}: {t.title} (started {fmt(t.started_at)})")
    q = db.query(Task).filter(Task.status == "Queued").order_by(Task.id).limit(8).all()
    if q:
        lines.append("Next in queue: " + "; ".join(f"{t.code} {BY_KEY[t.agent]['name']}: {t.title}" for t in q))
    return "\n".join(lines)


def ans_sales(db):
    leads = metrics.leads_today(db)
    pipe = dict(db.query(Lead.status, func.count(Lead.id)).group_by(Lead.status).all())
    st = metrics.employee_stats(db, "closer")
    lines = [f"Sales team (Scout + Closer): **{len(leads)} leads generated today**. Pipeline totals: "
             + (", ".join(f"{k}: {v}" for k, v in pipe.items()) or "empty")
             + f". Closer completed {st['completed']} task(s) today, {st['pending']} pending."]
    for l in leads[:25]:
        lines.append(f"- {l.name} | {l.category or '-'} | {l.address or '-'} | phone {l.phone or '-'} | "
                     f"website {l.website or 'none'} | rating {l.rating or '-'} ({l.reviews or 0} reviews) | "
                     f"source: {l.source} | status: {l.status} | score {l.score}")
    if not leads:
        lines.append("No leads yet today — Scout's daily Google Maps run creates them.")
    return "\n".join(lines)


def ans_posts(db):
    posts = metrics.posts_published_today(db)
    pc = metrics.platform_counts(posts)
    lines = [f"**{len(posts)} posts published today**" + (" — " + ", ".join(f"{k}: {v}" for k, v in pc.items()) if pc else "") + "."]
    for p in posts:
        lines.append(f"- #{p.id} '{p.title}' ({p.platform} copy) -> {p.published_on} at {fmt(p.published_at)} · {p.link}")
    drafts = db.query(Post).filter(Post.status.in_(["Draft", "Approved"])).count()
    lines.append(f"Waiting: {drafts} drafted/approved posts not yet published.")
    return "\n".join(lines)


def ans_blocked(db):
    bl = db.query(Task).filter(Task.status == "Blocked").order_by(Task.id).all()
    if not bl:
        return "No tasks are blocked right now. ✅"
    return f"**{len(bl)} blocked task(s):**\n" + "\n".join(
        f"- {t.code} · {BY_KEY[t.agent]['name']}: {t.title} — why: {t.blocked_reason}" for t in bl)


def ans_top(db):
    rows = metrics.top_employee_week(db)
    if not rows:
        return "No tasks completed yet this week."
    top = rows[0]
    return (f"**{BY_KEY[top[0]]['name']} ({BY_KEY[top[0]]['title']})** completed the most tasks this week: {top[1]}.\n"
            + "Ranking: " + ", ".join(f"{BY_KEY[a]['name']} {n}" for a, n in rows))


def ans_tickets(db):
    s = metrics.tickets_stats(db, day_start_utc())
    esc = db.query(Ticket).filter(Ticket.status == "Escalated").order_by(Ticket.id.desc()).limit(5).all()
    out = (f"Support today: {s['received']} received, {s['resolved']} resolved, {s['escalated']} escalated; "
           f"{s['open']} open now.")
    if esc:
        out += "\nEscalations: " + "; ".join(f"#{t.id} {t.subject} ({t.escalation_reason})" for t in esc)
    return out


def ans_employee(db, key):
    s = metrics.employee_stats(db, key)
    cur = s["current"] or s["next"]
    return (f"{s['emoji']} **{s['name']} — {s['title']}** is {'ACTIVE' if s['active'] else 'idle'}. "
            f"Today: {s['completed']} completed, {s['pending']} pending ({s['blocked']} blocked). "
            f"{s['kpi_label']} today: {s['kpi_value']}. "
            + (f"{'Working on' if s['active'] else 'Next up'}: {cur.code} {cur.title}." if cur else "No open tasks."))


def snapshot(db):
    s = metrics.agency_summary(db, "daily")
    s["employees"] = [{k: v for k, v in e.items() if k not in ("current", "next")} for e in s["employees"]]
    s["blocked_tasks"] = [{"id": t.code, "agent": t.agent, "title": t.title, "reason": t.blocked_reason}
                          for t in db.query(Task).filter(Task.status == "Blocked").limit(10)]
    s["recent_tasks"] = [{"id": t.code, "agent": t.agent, "title": t.title, "status": t.status}
                         for t in db.query(Task).order_by(Task.id.desc()).limit(15)]
    return s


INTENT_PATTERNS = [
    ("blocked", r"\bblock"),
    ("top", r"\b(most tasks|top employee|best employee|completed the most|most productive|who did the most)"),
    ("sales", r"\b(sales|leads?|pipeline|prospects?)\b"),
    ("posts", r"\b(posts?|published|publish|platforms?|social)\b"),
    ("tickets", r"\b(tickets?|support|customers? complain|escalat)"),
    ("running", r"\b(running|in progress|currently|how many (projects|tasks)|active tasks|status of (all|the agency))"),
]


def detect_intent(msg):
    m = msg.lower()
    if CREATE_RE.search(m) and ("task" in m or resolve_agent(m)):
        return "create"
    for name, pat in INTENT_PATTERNS:
        if re.search(pat, m):
            return name
    k = resolve_agent(m)
    if k and re.search(r"\b(status|doing|working|how is|what is)\b", m):
        return "employee:" + k
    return "general"


def answer_atlas(db, msg):
    intent = detect_intent(msg)
    if intent == "create":
        t = create_task_from_text(db, msg)
        return (f"✅ Created **{t.code}** for {label(t.agent)}: “{t.title}” · priority {t.priority}"
                + (f" · due {fmt(t.deadline)}" if t.deadline else "")
                + ". Logged in the Google Sheet; status will move Queued → In Progress → Completed on the Task Board.")
    if intent == "running":
        return ans_running(db)
    if intent == "sales":
        return ans_sales(db)
    if intent == "posts":
        return ans_posts(db)
    if intent == "blocked":
        return ans_blocked(db)
    if intent == "top":
        return ans_top(db)
    if intent == "tickets":
        return ans_tickets(db)
    if intent.startswith("employee:"):
        return ans_employee(db, intent.split(":", 1)[1])
    data = json.dumps(snapshot(db), default=str)[:12000]
    try:
        return llm.ask(system_prompt("atlas") + "\nAnswer the Boss using ONLY the JSON data provided. If the data does not "
                                                "contain the answer, say so.", f"DATA:\n{data}\n\nBOSS: {msg}")
    except Exception:
        return "(LLM offline — here is the live status instead)\n" + ans_running(db)


def answer_employee(db, key, msg):
    if CREATE_RE.search(msg.lower()) and "task" in msg.lower():
        t = create_task_from_text(db, msg, default_agent=key)
        return f"Got it — I created {t.code} “{t.title}” for {label(t.agent)}. I'll pick it up shortly."
    mine = db.query(Task).filter(Task.agent == key).order_by(Task.id.desc()).limit(15).all()
    data = {"me": {k: v for k, v in metrics.employee_stats(db, key).items() if k not in ("current", "next")},
            "my_recent_tasks": [{"id": t.code, "title": t.title, "status": t.status, "created": fmt(t.created_at),
                                 "completed": fmt(t.completed_at), "output": (t.output or t.blocked_reason)[:400]}
                                for t in mine]}
    if key == "scout":
        data["leads_today"] = [{"name": l.name, "category": l.category, "status": l.status} for l in metrics.leads_today(db)]
    if key in ("echo", "quill"):
        data["posts_today"] = [{"title": p.title, "platform": p.published_on or p.platform, "status": p.status}
                               for p in db.query(Post).filter(Post.created_at >= day_start_utc())]
    try:
        return llm.ask(system_prompt(key) + "\nYou are chatting with your Boss about your own work. Answer in first "
                                            "person using ONLY this JSON data.",
                       f"DATA:\n{json.dumps(data, default=str)[:10000]}\n\nBOSS: {msg}")
    except Exception:
        return ans_employee(db, key)


def handle(db, thread, msg):
    db.add(ChatMessage(thread=thread, role="boss", content=msg))
    db.commit()
    reply = answer_atlas(db, msg) if thread == "atlas" else answer_employee(db, thread, msg)
    db.add(ChatMessage(thread=thread, role="agent", content=reply))
    db.commit()
    return reply
