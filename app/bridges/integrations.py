"""Scripted automation bridges: Google Sheet ledger + Email (Apps Script), Apify, Telegram."""
import threading

import requests

from .. import config
from ..db import fmt
from ..team import BY_KEY


# ---------------- Google Sheet + Gmail via Apps Script ----------------
def _apps_script(payload: dict, timeout=30) -> dict:
    if not config.APPS_SCRIPT_URL:
        raise RuntimeError("APPS_SCRIPT_URL not configured")
    payload = {"secret": config.APPS_SCRIPT_SECRET, **payload}
    r = requests.post(config.APPS_SCRIPT_URL, json=payload, timeout=timeout, allow_redirects=True)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Apps Script returned non-JSON (HTTP {r.status_code}): {r.text[:150]}")
    if not data.get("ok"):
        raise RuntimeError(f"Apps Script error: {data.get('error')}")
    return data


def task_row(t) -> dict:
    out = t.output_link or (t.output or "")[:300]
    if t.status == "Blocked" and t.blocked_reason:
        out = "BLOCKED: " + t.blocked_reason[:280]
    return {
        "task_id": t.code,
        "timestamp": fmt(t.created_at),
        "assigned_by": t.assigned_by,
        "agent": BY_KEY[t.agent]["name"] + " - " + BY_KEY[t.agent]["title"] if t.agent in BY_KEY else t.agent,
        "description": t.title + (f" — {t.description[:200]}" if t.description else ""),
        "priority": t.priority,
        "status": t.status,
        "output": out,
        "completion_time": fmt(t.completed_at),
    }


def sheet_upsert(task) -> bool:
    if not config.APPS_SCRIPT_URL:
        return False
    try:
        _apps_script({"action": "upsert", "row": task_row(task)})
        return True
    except Exception:
        return False


def sheet_upsert_async(task_id: int):
    """Sync one task row in the background and mark it synced."""
    def run():
        from ..db import SessionLocal, Task
        db = SessionLocal()
        try:
            t = db.get(Task, task_id)
            if t:
                t.sheet_synced = sheet_upsert(t)
                db.commit()
        finally:
            db.close()
    threading.Thread(target=run, daemon=True).start()


def send_email(to: str, subject: str, html: str) -> None:
    _apps_script({"action": "email", "to": to, "subject": subject, "html": html, "name": config.AGENCY_NAME},
                 timeout=45)


# ---------------- Apify: Google Maps scraper ----------------
def apify_google_maps(query: str, max_places: int = None) -> list:
    if not config.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN not configured")
    url = (f"https://api.apify.com/v2/acts/{config.APIFY_ACTOR}/run-sync-get-dataset-items"
           f"?token={config.APIFY_TOKEN}&timeout=240")
    body = {
        "searchStringsArray": [query],
        "maxCrawledPlacesPerSearch": max_places or config.APIFY_MAX_PLACES,
        "language": "en",
        "skipClosedPlaces": True,
        "scrapePlaceDetailPage": False,
    }
    r = requests.post(url, json=body, timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f"Apify HTTP {r.status_code}: {r.text[:200]}")
    items = r.json()
    if not isinstance(items, list):
        raise RuntimeError(f"Apify unexpected response: {str(items)[:200]}")
    return items


# ---------------- Telegram ----------------
def telegram_publish(text: str) -> str:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage", timeout=30, json={
        "chat_id": config.TELEGRAM_CHANNEL, "text": text[:4000], "disable_web_page_preview": True})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data.get('description')}")
    msg_id = data["result"]["message_id"]
    chat = data["result"]["chat"]
    username = chat.get("username") or config.TELEGRAM_CHANNEL.lstrip("@")
    return f"https://t.me/{username}/{msg_id}"
