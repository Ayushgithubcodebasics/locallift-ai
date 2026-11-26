"""All numbers shown on the dashboard, in chat, in the PDF/CSV export and in the daily email come from here,
so every channel always agrees with the ledger."""
from datetime import timedelta

from sqlalchemy import func

from .db import (Task, Lead, Post, Ticket, Activity, Report, day_start_utc, week_start_utc, local_today, utcnow, fmt)
from .team import TEAM, BY_KEY


def status_counts(db, since=None):
    q = db.query(Task.status, func.count(Task.id))
    if since:
        q = q.filter(Task.created_at >= since)
    d = {s: 0 for s in ["Queued", "In Progress", "Blocked", "Completed"]}
    d.update(dict(q.group_by(Task.status).all()))
    return d


def completed_between(db, start, end=None, agent=None):
    q = db.query(Task).filter(Task.status == "Completed", Task.completed_at >= start)
    if end:
        q = q.filter(Task.completed_at < end)
    if agent:
        q = q.filter(Task.agent == agent)
    return q


def leads_today(db):
    return db.query(Lead).filter(Lead.created_at >= day_start_utc()).order_by(Lead.score.desc(), Lead.id).all()


def posts_published_today(db):
    return db.query(Post).filter(Post.status == "Published", Post.published_at >= day_start_utc()) \
        .order_by(Post.published_at).all()


def platform_counts(posts):
    c = {}
    for p in posts:
        for plat in [x.strip() for x in (p.published_on or "").split(",") if x.strip()]:
            c[plat] = c.get(plat, 0) + 1
    return c


def tickets_stats(db, since):
    q = db.query(Ticket)
    return {
        "received": q.filter(Ticket.created_at >= since).count(),
        "resolved": q.filter(Ticket.status == "Resolved", Ticket.resolved_at >= since).count(),
        "escalated": db.query(Ticket).filter(Ticket.status == "Escalated", Ticket.created_at >= since).count(),
        "open": db.query(Ticket).filter(Ticket.status == "Open").count(),
    }


def kpi_for(db, key, since):
    """Agency-specific KPI per employee: (label, value)."""
    if key == "atlas":
        v = db.query(Task).filter(Task.assigned_by.like("Atlas%"), Task.created_at >= since).count()
        return "Tasks delegated", v
    if key == "scout":
        return "Leads found", db.query(Lead).filter(Lead.created_at >= since).count()
    if key == "closer":
        return "Outreach drafts ready", db.query(Lead).filter(Lead.status == "Draft Ready", Lead.created_at >= since).count()
    if key == "quill":
        return "Posts drafted", db.query(Post).filter(Post.created_at >= since).count()
    if key == "echo":
        return "Posts published", db.query(Post).filter(Post.status == "Published", Post.published_at >= since).count()
    if key == "haven":
        return "Tickets resolved", db.query(Ticket).filter(Ticket.status == "Resolved", Ticket.resolved_at >= since).count()
    if key == "lens":
        return "Reports generated", db.query(Report).filter(Report.kind == "daily", Report.created_at >= since).count()
    if key == "sentinel":
        return "Issues flagged", db.query(Activity).filter(Activity.agent == "sentinel", Activity.level == "flag",
                                                           Activity.created_at >= since).count()
    return "-", 0


def employee_stats(db, key, since=None):
    since = since or day_start_utc()
    done = completed_between(db, since, agent=key).all()
    durations = [(t.completed_at - t.started_at).total_seconds() / 60 for t in done if t.started_at and t.completed_at]
    current = db.query(Task).filter(Task.agent == key, Task.status == "In Progress").order_by(Task.started_at.desc()).first()
    nxt = None if current else db.query(Task).filter(Task.agent == key, Task.status == "Queued").order_by(Task.id).first()
    pending = db.query(Task).filter(Task.agent == key, Task.status.in_(["Queued", "In Progress", "Blocked"])).count()
    blocked = db.query(Task).filter(Task.agent == key, Task.status == "Blocked").count()
    kpi_label, kpi_val = kpi_for(db, key, since)
    return {
        "key": key, "name": BY_KEY[key]["name"], "title": BY_KEY[key]["title"], "emoji": BY_KEY[key]["emoji"],
        "completed": len(done), "pending": pending, "blocked": blocked,
        "avg_minutes": round(sum(durations) / len(durations), 1) if durations else None,
        "kpi_label": kpi_label, "kpi_value": kpi_val,
        "current": current, "next": nxt, "active": current is not None,
        "total_completed": db.query(Task).filter(Task.agent == key, Task.status == "Completed").count(),
    }


def all_employee_stats(db, since=None):
    return [employee_stats(db, e["key"], since) for e in TEAM]


def top_employee_week(db):
    rows = db.query(Task.agent, func.count(Task.id)).filter(Task.status == "Completed",
                                                             Task.completed_at >= week_start_utc()) \
        .group_by(Task.agent).order_by(func.count(Task.id).desc()).all()
    return rows


def daily_series(db, days=7):
    """Completed tasks, leads and published posts per local day, oldest first."""
    today = local_today()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        s, e = day_start_utc(d), day_start_utc(d + timedelta(days=1))
        out.append({
            "date": d.isoformat(),
            "completed": db.query(Task).filter(Task.status == "Completed", Task.completed_at >= s, Task.completed_at < e).count(),
            "leads": db.query(Lead).filter(Lead.created_at >= s, Lead.created_at < e).count(),
            "posts": db.query(Post).filter(Post.status == "Published", Post.published_at >= s, Post.published_at < e).count(),
            "tickets": db.query(Ticket).filter(Ticket.created_at >= s, Ticket.created_at < e).count(),
        })
    return out


def agency_summary(db, period="daily"):
    since = day_start_utc() if period == "daily" else day_start_utc(local_today() - timedelta(days=6))
    tasks_created = db.query(Task).filter(Task.created_at >= since).count()
    completed = completed_between(db, since).count()
    now_counts = status_counts(db)
    posts = db.query(Post).filter(Post.status == "Published", Post.published_at >= since).all()
    return {
        "period": period, "since": fmt(since), "generated": fmt(utcnow()),
        "tasks_created": tasks_created, "tasks_completed": completed,
        "in_progress": now_counts["In Progress"], "queued": now_counts["Queued"], "blocked": now_counts["Blocked"],
        "leads": db.query(Lead).filter(Lead.created_at >= since).count(),
        "drafts_ready": db.query(Lead).filter(Lead.status == "Draft Ready", Lead.created_at >= since).count(),
        "posts_published": len(posts), "posts_by_platform": platform_counts(posts),
        "tickets": tickets_stats(db, since),
        "employees": all_employee_stats(db, since),
    }
