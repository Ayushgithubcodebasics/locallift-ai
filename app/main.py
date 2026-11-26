"""LocalLift AI — public website + Boss Command Center + cron API."""
import hmac
import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, metrics, chat, exports, llm, engine
from .bridges import integrations as ix
from .db import (SessionLocal, init_db, Task, Lead, Post, Ticket, ChatMessage, Activity, Report, fmt, utcnow,
                 local_to_utc, TZ, STATUSES, PRIORITIES, day_start_utc, local_today, log)
from .team import TEAM, BY_KEY, KEYS, label

app = FastAPI(title="LocalLift AI")
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 7, https_only=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals.update(fmt=fmt, BY_KEY=BY_KEY, TEAM=TEAM, config=config, label=label)


@app.on_event("startup")
def startup():
    init_db()
    engine.start_background_loop()


def render(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def is_boss(request):
    return request.session.get("boss") is True


def guard(request):
    if not is_boss(request):
        return RedirectResponse("/login", status_code=303)
    return None


# ======================= Public site =======================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = SessionLocal()
    try:
        posts = db.query(Post).filter(Post.status == "Published").order_by(Post.published_at.desc()).limit(3).all()
        return render(request, "public_home.html", posts=posts, sent=request.query_params.get("sent"))
    finally:
        db.close()


@app.post("/contact")
def contact(name: str = Form(...), email: str = Form(...), subject: str = Form(""), message: str = Form(...)):
    db = SessionLocal()
    try:
        tk = Ticket(customer_name=name[:190], email=email[:190], subject=subject[:290] or "Website enquiry",
                    message=message[:5000])
        db.add(tk)
        db.commit()
        t = Task(assigned_by="Website (contact form)", agent="haven", title=f"Answer ticket #{tk.id}: {tk.subject}"[:290],
                 description=f"New ticket from {name}.", priority="High")
        db.add(t)
        db.commit()
        tk.task_id = t.id
        db.commit()
        ix.sheet_upsert_async(t.id)
        engine.process_task_later(t.id, 5)
        log(db, "haven", f"New ticket #{tk.id} from website")
    finally:
        db.close()
    return RedirectResponse("/?sent=1#contact", status_code=303)


@app.get("/blog", response_class=HTMLResponse)
def blog(request: Request):
    db = SessionLocal()
    try:
        posts = db.query(Post).filter(Post.status == "Published").order_by(Post.published_at.desc()).limit(50).all()
        return render(request, "public_blog.html", posts=posts, single=None)
    finally:
        db.close()


@app.get("/blog/{pid}", response_class=HTMLResponse)
def blog_post(request: Request, pid: int):
    db = SessionLocal()
    try:
        p = db.get(Post, pid)
        if not p or p.status != "Published":
            return RedirectResponse("/blog")
        return render(request, "public_blog.html", posts=[p], single=p)
    finally:
        db.close()


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": utcnow().isoformat()}


# ======================= Auth =======================
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if hmac.compare_digest(username, config.BOSS_USERNAME) and hmac.compare_digest(password, config.BOSS_PASSWORD):
        request.session["boss"] = True
        return RedirectResponse("/boss", status_code=303)
    return render(request, "login.html", error="Wrong username or password")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ======================= Boss Command Center =======================
@app.get("/boss", response_class=HTMLResponse)
def boss_home(request: Request):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        return render(request, "boss_home.html", active="home",
                      summary=metrics.agency_summary(db, "daily"), counts=metrics.status_counts(db),
                      activity=db.query(Activity).order_by(Activity.id.desc()).limit(25).all(),
                      integrations=config.integration_status())
    finally:
        db.close()


@app.get("/boss/employees", response_class=HTMLResponse)
def boss_employees(request: Request):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        return render(request, "boss_employees.html", active="employees", stats=metrics.all_employee_stats(db))
    finally:
        db.close()


@app.get("/boss/board", response_class=HTMLResponse)
def boss_board(request: Request, agent: str = "", status: str = "", priority: str = "", day: str = ""):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        q = db.query(Task)
        if agent:
            q = q.filter(Task.agent == agent)
        if status:
            q = q.filter(Task.status == status)
        if priority:
            q = q.filter(Task.priority == priority)
        if day:
            try:
                d = datetime.strptime(day, "%Y-%m-%d").date()
                q = q.filter(Task.created_at >= day_start_utc(d), Task.created_at < day_start_utc(d + timedelta(days=1)))
            except ValueError:
                pass
        tasks = q.order_by(Task.id.desc()).limit(300).all()
        columns = {s: [t for t in tasks if t.status == s] for s in STATUSES}
        return render(request, "boss_board.html", active="board", tasks=tasks, columns=columns,
                      f={"agent": agent, "status": status, "priority": priority, "day": day},
                      STATUSES=STATUSES, PRIORITIES=PRIORITIES, view=request.query_params.get("view", "kanban"))
    finally:
        db.close()


@app.get("/boss/tasks/{tid}", response_class=HTMLResponse)
def boss_task(request: Request, tid: int):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        t = db.get(Task, tid)
        if not t:
            return RedirectResponse("/boss/board")
        council = None
        if t.council_json:
            try:
                council = json.loads(t.council_json)
            except Exception:
                council = None
        children = db.query(Task).filter(Task.parent_id == t.id).all()
        return render(request, "boss_task.html", active="board", t=t, council=council, children=children)
    finally:
        db.close()


@app.post("/boss/tasks/{tid}/retry")
def boss_task_retry(request: Request, tid: int):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        t = db.get(Task, tid)
        if t and t.status in ("Blocked", "Completed"):
            t.status, t.blocked_reason, t.started_at, t.completed_at = "Queued", "", None, None
            db.commit()
            ix.sheet_upsert_async(t.id)
            engine.process_task_later(t.id, 3)
    finally:
        db.close()
    return RedirectResponse(f"/boss/tasks/{tid}", status_code=303)


@app.get("/boss/assign", response_class=HTMLResponse)
def assign_page(request: Request):
    if (r := guard(request)):
        return r
    return render(request, "boss_assign.html", active="assign", PRIORITIES=PRIORITIES,
                  preset=request.query_params.get("agent", "atlas"))


@app.post("/boss/assign")
def assign(request: Request, title: str = Form(...), description: str = Form(""), agent: str = Form(...),
           priority: str = Form("Medium"), deadline: str = Form("")):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        dl = None
        if deadline:
            try:
                dl = local_to_utc(datetime.strptime(deadline, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ))
            except ValueError:
                dl = None
        t = Task(assigned_by="Boss", agent=agent if agent in KEYS else "atlas", title=title[:290],
                 description=description[:5000], priority=priority if priority in PRIORITIES else "Medium", deadline=dl)
        db.add(t)
        db.commit()
        ix.sheet_upsert_async(t.id)
        engine.process_task_later(t.id, 6)
        log(db, t.agent, f"Boss assigned {t.code}: {t.title}")
        return RedirectResponse(f"/boss/tasks/{t.id}", status_code=303)
    finally:
        db.close()


@app.get("/boss/reports", response_class=HTMLResponse)
def boss_reports(request: Request):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        posts = db.query(Post).filter(Post.status == "Published").all()
        tick = {s: db.query(Ticket).filter(Ticket.status == s).count() for s in ("Open", "Resolved", "Escalated")}
        return render(request, "boss_reports.html", active="reports",
                      daily=metrics.agency_summary(db, "daily"), weekly=metrics.agency_summary(db, "weekly"),
                      series=metrics.daily_series(db, 7), platforms=metrics.platform_counts(posts), tickets=tick,
                      leads=metrics.leads_today(db), posts_today=metrics.posts_published_today(db),
                      last_report=db.query(Report).filter(Report.kind == "daily").order_by(Report.id.desc()).first(),
                      weekly_stats=metrics.all_employee_stats(db, day_start_utc(local_today() - timedelta(days=6))))
    finally:
        db.close()


@app.get("/boss/reports/export.csv")
def export_csv(request: Request, kind: str = "tasks"):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        data = exports.csv_export(db, kind)
    finally:
        db.close()
    return Response(data, media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=locallift_{kind}_{local_today()}.csv"})


@app.get("/boss/reports/export.pdf")
def export_pdf(request: Request, period: str = "daily"):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        data = exports.pdf_export(db, "weekly" if period == "weekly" else "daily")
    finally:
        db.close()
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=locallift_{period}_report_{local_today()}.pdf"})


@app.get("/boss/chat", response_class=HTMLResponse)
@app.get("/boss/chat/{thread}", response_class=HTMLResponse)
def chat_page(request: Request, thread: str = "atlas"):
    if (r := guard(request)):
        return r
    thread = thread if thread in KEYS else "atlas"
    db = SessionLocal()
    try:
        msgs = db.query(ChatMessage).filter(ChatMessage.thread == thread).order_by(ChatMessage.id).limit(300).all()
        return render(request, "boss_chat.html", active="chat", thread=thread, msgs=msgs)
    finally:
        db.close()


@app.post("/boss/chat/{thread}")
async def chat_send(request: Request, thread: str):
    if not is_boss(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    form = await request.form()
    msg = (form.get("message") or "").strip()
    if not msg:
        return JSONResponse({"reply": ""})
    db = SessionLocal()
    try:
        from starlette.concurrency import run_in_threadpool
        reply = await run_in_threadpool(chat.handle, db, thread if thread in KEYS else "atlas", msg)
    finally:
        db.close()
    return JSONResponse({"reply": reply})


@app.get("/boss/leads", response_class=HTMLResponse)
def boss_leads(request: Request):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        return render(request, "boss_list.html", active="leads", kind="leads",
                      rows=db.query(Lead).order_by(Lead.id.desc()).limit(300).all())
    finally:
        db.close()


@app.get("/boss/posts", response_class=HTMLResponse)
def boss_posts(request: Request):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        return render(request, "boss_list.html", active="posts", kind="posts",
                      rows=db.query(Post).order_by(Post.id.desc()).limit(200).all())
    finally:
        db.close()


@app.get("/boss/tickets", response_class=HTMLResponse)
def boss_tickets(request: Request):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        return render(request, "boss_list.html", active="tickets", kind="tickets",
                      rows=db.query(Ticket).order_by(Ticket.id.desc()).limit(200).all())
    finally:
        db.close()


@app.get("/boss/system", response_class=HTMLResponse)
def boss_system(request: Request, test: str = ""):
    if (r := guard(request)):
        return r
    llm_health = llm.health() if test == "llm" else None
    sheet = None
    if test == "sheet":
        try:
            ix._apps_script({"action": "ping"})
            sheet = "OK — Apps Script reachable"
        except Exception as e:
            sheet = f"FAIL — {e}"
    return render(request, "boss_system.html", active="system", integrations=config.integration_status(),
                  llm_health=llm_health, sheet=sheet, stats=llm.STATS, router="litellm" if llm._litellm else "direct-http")


@app.post("/boss/run-now")
def run_now(request: Request, background: BackgroundTasks):
    if (r := guard(request)):
        return r
    background.add_task(engine.tick)
    return RedirectResponse("/boss/board", status_code=303)


@app.post("/boss/send-email-now")
def send_email_now(request: Request):
    if (r := guard(request)):
        return r
    db = SessionLocal()
    try:
        res = engine.maybe_send_daily_email(db, force=True)
    finally:
        db.close()
    return RedirectResponse(f"/boss/system?email={res}", status_code=303)


# ======================= Cron API (GitHub Actions heartbeat) =======================
def _cron_ok(key):
    return hmac.compare_digest(key or "", config.CRON_SECRET)


@app.get("/api/cron/tick")
@app.post("/api/cron/tick")
def cron_tick(key: str = ""):
    if not _cron_ok(key):
        return JSONResponse({"error": "bad key"}, status_code=403)
    engine.tick_async()
    return {"ok": True, "started": utcnow().isoformat()}


@app.get("/api/cron/daily-email")
def cron_daily(key: str = "", force: int = 0):
    if not _cron_ok(key):
        return JSONResponse({"error": "bad key"}, status_code=403)
    db = SessionLocal()
    try:
        return {"result": engine.maybe_send_daily_email(db, force=bool(force))}
    finally:
        db.close()


@app.get("/api/status")
def api_status(request: Request):
    if not is_boss(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    db = SessionLocal()
    try:
        return {"counts": metrics.status_counts(db),
                "tasks": [{"id": t.code, "agent": t.agent, "title": t.title, "status": t.status}
                          for t in db.query(Task).order_by(Task.id.desc()).limit(50)]}
    finally:
        db.close()
