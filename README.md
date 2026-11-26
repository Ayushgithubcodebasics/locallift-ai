# 🚀 LocalLift AI — an Autonomous AI Agency

LocalLift AI is a growth agency for small local businesses in Riga (cafés, salons, clinics) that is run by **8 autonomous AI employees**: a Project Manager plus 7 specialists. The agents plan the day, find real leads on Google Maps, write posts and pick the best version with an LLM Council, publish to Telegram and the blog, answer customer emails, report KPIs and audit each other. Every task is mirrored to a **Google Sheet ledger**, and the Boss gets a **daily email**. The Boss runs the business from the **Boss Command Center**, where they can assign tasks, watch the live board, read reports and chat with the PM or any employee.

| # | Employee | Role | Main tools | KPI |
|---|---|---|---|---|
| 1 | 🧭 Atlas | Project Manager (Chief of Staff) | Ledger, Google Sheet, Gmail, LiteLLM | tasks delegated, daily email |
| 2 | 🔎 Scout | Lead Researcher | Apify Google Maps scraper | leads found / day |
| 3 | 🤝 Closer | Sales & Outbound Strategist | scoring rules, LLM Council | outreach drafts ready |
| 4 | ✍️ Quill | Content Writer | LLM Council | posts drafted |
| 5 | 📣 Echo | Social Media Publisher | Telegram Bot API, blog | posts published |
| 6 | 🛟 Haven | Customer Support | LLM Council, Gmail | tickets resolved / escalated |
| 7 | 📊 Lens | Performance Analyst | SQL, LiteLLM / OpenJarvis | reports generated |
| 8 | 🛡️ Sentinel | QA & Technical Auditor | QA rules, ledger reconciliation | issues flagged |

The full role definitions are in `employees/<name>/ROLE.md`, with install configs in `employees/<name>/.installed.json`. The workflow diagram and agency plan are in `docs/AGENCY_PLAN.md`.

## Architecture

```
GitHub Actions (every 10 min) ──► Render web service (FastAPI)
                                   ├─ Boss Command Center (login, assign, board, reports, chat, employees)
                                   ├─ Public site + contact form + blog
                                   ├─ Engine: Atlas daily plan → employees pick up tasks → status updates
                                   ├─ LiteLLM router → Gemini / OpenRouter free / (Groq) / (OpenJarvis)
                                   ├─ LLM Council (karpathy/llm-council port) for client-facing text
                                   ├─ Apify REST → Google Maps leads
                                   └─ Telegram Bot API → channel posts
Neon Postgres  ◄── single source of truth (tasks, leads, posts, tickets, chat history)
Apps Script web app ◄── Google Sheet ledger mirror + Gmail daily email + customer replies
```

Ledger columns: `Task ID | Timestamp | Assigned By | Agent Name | Task Description | Priority | Status | Output / Result Link | Completion Time`.

## Deploy (all free tiers)

### 1. Google Sheet and email bridge
1. Open your Google Sheet, then go to **Extensions → Apps Script**.
2. Delete the existing code and paste in `apps_script/Code.gs`.
3. Change `SECRET` to a long random string and click **Save**.
4. Select `testSetup` and click **Run**. Allow the permissions it asks for; you'll get a test email.
5. Click **Deploy → New deployment**, choose the type **Web app**, set **Execute as: Me** and **Who has access: Anyone**, then click **Deploy**.
6. Copy the `/exec` URL.

### 2. Render
1. Go to **New → Web Service** and connect this GitHub repo.
2. Set Runtime to **Python 3**, Build to `pip install -r requirements.txt`, Start to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, and Instance type to **Free**.
3. Add the environment variables from `.env.example`. At minimum: `DATABASE_URL`, `SECRET_KEY`, `CRON_SECRET`, `BOSS_USERNAME`, `BOSS_PASSWORD`, `PUBLIC_BASE_URL`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `APIFY_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL`, `APPS_SCRIPT_URL`, `APPS_SCRIPT_SECRET`, `BOSS_EMAIL`, `SHEET_URL`, and `PYTHON_VERSION=3.11.9`.
4. Deploy, then open `https://<your-app>.onrender.com`.

### 3. Heartbeat (makes the agency run 24/7)
1. In the GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Add `APP_URL` (your Render URL, with no trailing slash) and `CRON_SECRET` (the same value as on Render).
3. Go to **Actions**, enable workflows, open **Agency heartbeat** and click **Run workflow** once.

### 4. Check that everything works
1. Log in at `/login` and go to **System**.
2. Use **Test every LLM model** and **Test Google Sheet**.
3. Click **Run one agent heartbeat now**, then watch the **Live Task Board**.
4. Click **Send the daily email now** to get the first email.

## Free-tier notes
- The Render free tier sleeps after ~15 minutes idle. The GitHub heartbeat wakes it every 10 minutes, and all data lives in Neon, so nothing is lost.
- If a model's free quota runs out, LiteLLM falls back to the next model. If all models fail, the task is marked **Blocked** with the reason, and that reason shows on the board, in the Sheet, in the email and in chat.
- Closer never sends cold email (GDPR). It only writes drafts for the Boss to approve.
- If the `litellm` package ever fails to install or uses too much memory, set `USE_LITELLM=0`. The same router then calls the OpenAI-compatible endpoints directly.

## Optional: OpenJarvis
OpenJarvis (Stanford, local-first) can be installed on any VM and started with `jarvis serve --port 8000`, which exposes an OpenAI-compatible API. Set `JARVIS_BASE_URL=http://<vm>:8000/v1` and Lens will use it first for insights.

## Local run
```bash
pip install -r requirements.txt
python scripts/hire_employees.py      # regenerates employees/*/.installed.json
uvicorn app.main:app --reload         # uses SQLite if DATABASE_URL is not set
```

## Test login (for the lecturer)
The credentials are set by the `BOSS_USERNAME` / `BOSS_PASSWORD` environment variables on Render.
