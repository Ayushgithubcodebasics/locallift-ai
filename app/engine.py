"""The autonomous work loop: Atlas plans the day, employees pick up queued tasks, status moves
Queued -> In Progress -> Completed / Blocked, every change is mirrored to the Google Sheet,
and the daily bulletin is emailed once per day."""
import threading
import time
import traceback
from datetime import datetime

from sqlalchemy import update

from . import config
from .agents import handler_for, Blocked
from .bridges import integrations as ix
from .db import SessionLocal, Task, Report, utcnow, local_today, TZ, get_setting, set_setting, log
from .team import BY_KEY
from . import emails

ROUTINES = [  # (agent, routine, title, priority) in execution order
    ("atlas", "plan", "Plan the day and assign routine work", "High"),
    ("scout", "leads", "Find new local-business leads on Google Maps", "High"),
    ("closer", "qualify", "Qualify new leads and draft outreach", "Medium"),
    ("quill", "posts", "Write today's LinkedIn + Telegram posts", "Medium"),
    ("sentinel", "audit", "QA posts, flag stalled tasks, reconcile Google Sheet", "Medium"),
    ("echo", "publish", "Publish approved posts", "Medium"),
    ("haven", "inbox", "Sweep support inbox and answer tickets", "High"),
    ("lens", "report", "Compile daily KPI report", "Low"),
]

_tick_lock = threading.Lock()


def ensure_daily_plan(db):
    today = local_today().isoformat()
    if db.query(Task).filter(Task.routine_key == f"{today}:atlas:plan").first():
        return False
    for agent, routine, title, prio in ROUTINES:
        key = f"{today}:{agent}:{routine}"
        t = Task(assigned_by="Boss (standing order)" if agent == "atlas" else "Atlas (PM)", agent=agent, title=title,
                 description=f"Daily routine for {today}.", priority=prio, routine_key=key)
        db.add(t)
        db.commit()
        ix.sheet_upsert_async(t.id)
    log(db, "atlas", f"Daily plan for {today} created: {len(ROUTINES)} routine tasks queued.")
    return True


def claim(db, task_id) -> bool:
    res = db.execute(update(Task).where(Task.id == task_id, Task.status == "Queued")
                     .values(status="In Progress", started_at=utcnow(), blocked_reason=""))
    db.commit()
    return res.rowcount == 1


def process_task(task_id: int):
    db = SessionLocal()
    try:
        if not claim(db, task_id):
            return
        task = db.get(Task, task_id)
        db.refresh(task)
        ix.sheet_upsert_async(task.id)
        log(db, task.agent, f"Started {task.code}: {task.title}")
        try:
            output, link = handler_for(task)(db, task)
            task.status, task.output, task.output_link = "Completed", output[:20000], link or ""
            task.completed_at = utcnow()
            log(db, task.agent, f"Completed {task.code}")
        except Blocked as b:
            task.status, task.blocked_reason = "Blocked", str(b)[:1500]
            log(db, task.agent, f"{task.code} blocked: {b}", "flag")
        except Exception as e:  # unexpected bug -> blocked with reason, never crash the loop
            task.status, task.blocked_reason = "Blocked", f"Unexpected error: {e}"[:1500]
            log(db, task.agent, f"{task.code} error: {traceback.format_exc()[-800:]}", "error")
        task.sheet_synced = False
        db.commit()
        task.sheet_synced = ix.sheet_upsert(task)
        db.commit()
    finally:
        db.close()


def process_task_later(task_id: int, delay: float = 4.0):
    """Used right after the Boss assigns a task, so the board visibly moves Queued -> In Progress -> Completed."""
    def run():
        time.sleep(delay)
        process_task(task_id)
    threading.Thread(target=run, daemon=True).start()


def maybe_send_daily_email(db, force=False):
    today = local_today().isoformat()
    if not force:
        if get_setting(db, "daily_email_sent") == today:
            return "already sent"
        if datetime.now(TZ).hour < config.DAILY_EMAIL_HOUR:
            return "too early"
    if not (config.APPS_SCRIPT_URL and config.BOSS_EMAIL):
        return "email not configured"
    subject, html = emails.daily_bulletin(db)
    try:
        ix.send_email(config.BOSS_EMAIL, subject, html)
    except Exception as e:
        log(db, "atlas", f"Daily email failed: {e}", "error")
        return f"failed: {e}"
    set_setting(db, "daily_email_sent", today)
    db.add(Report(kind="email", for_date=today, content=subject))
    db.commit()
    log(db, "atlas", f"Daily status email sent to Boss ({subject}).")
    return "sent"


def tick(max_tasks: int = None):
    """One heartbeat. Safe to call often (GitHub Actions every 10 min + in-app loop)."""
    if not _tick_lock.acquire(blocking=False):
        return {"skipped": "tick already running"}
    result = {"planned": False, "processed": [], "email": None}
    try:
        db = SessionLocal()
        try:
            result["planned"] = ensure_daily_plan(db)
            queued = db.query(Task.id).filter(Task.status == "Queued").order_by(
                Task.routine_key.is_(None).desc(), Task.id).limit(max_tasks or config.TASKS_PER_TICK).all()
        finally:
            db.close()
        for (tid,) in queued:
            process_task(tid)
            result["processed"].append(tid)
        db = SessionLocal()
        try:
            result["email"] = maybe_send_daily_email(db)
        finally:
            db.close()
    finally:
        _tick_lock.release()
    return result


def tick_async():
    threading.Thread(target=tick, daemon=True).start()


def start_background_loop():
    def loop():
        time.sleep(15)
        while True:
            try:
                tick()
            except Exception:
                traceback.print_exc()
            time.sleep(config.BACKGROUND_LOOP_SECONDS)
    threading.Thread(target=loop, daemon=True).start()
