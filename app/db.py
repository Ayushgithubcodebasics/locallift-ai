"""Database models (single source of truth). The Google Sheet is a mirror of the tasks table."""
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from sqlalchemy import (create_engine, Column, Integer, String, Text, DateTime, Boolean, Float)
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

TZ = ZoneInfo(config.TIMEZONE)


def _db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_url = _db_url(config.DATABASE_URL)
_kwargs = {"pool_pre_ping": True}
if _url.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
else:
    _kwargs.update(pool_size=5, max_overflow=5, pool_recycle=280)
engine = create_engine(_url, **_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

STATUSES = ["Queued", "In Progress", "Blocked", "Completed"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]


# ---------------- time helpers ----------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ)


def fmt(dt, with_time=True) -> str:
    if dt is None:
        return ""
    return to_local(dt).strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def local_today() -> date:
    return datetime.now(TZ).date()


def day_start_utc(d: date = None) -> datetime:
    d = d or local_today()
    return datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)


def week_start_utc() -> datetime:
    d = local_today()
    return day_start_utc(d - timedelta(days=d.weekday()))


def local_to_utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=TZ)
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


# ---------------- models ----------------
class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    assigned_by = Column(String(80), default="Boss")
    agent = Column(String(30), index=True)
    title = Column(String(300))
    description = Column(Text, default="")
    priority = Column(String(10), default="Medium")
    deadline = Column(DateTime, nullable=True)
    status = Column(String(20), default="Queued", index=True)
    output = Column(Text, default="")
    output_link = Column(String(500), default="")
    blocked_reason = Column(Text, default="")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    routine_key = Column(String(120), nullable=True, index=True)
    parent_id = Column(Integer, nullable=True)
    council_json = Column(Text, default="")
    sheet_synced = Column(Boolean, default=False)

    @property
    def code(self):
        return f"T-{self.id:04d}"


class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    name = Column(String(300))
    category = Column(String(200), default="")
    address = Column(String(400), default="")
    phone = Column(String(80), default="")
    website = Column(String(400), default="")
    rating = Column(Float, nullable=True)
    reviews = Column(Integer, nullable=True)
    maps_url = Column(String(600), default="")
    search_query = Column(String(300), default="")
    source = Column(String(80), default="Google Maps via Apify")
    status = Column(String(30), default="New", index=True)  # New / Draft Ready / Low Fit
    score = Column(Integer, default=0)
    outreach_draft = Column(Text, default="")
    task_id = Column(Integer, nullable=True)


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    platform = Column(String(40), default="LinkedIn")  # intended platform
    title = Column(String(300), default="")
    body = Column(Text, default="")
    status = Column(String(20), default="Draft", index=True)  # Draft / Approved / Rejected / Published
    qa_note = Column(Text, default="")
    published_on = Column(String(120), default="")  # e.g. "Telegram, Blog"
    published_at = Column(DateTime, nullable=True, index=True)
    link = Column(String(500), default="")
    task_id = Column(Integer, nullable=True)


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    customer_name = Column(String(200), default="")
    email = Column(String(200), default="")
    subject = Column(String(300), default="")
    message = Column(Text, default="")
    category = Column(String(60), default="")
    priority = Column(String(10), default="Medium")
    status = Column(String(20), default="Open", index=True)  # Open / Resolved / Escalated
    reply = Column(Text, default="")
    escalation_reason = Column(Text, default="")
    resolved_at = Column(DateTime, nullable=True)
    task_id = Column(Integer, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    thread = Column(String(30), index=True)  # agent key
    role = Column(String(10))  # boss / agent
    content = Column(Text)


class Activity(Base):
    __tablename__ = "activity"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    agent = Column(String(30), index=True)
    level = Column(String(10), default="info")  # info / flag / error
    message = Column(Text)


class Report(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    kind = Column(String(20))  # daily / weekly / email
    for_date = Column(String(20), index=True)
    content = Column(Text, default="")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(120), primary_key=True)
    value = Column(Text, default="")


def init_db():
    Base.metadata.create_all(engine)


def get_setting(db, key, default=""):
    s = db.get(Setting, key)
    return s.value if s else default


def set_setting(db, key, value):
    s = db.get(Setting, key)
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def log(db, agent, message, level="info"):
    db.add(Activity(agent=agent, message=message[:2000], level=level))
    db.commit()
