"""Generates employees/<key>/ (ROLE.md, SCHEDULE.md, RELEASES.md, routines/*/SKILL.md, .installed.json)
for the 8 LocalLift employees, following the markfulton/ai-employees folder convention.
The runtime (app/engine.py) uses these same roles.

Run:  python scripts/hire_employees.py
Optional (to also pull the upstream templates with the official installer, needs Node):
      npx ai-employees hire gtm-engineer --to employees/_upstream/gtm-engineer
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.team import TEAM  # noqa: E402

ROUTINES = {
    "atlas": [("plan", "Every day, first heartbeat", "Create the daily plan: one routine task per employee."),
              ("daily-email", f"Every day after 08:00 Europe/Riga", "Email the Boss the daily status bulletin."),
              ("delegate", "On Boss goal", "Break a Boss goal into sub-tasks for the right employees.")],
    "scout": [("leads", "Daily", "Run the Apify Google Maps scraper for today's query and store new leads.")],
    "closer": [("qualify", "Daily, after Scout", "Score new leads and write outreach drafts (council for the top lead).")],
    "quill": [("posts", "Daily", "Write 1 LinkedIn + 1 Telegram post with the LLM Council.")],
    "sentinel": [("audit", "Daily, after Quill", "QA drafts, flag stalled/overdue tasks, reconcile the Google Sheet.")],
    "echo": [("publish", "Daily, after Sentinel", "Publish approved posts to Telegram and the blog.")],
    "haven": [("inbox", "Daily + on every new contact form", "Triage tickets, reply via council, escalate risky ones.")],
    "lens": [("report", "Daily, last", "Compute KPIs and write the daily report with insights.")],
}

for e in TEAM:
    d = ROOT / "employees" / e["key"]
    (d / "routines").mkdir(parents=True, exist_ok=True)
    (d / "ROLE.md").write_text(
        f"# {e['emoji']} {e['name']} — {e['title']}\n\n"
        f"**Responsibilities:** {e['responsibilities']}\n\n**Inputs:** {e['inputs']}\n\n**Outputs:** {e['outputs']}\n\n"
        f"**Tools & integrations:** {e['tools']}\n\n**KPIs:** {e['kpis']}\n\n"
        f"**Reports to:** {e['reports_to']} · **Hands work to:** {e['hands_to']}\n", encoding="utf-8")
    sched = "# Schedule\n\n| Routine | When | What |\n|---|---|---|\n" + "".join(
        f"| {r} | {w} | {x} |\n" for r, w, x in ROUTINES[e["key"]])
    (d / "SCHEDULE.md").write_text(sched, encoding="utf-8")
    (d / "RELEASES.md").write_text(
        "# Released channels\n\nChannels this employee may act on without a Boss click.\n\n"
        + {"echo": "- telegram: LocalLift News channel\n- blog: agency blog\n",
           "haven": "- email-reply: reply to customers who wrote to us via the contact form\n",
           "atlas": "- email-boss: daily bulletin to the Boss\n- sheet: Google Sheet ledger\n"}.get(
            e["key"], "- (none — outputs wait for Boss approval)\n"), encoding="utf-8")
    for r, w, x in ROUTINES[e["key"]]:
        rd = d / "routines" / r
        rd.mkdir(exist_ok=True)
        (rd / "SKILL.md").write_text(f"---\nname: {e['key']}-{r}\ndescription: {x}\n---\n\n# {r}\n\nWhen: {w}\n\n{x}\n\n"
                                     f"Implementation: app/agents.py -> `{e['key']}()`; scheduling: app/engine.py.\n",
                                     encoding="utf-8")
    installed = {
        "employee": e["key"], "name": e["name"], "title": e["title"],
        "template": "markfulton/ai-employees folder convention (ROLE.md, SCHEDULE.md, RELEASES.md, routines/*/SKILL.md)",
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agency": "LocalLift AI", "runtime": "locallift-ai worker (app/engine.py)",
        "reports_to": e["reports_to"], "hands_to": e["hands_to"],
        "routines": [{"id": r, "when": w, "skill": f"routines/{r}/SKILL.md"} for r, w, _ in ROUTINES[e["key"]]],
        "tools": e["tools"], "kpis": e["kpis"],
        "released_channels": {"echo": ["telegram", "blog"], "haven": ["email-reply"], "atlas": ["email-boss", "sheet"]}.get(e["key"], []),
        "ledger": "Google Sheet via Apps Script (APPS_SCRIPT_URL)",
    }
    (d / ".installed.json").write_text(json.dumps(installed, indent=2), encoding="utf-8")
    print("hired", e["name"])

(ROOT / "employees" / ".installed.json").write_text(json.dumps({
    "agency": "LocalLift AI", "employees": [e["key"] for e in TEAM],
    "project_manager": "atlas", "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2))
print("done")
