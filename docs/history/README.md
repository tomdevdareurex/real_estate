# History

Completed plans, kept for the reasoning rather than the steps.

These documents describe work that is **already done**. They are here because the *why* behind a
decision outlives the plan that carried it out, and because a future session that re-derives the
same conclusion from the same evidence wastes the time this record exists to save.

Do not treat them as current guidance. Where a finding here has since been overturned, the
retraction lives with the live documentation — `AGENTS.md` and `docs/online_scraping.md` are the
authorities on how the scraper behaves today.

| Document | What it settled |
| --- | --- |
| [connectivity_fix_plan.md](connectivity_fix_plan.md) | Why retrieval runs on `curl_cffi` rather than `httpx`: bot protection scores the TLS/HTTP-2 fingerprint, so a Chrome-shaped header set over a Python handshake is a contradiction. Still cited from `networking/curl_fetcher.py`. |
