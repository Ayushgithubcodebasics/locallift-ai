---
name: sentinel-audit
description: QA drafts, flag stalled/overdue tasks, reconcile the Google Sheet.
---

# audit

When: Daily, after Quill

QA drafts, flag stalled/overdue tasks, reconcile the Google Sheet.

Implementation: app/agents.py -> `sentinel()`; scheduling: app/engine.py.
