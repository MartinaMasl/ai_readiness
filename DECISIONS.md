# Decisions

- 2026-10-15: Hosting on Cloudflare Workers (Python Workers) with FastAPI. Reason: chosen by the author. Minimal example in `worker/`.
- 2026-10-15: The Worker serves `index.html` at `/` as a static asset and the FastAPI app under `/api`. Reason: author wants the report page on the main URL. A build step copies the root `index.html` into `worker/public/`, so the original file is not moved or duplicated in git.
