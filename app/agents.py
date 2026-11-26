"""What each employee actually does when it picks up a task."""
import json
import re

from . import config, llm
from .bridges import integrations as ix
from .db import (Task, Lead, Post, Ticket, Activity, Report, utcnow, fmt, local_today, day_start_utc, log)
from .team import BY_KEY, KEYS, system_prompt, resolve_agent
from . import metrics


class Blocked(Exception):
    """Raise to mark a task Blocked with a human-readable reason."""


def _is_routine(task, name):
    return bool(task.routine_key and task.routine_key.endswith(":" + name))


# ======================= ATLAS (Project Manager) =======================
def atlas(db, task):
    if _is_routine(task, "plan"):
        return "Daily plan created (routine tasks queued for all employees).", ""
    # Boss goal -> break down and delegate
    team_desc = "\n".join(f"- {k}: {BY_KEY[k]['name']}, {BY_KEY[k]['title']}: {BY_KEY[k]['responsibilities']}"
                          for k in KEYS if k != "atlas")
    try:
        data = llm.ask_json(
            system_prompt("atlas"),
            f"The Boss gave you this goal:\nTITLE: {task.title}\nDETAILS: {task.description}\n\n"
            f"Your team:\n{team_desc}\n\nBreak it into 1-4 concrete sub-tasks for the most suitable employees. "
            f'JSON format: {{"subtasks":[{{"agent":"<key>","title":"...","description":"...","priority":"Low|Medium|High|Urgent"}}]}}')
    except Exception as e:
        raise Blocked(f"Could not plan the goal (LLM unavailable): {e}")
    subs = data.get("subtasks", []) if isinstance(data, dict) else data
    created = []
    for s in subs[:4]:
        key = s.get("agent") if s.get("agent") in KEYS else resolve_agent(str(s.get("agent", "")))
        if not key or key == "atlas":
            continue
        t = Task(assigned_by="Atlas (PM)", agent=key, title=str(s.get("title", ""))[:290] or task.title,
                 description=str(s.get("description", ""))[:2000],
                 priority=s.get("priority") if s.get("priority") in ("Low", "Medium", "High", "Urgent") else task.priority,
                 deadline=task.deadline, parent_id=task.id)
        db.add(t)
        db.commit()
        ix.sheet_upsert_async(t.id)
        created.append(f"{t.code} -> {BY_KEY[key]['name']}: {t.title}")
    if not created:
        raise Blocked("Planner returned no valid sub-tasks.")
    return "Delegated:\n" + "\n".join(created), ""


# ======================= SCOUT (Lead Researcher) =======================
def scout(db, task):
    m = re.search(r"query\s*:\s*(.+)", task.description or "", re.I)
    if m:
        query = m.group(1).strip().splitlines()[0]
    elif task.routine_key:
        query = config.LEAD_QUERIES[local_today().toordinal() % len(config.LEAD_QUERIES)]
    else:
        query = (task.title + " " + (task.description or "")).strip()
        if "riga" not in query.lower():
            query += " in Riga Latvia"
    try:
        items = ix.apify_google_maps(query)
    except Exception as e:
        raise Blocked(f"Apify scrape failed: {e}")
    new = 0
    names = []
    for it in items:
        name = (it.get("title") or "").strip()
        if not name:
            continue
        addr = (it.get("address") or it.get("street") or "").strip()
        if db.query(Lead).filter(Lead.name == name, Lead.address == addr).first():
            continue
        db.add(Lead(name=name[:290], category=(it.get("categoryName") or "")[:190], address=addr[:390],
                    phone=(it.get("phone") or "")[:70], website=(it.get("website") or "")[:390],
                    rating=it.get("totalScore"), reviews=it.get("reviewsCount"), maps_url=(it.get("url") or "")[:590],
                    search_query=query[:290], task_id=task.id))
        new += 1
        names.append(name)
    db.commit()
    return (f"Searched Google Maps for '{query}': {len(items)} places scraped, {new} new leads saved."
            + (f"\nNew: {', '.join(names[:10])}" if names else ""), f"{config.PUBLIC_BASE_URL}/boss/leads")


# ======================= CLOSER (Sales) =======================
def lead_score(lead):
    s = 0
    if not lead.website:
        s += 40
    if lead.rating is not None and lead.rating < 4.3:
        s += 20
    if lead.reviews is not None and lead.reviews < 60:
        s += 20
    if lead.phone:
        s += 10
    if lead.category:
        s += 10
    return s


def closer(db, task):
    leads = db.query(Lead).filter(Lead.status == "New").order_by(Lead.id).limit(5).all()
    if not leads:
        return "No new leads waiting. Nothing to qualify.", ""
    done = []
    for i, lead in enumerate(leads):
        lead.score = lead_score(lead)
        if lead.score < 40:
            lead.status = "Low Fit"
            done.append(f"{lead.name}: score {lead.score} -> Low Fit")
            continue
        facts = (f"Business: {lead.name}\nCategory: {lead.category}\nAddress: {lead.address}\n"
                 f"Website: {lead.website or 'NONE'}\nGoogle rating: {lead.rating} from {lead.reviews} reviews")
        q = (f"{facts}\n\nWrite a short, friendly, personalised first-contact email (max 120 words, with a subject line) "
             f"offering LocalLift's Growth Starter Pack (EUR 99/month: lead audit, 3 social posts/week, review replies). "
             f"Mention one specific observation from the facts. No fake claims.")
        try:
            if i == 0:  # best lead of the batch gets the full LLM Council
                res = llm.council(q, system_prompt("closer"))
                lead.outreach_draft = res["final"]
                task.council_json = json.dumps(res)[:60000]
            else:
                lead.outreach_draft = llm.ask(system_prompt("closer"), q)
        except Exception as e:
            db.commit()
            raise Blocked(f"LLM unavailable while drafting outreach: {e}")
        lead.status = "Draft Ready"
        done.append(f"{lead.name}: score {lead.score} -> Draft Ready")
    db.commit()
    return "Qualified leads:\n" + "\n".join(done) + "\n(Drafts only — nothing is sent without Boss approval.)", \
        f"{config.PUBLIC_BASE_URL}/boss/leads"


# ======================= QUILL (Content Writer) =======================
def quill(db, task):
    brief = (task.title + "\n" + (task.description or "")).strip()
    if task.routine_key:
        brief = ("Write 2 posts for LocalLift AI's own marketing today: one LinkedIn post and one Telegram post. "
                 "Topic ideas: tips for local cafes/salons in Riga on getting more Google reviews, posting consistently, "
                 "answering customers fast; or a short case-style story of how automation saves owners time. "
                 f"Today is {local_today().strftime('%A %d %B %Y')} — vary the topic.")
    q = (f"Brief: {brief}\n\nWrite the posts. Each post: platform (LinkedIn, Telegram or Blog), a short title and the body "
         f"(LinkedIn 80-180 words, Telegram 40-100 words, plain text, max 3 hashtags). If the brief asks for a number of "
         f'posts, write exactly that many. JSON format: {{"posts":[{{"platform":"LinkedIn","title":"...","body":"..."}}]}}')
    try:
        res = llm.council(q, system_prompt("quill"), json_output=True, max_tokens=1800)
        data = llm.extract_json(res["final"])
    except Exception as e:
        raise Blocked(f"Could not write posts (LLM error): {e}")
    posts = data.get("posts", []) if isinstance(data, dict) else data
    task.council_json = json.dumps(res)[:60000]
    made = []
    for p in posts[:6]:
        body = str(p.get("body", "")).strip()
        if not body:
            continue
        plat = str(p.get("platform", "LinkedIn")).strip().title()
        if plat not in ("Linkedin", "Telegram", "Blog"):
            plat = "LinkedIn"
        plat = "LinkedIn" if plat == "Linkedin" else plat
        post = Post(platform=plat, title=str(p.get("title", ""))[:290], body=body, task_id=task.id)
        db.add(post)
        db.commit()
        made.append(f"#{post.id} [{plat}] {post.title}")
    if not made:
        raise Blocked("Council returned no usable posts.")
    if not task.routine_key:  # Boss request -> hand over to QA and publishing straight away
        from .engine import process_task_later
        for agent, title, delay in (("sentinel", "QA the new posts", 5), ("echo", "Publish approved posts", 45)):
            t = Task(assigned_by="Atlas (PM)", agent=agent, title=title, parent_id=task.id,
                     description=f"Follow-up of {task.code}: audit/QA and publish.", priority=task.priority)
            db.add(t)
            db.commit()
            ix.sheet_upsert_async(t.id)
            process_task_later(t.id, delay)
    return f"Drafted {len(made)} post(s) (best version chosen by LLM Council):\n" + "\n".join(made), \
        f"{config.PUBLIC_BASE_URL}/boss/posts"


# ======================= SENTINEL (QA & Auditor) =======================
BANNED = ["lorem", "[insert", "{name}", "[your", "as an ai"]


def qa_post(p):
    b = (p.body or "").lower()
    if len(p.body or "") < 40:
        return "too short"
    if len(p.body) > 3500:
        return "too long"
    for w in BANNED:
        if w in b:
            return f"contains placeholder/banned text '{w}'"
    return ""


def sentinel(db, task):
    notes = []
    # 1) QA drafts
    for p in db.query(Post).filter(Post.status == "Draft").all():
        issue = qa_post(p)
        if issue:
            p.status, p.qa_note = "Rejected", issue
            log(db, "sentinel", f"Rejected post #{p.id}: {issue}", "flag")
            notes.append(f"Rejected post #{p.id} ({issue})")
        else:
            p.status, p.qa_note = "Approved", "Passed QA"
            notes.append(f"Approved post #{p.id}")
    db.commit()
    # 2) Stalled tasks
    from datetime import timedelta
    cutoff = utcnow() - timedelta(minutes=20)
    for t in db.query(Task).filter(Task.status == "In Progress", Task.started_at < cutoff, Task.id != task.id).all():
        t.status, t.blocked_reason = "Blocked", "Stalled: no progress for 20+ minutes (flagged by Sentinel)."
        db.commit()
        ix.sheet_upsert_async(t.id)
        log(db, "sentinel", f"{t.code} stalled -> Blocked", "flag")
        notes.append(f"{t.code} stalled -> Blocked")
    # 3) Overdue tasks
    overdue = db.query(Task).filter(Task.deadline != None, Task.deadline < utcnow(),  # noqa
                                    Task.status.in_(["Queued", "In Progress", "Blocked"])).all()
    for t in overdue:
        log(db, "sentinel", f"{t.code} is overdue (deadline {fmt(t.deadline)})", "flag")
    if overdue:
        notes.append("Overdue: " + ", ".join(t.code for t in overdue))
    # 4) Ledger reconciliation
    unsynced = db.query(Task).filter(Task.sheet_synced == False).limit(40).all()  # noqa
    fixed = 0
    for t in unsynced:
        if ix.sheet_upsert(t):
            t.sheet_synced = True
            fixed += 1
    db.commit()
    notes.append(f"Ledger reconciliation: {fixed}/{len(unsynced)} unsynced rows pushed to Google Sheet.")
    # 5) Integration health
    missing = [k for k, v in config.integration_status().items() if v is False]
    if missing:
        log(db, "sentinel", "Integrations not configured: " + ", ".join(missing), "flag")
        notes.append("Not configured: " + ", ".join(missing))
    return "Audit complete:\n" + "\n".join(notes), ""


# ======================= ECHO (Publisher) =======================
def echo(db, task):
    posts = db.query(Post).filter(Post.status == "Approved").order_by(Post.id).limit(6).all()
    if not posts:
        return "No approved posts waiting to be published.", ""
    out, errors = [], []
    for p in posts:
        where, link = [], ""
        text = (p.title + "\n\n" if p.title else "") + p.body
        if p.platform in ("Telegram", "LinkedIn") and config.TELEGRAM_BOT_TOKEN:
            try:
                link = ix.telegram_publish(text)
                where.append("Telegram")
            except Exception as e:
                errors.append(f"#{p.id} Telegram: {e}")
        where.append("Blog")
        p.published_on = ", ".join(where)
        p.link = link or f"{config.PUBLIC_BASE_URL}/blog/{p.id}"
        p.status, p.published_at = "Published", utcnow()
        db.commit()
        out.append(f"#{p.id} '{p.title}' -> {p.published_on} ({p.link})")
    if errors and not any("Telegram" in o for o in out):
        log(db, "echo", "; ".join(errors), "error")
    return "Published:\n" + "\n".join(out) + (("\nWarnings: " + "; ".join(errors)) if errors else ""), \
        f"{config.PUBLIC_BASE_URL}/blog"


# ======================= HAVEN (Support) =======================
def haven(db, task):
    tickets = db.query(Ticket).filter(Ticket.status == "Open").order_by(Ticket.id).limit(4).all()
    if not tickets:
        return "Support inbox is empty. No open tickets.", ""
    out = []
    for tk in tickets:
        try:
            tri = llm.ask_json(system_prompt("haven"),
                               f"Ticket from {tk.customer_name} <{tk.email}>\nSubject: {tk.subject}\nMessage: {tk.message}\n\n"
                               'Classify. JSON: {"category":"Sales question|Pricing|Technical|Complaint|Refund|Partnership|Other",'
                               '"priority":"Low|Medium|High|Urgent","escalate":true/false,"reason":"..."}  '
                               "Escalate only for refunds, legal threats, angry complaints or anything you cannot answer safely.")
        except Exception as e:
            raise Blocked(f"LLM unavailable for ticket triage: {e}")
        if not isinstance(tri, dict):
            tri = {}
        tk.category = str(tri.get("category", "Other"))[:50]
        tk.priority = tri.get("priority") if tri.get("priority") in ("Low", "Medium", "High", "Urgent") else "Medium"
        if tri.get("escalate"):
            tk.status, tk.escalation_reason = "Escalated", str(tri.get("reason", ""))[:500]
            db.commit()
            log(db, "haven", f"Ticket #{tk.id} escalated: {tk.escalation_reason}", "flag")
            out.append(f"#{tk.id} escalated to Boss ({tk.escalation_reason})")
            continue
        try:
            res = llm.council(f"Customer {tk.customer_name} wrote:\nSubject: {tk.subject}\n{tk.message}\n\n"
                              "Write the reply email body (max 150 words), warm and helpful, signed 'Haven, LocalLift AI "
                              "Support'. Our offer: Growth Starter Pack EUR 99/month (lead audit, 3 posts/week, review "
                              "replies), cancel anytime. Do not promise anything else.", system_prompt("haven"))
        except Exception as e:
            raise Blocked(f"LLM unavailable for ticket reply: {e}")
        tk.reply = res["final"]
        task.council_json = json.dumps(res)[:60000]
        sent = "reply saved"
        if config.APPS_SCRIPT_URL and tk.email:
            try:
                ix.send_email(tk.email, f"Re: {tk.subject or 'Your message to LocalLift AI'}",
                              "<p>" + tk.reply.replace("\n", "<br>") + "</p>")
                sent = "reply emailed"
            except Exception as e:
                sent = f"reply saved (email failed: {str(e)[:80]})"
        tk.status, tk.resolved_at = "Resolved", utcnow()
        db.commit()
        out.append(f"#{tk.id} [{tk.category}/{tk.priority}] {sent}")
    return "Tickets handled:\n" + "\n".join(out), f"{config.PUBLIC_BASE_URL}/boss/tickets"


# ======================= LENS (Analyst) =======================
def lens(db, task):
    s = metrics.agency_summary(db, "daily")
    lines = [f"Daily KPI report {local_today().isoformat()}",
             f"Tasks completed: {s['tasks_completed']} | in progress: {s['in_progress']} | queued: {s['queued']} | blocked: {s['blocked']}",
             f"Leads found: {s['leads']} | outreach drafts: {s['drafts_ready']}",
             f"Posts published: {s['posts_published']} {s['posts_by_platform']}",
             f"Tickets: received {s['tickets']['received']}, resolved {s['tickets']['resolved']}, escalated {s['tickets']['escalated']}"]
    for e in s["employees"]:
        lines.append(f"- {e['name']}: {e['completed']} done, {e['pending']} pending, {e['kpi_label']}: {e['kpi_value']}")
    report = "\n".join(lines)
    try:
        models = (["jarvis/" + "default"] if config.JARVIS_BASE_URL else []) + config.LLM_MODELS
        insight = llm.complete([{"role": "system", "content": system_prompt("lens")},
                                {"role": "user", "content": report + "\n\nGive 3 short bullet insights and 1 bottleneck "
                                                                     "to fix, based ONLY on these numbers."}],
                               models=models, max_tokens=400)[0]
        report += "\n\nInsights:\n" + insight
    except Exception:
        report += "\n\n(Insights skipped: LLM unavailable.)"
    db.add(Report(kind="daily", for_date=local_today().isoformat(), content=report))
    db.commit()
    return report, f"{config.PUBLIC_BASE_URL}/boss/reports"


# ======================= Generic deliverable =======================
def generic(db, task):
    key = task.agent
    q = f"Task from the Boss:\nTITLE: {task.title}\nDETAILS: {task.description}\n\nProduce the deliverable now."
    try:
        if key in ("closer", "haven", "quill"):  # client-facing -> council
            res = llm.council(q, system_prompt(key))
            task.council_json = json.dumps(res)[:60000]
            return res["final"], ""
        return llm.ask(system_prompt(key), q), ""
    except Exception as e:
        raise Blocked(f"LLM unavailable: {e}")


HANDLERS = {"atlas": atlas, "scout": scout, "closer": closer, "quill": quill, "sentinel": sentinel,
            "echo": echo, "haven": haven, "lens": lens}


def handler_for(task):
    # Boss free-form tasks for some roles are better served by the generic deliverable writer
    if not task.routine_key and task.agent in ("lens", "sentinel", "echo", "haven", "closer"):
        txt = (task.title + " " + (task.description or "")).lower()
        routine_words = {"lens": ["report", "kpi"], "sentinel": ["audit", "qa", "check"], "echo": ["publish"],
                         "haven": ["ticket", "inbox"], "closer": ["lead", "qualif", "outreach"]}
        if not any(w in txt for w in routine_words[task.agent]):
            return generic
    return HANDLERS.get(task.agent, generic)
