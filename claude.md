# AI readiness assessment chatbot (diploma thesis MVP)

## What this project is

This is the practical part of a diploma thesis. The thesis defines a
methodology for assessing an organization's AI readiness and applies it
in two formats, a chatbot and a questionnaire, which are later compared.
This repository builds the chatbot first. The questionnaire will be added
later and must reuse the same rubric file, scoring code and report.

The author has to describe and defend every component in the thesis.
Prefer simple, explicit code she can explain over clever abstractions.
This is an MVP: build what is listed here and nothing more.

## The methodology (fixed, do not change it)

- Four dimensions (Data, People, Strategy, Technology), each with three
  subdimensions. `rubric/rubric.json` currently holds only Data; the
  other three will be added later, and no code should need to change
  when they are.
- Each subdimension is scored as a whole number from 0 to 5 by matching
  what the respondent said to the level descriptions in the rubric.
- Dimension score = mean of its scored subdimension scores.
- Overall score = mean of the four dimension scores.
- Maturity level of a dimension or of the overall score = the score
  rounded down (floor). Carry full precision through the calculation and
  round to two decimals only for display.
- A subdimension with no score (no evidence at all) is left out of its
  dimension mean. If a dimension has no scored subdimension, it has no
  score, and the overall score is reported as not available.
- The remaining rules (mixed evidence, plans, insufficient evidence,
  follow-up limit) are in `rules` in the rubric file. Read them from
  there; do not hard-code them.

The texts in `rubric/rubric.json` and the rules in the prompts are part
of the thesis methodology. Do not reword them. If something in them
blocks you or looks wrong, say so and ask.

## How the chatbot works

Code controls the flow. The language model does two narrow tasks and
never does arithmetic.

1. Start: fixed intro message.
2. Ask: the fixed `opening_question` of the current subdimension.
3. Answer: wait for the respondent.
4. Check (model call, `prompts/check.md`): for each criterion, is there
   evidence, with the quote; if something is missing, one follow-up
   question.
5. Loop: if not everything is covered and fewer than `max_follow_ups`
   follow-ups were asked, ask the follow-up and go back to 3.
6. Score (model call, `prompts/score.md`): level, evidence quotes,
   reason, why not higher, flags.
7. Next subdimension, back to 2, until all are done.
8. Report (code): scores, levels and the report described below.
9. Save: write the conversation log after every subdimension.

In LangGraph this is four nodes (ask, check, score, report) with
conditional edges for the follow-up loop and the next subdimension, and
a pause for the respondent's answer.

## The report

`index.html` in the repository root shows how the report must look. It
already exists and was made by the author. Treat it as the design
reference: keep its layout and styling, and fill it with the calculated
scores and rubric texts instead of the example values. Do not redesign
it, and do not overwrite the original file; work from it as a template.

The report contains:

- Overall score, its level name and the level description from `scale`.
- A radar chart of the dimension scores, each axis from 0 to 5.
- Per dimension: score, level name, and the text from `report_levels`
  for that level.
- Per dimension, the main gap: the lowest-scoring subdimension, its
  current level description and the description of the next level. On a
  tie, take the subdimension listed first. If the lowest is at level 5,
  show no gap.
- Subdimension scores are not shown as numbers to the respondent.

If `index.html` shows something this list does not cover, or leaves out
something this list requires, do not decide yourself. Tell the author
what differs and ask.

## The conversation log

One JSON file per conversation in `logs/`, written after every
subdimension so nothing is lost if a conversation stops. The author
reads these files to find mistakes and improve the prompts, so they must
contain everything needed to see why a score was given.

Per conversation: an id, start time, model id and settings, rubric
version, and the version of each prompt (read from the first line of the
prompt file).

Per subdimension:

```json
{
  "subdimension": "Ownership and Stewardship",
  "transcript": [
    {"role": "chatbot", "text": "..."},
    {"role": "respondent", "text": "..."}
  ],
  "checks": [
    {
      "criteria": {
        "assignment": {"covered": true, "quote": "..."},
        "documentation": {"covered": false, "quote": null}
      },
      "all_covered": false,
      "follow_up": "...",
      "targets": "documentation"
    }
  ],
  "score": {
    "level": 2,
    "evidence": {"assignment": "...", "documentation": "no evidence"},
    "reason": "...",
    "why_not_higher": "...",
    "flags": []
  },
  "review": {"my_level": null, "error_type": null, "note": null}
}
```

The `review` block stays empty; the author fills it in by hand later.
The chatbot never reads log files back.

## Stack

- Python, LangGraph for the workflow.
- LangChain's chat model integration, so the provider can be swapped.
  Provider, model id and temperature come from environment variables.
  Ask the author which provider she uses before the first model call.
  Use temperature 0 and a fixed, dated model version.
- Pydantic models for the rubric file, the check result, the score and
  the log. Use them for structured model output.
- Streamlit for the chat. The report is `index.html` filled with data
  and shown inside the Streamlit app. If the radar chart in `index.html`
  is already drawn with a library, keep that library; otherwise use
  Plotly.
- pytest for the scoring code.
- API keys in `.env`, never in the repository. `.env` and `logs/` go in
  `.gitignore`, because logs will contain respondents' answers.

## Layout

```
index.html              report design by the author, used as template
rubric/rubric.json      input, same for every conversation
prompts/check.md        input, versioned in its first line
prompts/score.md        input, versioned in its first line
app/rubric.py           loads and validates the rubric file
app/scoring.py          means and levels
app/report.py           report data, fills the index.html template
app/graph.py            the workflow
app/logging.py          writes the conversation log
app/cli.py              terminal chat for testing
app/ui.py               Streamlit app
tests/
logs/
DECISIONS.md
```

Other files may already exist in the repository. Do not move or delete
them without asking.

## Build order

Work on one step at a time. After each step, stop, show how to run or
test what you built, and wait for the author before starting the next.

1. Rubric file. Done for the Data dimension.
2. Rubric loader, scoring code and report data, with tests. No model.
   This includes filling `index.html` with made-up scores so the author
   can check the report.
3. The two prompts tested alone: a small script that runs the check and
   the scoring prompt on hand-written answers and prints the result.
4. The workflow as a terminal chat, with logs written to `logs/`.
5. The Streamlit chat with the report shown at the end.
6. The other three dimensions in the rubric file (the author writes
   these).
7. Deployment: hosting, a database for the logs, personal links, a
   consent screen.
8. Pilot conversations and prompt refinement.

Tests for step 2 must include this worked example from the thesis:

| Dimension  | Subdimension scores | Dimension score | Level |
|------------|---------------------|-----------------|-------|
| Data       | 4, 2, 5             | 3.67            | 3     |
| People     | 5, 3, 2             | 3.33            | 3     |
| Strategy   | 2, 3, 3             | 2.67            | 2     |
| Technology | 1, 1, 0             | 0.67            | 0     |

Overall score 2.58, level 2. Also test a dimension with one unscored
subdimension (4, none, 5 gives 4.5) and the main-gap tie rule.

## Not in scope until the author asks

A database, login, deployment, resuming a conversation, a summary the
respondent confirms, the questionnaire, fine-tuning, and any extra model
call beyond check and score.

## Decision log

Keep `DECISIONS.md` up to date. Whenever a design choice is made, by you
or by the author, add a line with the date, the decision and the reason.
The thesis chapter on implementation is written from this file.