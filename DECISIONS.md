# Decisions

- 2026-10-15: Hosting on Cloudflare Workers (Python Workers) with FastAPI. Reason: chosen by the author. Minimal example in `worker/`.
- 2026-10-15: `index.html` moved to `worker/public/index.html` and served at `/` as a static asset; the FastAPI app is under `/api`. Reason: author's choice, so the report page is on the main URL with no copy step.
- 2026-10-15: Temporary "API test" button at the bottom of `index.html` that calls `POST /api/echo`. Reason: author wants to check the page can reach the API. Remove before the real report.
