"""Daily status bulletin (HTML) for the Boss."""
from html import escape

from . import config, metrics
from .db import Task, day_start_utc, local_today, fmt, utcnow
from .team import BY_KEY


def _row(cells, head=False):
    tag = "th" if head else "td"
    style = "padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:left;font-size:13px"
    return "<tr>" + "".join(f"<{tag} style='{style}'>{escape(str(c))}</{tag}>" for c in cells) + "</tr>"


def daily_bulletin(db):
    s = metrics.agency_summary(db, "daily")
    since = day_start_utc()
    completed = db.query(Task).filter(Task.status == "Completed", Task.completed_at >= since).order_by(Task.completed_at).all()
    inprog = db.query(Task).filter(Task.status.in_(["In Progress", "Queued"])).order_by(Task.id).all()
    blocked = db.query(Task).filter(Task.status == "Blocked").order_by(Task.id).all()
    # The bulletin goes out in the morning, so also include yesterday's finished work
    from datetime import timedelta
    y = local_today() - timedelta(days=1)
    yesterday = metrics.completed_between(db, day_start_utc(y), since).count()

    subject = (f"[{config.AGENCY_NAME}] Daily brief {local_today().isoformat()} — {s['tasks_completed']} done today, "
               f"{yesterday} yesterday, {s['blocked']} blocked")
    kpi = (f"<table style='border-collapse:collapse;width:100%'>"
           + _row(["Leads today", "Drafts ready", "Posts published", "Tickets in/resolved/escalated", "Blocked"], True)
           + _row([s["leads"], s["drafts_ready"], f"{s['posts_published']} {s['posts_by_platform'] or ''}",
                   f"{s['tickets']['received']}/{s['tickets']['resolved']}/{s['tickets']['escalated']}", s["blocked"]])
           + "</table>")
    emp = ("<table style='border-collapse:collapse;width:100%'>"
           + _row(["Employee", "Done today", "Pending", "KPI"], True)
           + "".join(_row([f"{e['emoji']} {e['name']} — {e['title']}", e["completed"], e["pending"],
                           f"{e['kpi_label']}: {e['kpi_value']}"]) for e in s["employees"]) + "</table>")

    def tlist(tasks, extra=None, empty="None"):
        if not tasks:
            return f"<p style='color:#6b7280'>{empty}</p>"
        return ("<table style='border-collapse:collapse;width:100%'>"
                + _row(["Task", "Employee", "Title", extra or "Status"], True)
                + "".join(_row([t.code, BY_KEY.get(t.agent, {}).get("name", t.agent), t.title[:80],
                                (t.blocked_reason[:120] if extra == "Reason" else t.status)]) for t in tasks[:25])
                + "</table>")

    links = f"<a href='{config.PUBLIC_BASE_URL}/boss'>Open Boss Command Center</a>"
    if config.SHEET_URL:
        links += f" · <a href='{config.SHEET_URL}'>Google Sheet ledger</a>"
    html = f"""<div style='font-family:Arial,sans-serif;max-width:760px'>
<h2 style='margin:0'>🧭 {escape(config.AGENCY_NAME)} — Daily Brief</h2>
<p style='color:#6b7280;margin-top:4px'>Prepared by Atlas (Project Manager) · {fmt(utcnow())} {escape(config.TIMEZONE)}</p>
<h3>Key KPIs (today)</h3>{kpi}
<h3>Per-employee</h3>{emp}
<h3>✅ Completed today ({len(completed)}) · yesterday: {yesterday}</h3>{tlist(completed)}
<h3>⏳ In progress / queued ({len(inprog)})</h3>{tlist(inprog)}
<h3>⛔ Blocked ({len(blocked)})</h3>{tlist(blocked, "Reason")}
<p style='margin-top:20px'>{links}</p></div>"""
    return subject, html
