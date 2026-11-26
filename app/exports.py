"""CSV and PDF exports for the Reports section."""
import csv
import io

from . import metrics
from .db import Task, Lead, Post, Ticket, fmt
from .team import BY_KEY


def csv_export(db, kind):
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "leads":
        w.writerow(["ID", "Found", "Name", "Category", "Address", "Phone", "Website", "Rating", "Reviews", "Source",
                    "Status", "Score", "Maps URL"])
        for l in db.query(Lead).order_by(Lead.id):
            w.writerow([l.id, fmt(l.created_at), l.name, l.category, l.address, l.phone, l.website, l.rating, l.reviews,
                        l.source, l.status, l.score, l.maps_url])
    elif kind == "posts":
        w.writerow(["ID", "Created", "Platform", "Title", "Status", "Published on", "Published at", "Link", "Body"])
        for p in db.query(Post).order_by(Post.id):
            w.writerow([p.id, fmt(p.created_at), p.platform, p.title, p.status, p.published_on, fmt(p.published_at),
                        p.link, p.body])
    elif kind == "tickets":
        w.writerow(["ID", "Received", "Customer", "Email", "Subject", "Category", "Priority", "Status", "Resolved",
                    "Escalation reason"])
        for t in db.query(Ticket).order_by(Ticket.id):
            w.writerow([t.id, fmt(t.created_at), t.customer_name, t.email, t.subject, t.category, t.priority, t.status,
                        fmt(t.resolved_at), t.escalation_reason])
    elif kind == "employees":
        w.writerow(["Employee", "Title", "Completed today", "Pending", "Blocked", "Avg completion (min)", "KPI", "KPI value",
                    "Completed all-time"])
        for e in metrics.all_employee_stats(db):
            w.writerow([e["name"], e["title"], e["completed"], e["pending"], e["blocked"], e["avg_minutes"],
                        e["kpi_label"], e["kpi_value"], e["total_completed"]])
    else:
        w.writerow(["Task ID", "Timestamp", "Assigned By", "Agent Name", "Task Description", "Priority", "Status",
                    "Output / Result Link", "Completion Time", "Deadline", "Blocked reason"])
        for t in db.query(Task).order_by(Task.id):
            w.writerow([t.code, fmt(t.created_at), t.assigned_by, BY_KEY.get(t.agent, {}).get("name", t.agent), t.title,
                        t.priority, t.status, t.output_link or (t.output or "")[:300], fmt(t.completed_at),
                        fmt(t.deadline), t.blocked_reason])
    return buf.getvalue()


def _latin(s):
    return str(s).encode("latin-1", "replace").decode("latin-1")


def pdf_export(db, period="daily"):
    from fpdf import FPDF
    s = metrics.agency_summary(db, period)
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin(f"LocalLift AI - {period.title()} Agency Report"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _latin(f"Period since {s['since']} | generated {s['generated']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Agency KPIs", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    rows = [("Tasks created", s["tasks_created"]), ("Tasks completed", s["tasks_completed"]),
            ("In progress / queued / blocked (now)", f"{s['in_progress']} / {s['queued']} / {s['blocked']}"),
            ("Leads generated", s["leads"]), ("Outreach drafts ready", s["drafts_ready"]),
            ("Posts published", f"{s['posts_published']} {s['posts_by_platform']}"),
            ("Tickets received / resolved / escalated",
             f"{s['tickets']['received']} / {s['tickets']['resolved']} / {s['tickets']['escalated']}")]
    for k, v in rows:
        pdf.cell(95, 7, _latin(k), border=1)
        pdf.cell(95, 7, _latin(v), border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Per-employee", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 9)
    widths = [52, 20, 20, 28, 70]
    for h, wdt in zip(["Employee", "Done", "Pending", "Avg min", "KPI"], widths):
        pdf.cell(wdt, 7, h, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for e in s["employees"]:
        vals = [f"{e['name']} ({e['title'][:22]})", e["completed"], e["pending"], e["avg_minutes"] or "-",
                f"{e['kpi_label']}: {e['kpi_value']}"]
        for v, wdt in zip(vals, widths):
            pdf.cell(wdt, 7, _latin(v)[:45], border=1)
        pdf.ln()
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Leads", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    from datetime import timedelta
    from .db import day_start_utc, local_today
    since = day_start_utc() if period == "daily" else day_start_utc(local_today() - timedelta(days=6))
    for l in db.query(Lead).filter(Lead.created_at >= since).order_by(Lead.id).limit(40):
        pdf.multi_cell(0, 5, _latin(f"- {l.name} | {l.category} | {l.phone or '-'} | {l.website or 'no website'} | {l.status}"),
                       new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
