"""All settings come from environment variables (Render -> Environment). Never commit keys."""
import os


def env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


# --- Core ---
DATABASE_URL = env("DATABASE_URL", "sqlite:///./locallift.db")
SECRET_KEY = env("SECRET_KEY", "dev-only-change-me")
BOSS_USERNAME = env("BOSS_USERNAME", "boss")
BOSS_PASSWORD = env("BOSS_PASSWORD", "locallift2026")
CRON_SECRET = env("CRON_SECRET", "dev-cron")
TIMEZONE = env("TIMEZONE", "Europe/Riga")
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
AGENCY_NAME = env("AGENCY_NAME", "LocalLift AI")
DAILY_EMAIL_HOUR = int(env("DAILY_EMAIL_HOUR", "8"))  # local time
BACKGROUND_LOOP_SECONDS = int(env("BACKGROUND_LOOP_SECONDS", "120"))
TASKS_PER_TICK = int(env("TASKS_PER_TICK", "3"))

# --- Google Sheet + Email (Apps Script web app) ---
APPS_SCRIPT_URL = env("APPS_SCRIPT_URL")
APPS_SCRIPT_SECRET = env("APPS_SCRIPT_SECRET", "change-me")
BOSS_EMAIL = env("BOSS_EMAIL")
SHEET_URL = env("SHEET_URL")

# --- LLMs (LiteLLM router -> Gemini / OpenRouter / Groq / OpenJarvis) ---
GEMINI_API_KEY = env("GEMINI_API_KEY")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY")
GROQ_API_KEY = env("GROQ_API_KEY")
JARVIS_BASE_URL = env("JARVIS_BASE_URL")  # e.g. http://your-vm:8000/v1 (OpenJarvis `jarvis serve`)
USE_LITELLM = env("USE_LITELLM", "1") == "1"

LLM_MODELS = [m.strip() for m in env(
    "LLM_MODELS",
    "gemini/gemini-2.5-flash,gemini/gemini-2.5-flash-lite,openrouter/meta-llama/llama-3.3-70b-instruct:free",
).split(",") if m.strip()]
COUNCIL_MODELS = [m.strip() for m in env(
    "COUNCIL_MODELS",
    "gemini/gemini-2.5-flash,openrouter/meta-llama/llama-3.3-70b-instruct:free,gemini/gemini-2.5-flash-lite",
).split(",") if m.strip()]
CHAIRMAN_MODEL = env("CHAIRMAN_MODEL", "gemini/gemini-2.5-flash")

# --- Apify (Google Maps lead scraping) ---
APIFY_TOKEN = env("APIFY_TOKEN")
APIFY_ACTOR = env("APIFY_ACTOR", "compass~crawler-google-places")
APIFY_MAX_PLACES = int(env("APIFY_MAX_PLACES", "8"))
LEAD_QUERIES = [q.strip() for q in env(
    "LEAD_QUERIES",
    "cafes in Riga Latvia;hair salons in Riga Latvia;dental clinics in Riga Latvia;bakeries in Riga Latvia;"
    "beauty salons in Riga Latvia;fitness studios in Riga Latvia;florists in Riga Latvia",
).split(";") if q.strip()]

# --- Telegram (publishing channel) ---
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL = env("TELEGRAM_CHANNEL", "@locallift_news")


def integration_status() -> dict:
    return {
        "Database": "Postgres (Neon)" if DATABASE_URL.startswith("postgres") else "SQLite (local only)",
        "Google Sheet + Email (Apps Script)": bool(APPS_SCRIPT_URL),
        "Boss email": bool(BOSS_EMAIL),
        "Gemini": bool(GEMINI_API_KEY),
        "OpenRouter": bool(OPENROUTER_API_KEY),
        "Groq": bool(GROQ_API_KEY),
        "OpenJarvis": bool(JARVIS_BASE_URL),
        "Apify": bool(APIFY_TOKEN),
        "Telegram": bool(TELEGRAM_BOT_TOKEN),
    }
