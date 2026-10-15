# AI readiness assessment chatbot

The practical part of a diploma thesis. The thesis defines a methodology for
assessing an organization's AI readiness and applies it in two formats, a
chatbot and a questionnaire, which are then compared. This repository holds the
chatbot. The questionnaire will be added later, reusing the same rubric,
scoring code and report.

## The methodology

- **Four dimensions** (Data, People, Strategy, Technology), each with three
  **subdimensions**. All texts are in [`worker/src/rubric.json`](worker/src/rubric.json).
- Each subdimension has an opening question, **criteria** (what evidence to
  look for) and level descriptions from **0 (No capability)** to
  **5 (Optimized)**.
- Each subdimension is scored as a whole number from 0 to 5 by matching the
  respondent's answers to the level descriptions. No evidence at all gives 0.
- **Dimension score** = mean of its subdimension scores.
  **Overall score** = mean of the dimension scores.
- **Maturity level** = the score rounded down. Calculations are exact
  (fractions); scores are rounded to two decimals only for display.
- The report shows the overall score and level, a radar chart of the
  dimensions, and per dimension its level and its **main gap**: the
  lowest-scoring subdimension with a one-sentence description of what the
  next level requires. Subdimension scores are not shown to the respondent.

## How the chatbot works

Code controls the conversation; the language model will only do two narrow
tasks and never calculates anything.

```
for each subdimension:
    ask the opening question
    loop:
        respondent answers
        CHECK  (model): which criteria have evidence? if some are missing,
                        suggest one follow-up question
        if something is missing and fewer than 2 follow-ups were asked:
            ask the follow-up
        else:
            break
    SCORE (model): level 0-5 with evidence, reason and why not higher
    save the log entry for this subdimension
REPORT (code): means, levels, main gaps
```

In the current demo, CHECK and SCORE are replaced by fixed functions
(`fake_check`, `fake_score` in [`flow.py`](worker/src/flow.py)); the rest of
the flow is the final one.

Each subdimension is scored only on the answers given in its own section.

## Architecture

```
browser                       Cloudflare Worker (Python)              Cloudflare D1
-------                       --------------------------              -------------
index.html  (chat)   --->     POST /api/conversations          <--->  conversations
                     --->     POST /api/conversations/{id}/answer      (state, JSON)
result.html (report) --->     GET  /api/conversations/{id}/report      logs
                                                                        (log, JSON)
```

- **Hosting:** Cloudflare Workers with Python (FastAPI). The pages in
  `worker/public/` are served as static files; everything under `/api` goes
  to the Python app.
- **One step per request:** Workers keep nothing in memory between requests.
  Every answer loads the conversation state from D1, runs one step of the flow
  and saves the state again. The model gets the earlier conversation from this
  stored transcript.
- **Log:** one JSON log per conversation, written after every subdimension.
  It contains the transcript, every check result and the score with its
  evidence and reasoning, plus an empty `review` block for manual review.
- **Plain Python, no LangGraph:** the current LangGraph cannot be installed on
  Python Workers, and the flow is short enough to be clearer as plain code
  (see [`DECISIONS.md`](DECISIONS.md)).

## Repository layout

```
worker/
  wrangler.jsonc          Cloudflare configuration (Worker, D1 database, static files)
  pyproject.toml          Python packages
  migrations/             SQL that creates the database tables
  public/index.html       chat page
  public/result.html      report page
  src/rubric.json         the rubric: all methodology texts
  src/rubric.py           loads and validates the rubric
  src/scoring.py          means, levels, main gap
  src/report.py           report data as JSON
  src/flow.py             the conversation flow, one step per request
  src/storage.py          reads and writes state and logs in D1
  src/main.py             the API endpoints
  src/demo.py             made-up scores for the example report
  tests/                  pytest tests
DECISIONS.md              every design decision with date and reason
claude.md                 the project specification
```

## Running it locally

Requirements: [uv](https://docs.astral.sh/uv/), Node.js (for Cloudflare's
`wrangler` tool), and a Cloudflare account for deploying.

```bash
cd worker
uv run pytest                                                  # tests
uv run pywrangler d1 migrations apply ai-readiness --local     # once: local database tables
uv run pywrangler dev                                          # http://localhost:8787
```

The tests include the worked example from the thesis (Data 4, 2, 5 /
People 5, 3, 2 / Strategy 2, 3, 3 / Technology 1, 1, 0 gives an overall
score of 2.58, level 2).

Deploying:

```bash
uv run pywrangler d1 migrations apply ai-readiness --remote    # when migrations change
uv run pywrangler deploy
```

## Data and privacy

Conversations contain respondents' answers. They are stored only in the
Cloudflare D1 database, never in this repository. The demo is currently open
to anyone with the link; personal links and a consent screen are planned
(step 7).
