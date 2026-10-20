"""Step 4: run the check and the score prompt on hand-written answers and
print what the model returns. Uses the same code as the Worker (llm.py).

Needs worker/.dev.vars with:
    OPENAI_API_KEY=sk-...
    MODEL_ID=gpt-6.1-sol
    TEMPERATURE=0

Run from the worker/ folder:
    uv run python scripts/try_prompts.py            all cases
    uv run python scripts/try_prompts.py 3          only case 3
"""

import asyncio
import json
import sys
from pathlib import Path

from openai import AsyncOpenAI, APIStatusError

WORKER = Path(__file__).parent.parent
sys.path.insert(0, str(WORKER / "src"))

import llm  # noqa: E402
from rubric import load_rubric  # noqa: E402

PROMPTS = WORKER.parent / "prompts"

# Hand-written conversations. Each one is the transcript of one
# subdimension so far; the last message is the respondent's.
CASES = [
    {
        "title": "Short, vague answer: expect a follow-up",
        "subdimension": "Discoverability",
        "messages": [
            ("respondent", "Mostly we just ask around. Usually someone in the team knows where things are."),
        ],
    },
    {
        "title": "Question about a term: expect a clarification, not the definition copied",
        "subdimension": "Discoverability",
        "messages": [
            ("respondent", "What exactly do you mean by 'what data exists'? Like databases?"),
        ],
    },
    {
        "title": "Question about the conversation: expect 'I don't know'",
        "subdimension": "Discoverability",
        "messages": [
            ("respondent", "How long will this take? And who will see my answers?"),
        ],
    },
    {
        "title": "Unrelated question: expect a polite refusal",
        "subdimension": "Discoverability",
        "messages": [
            ("respondent", "Can you recommend a good data catalog tool for us?"),
        ],
    },
    {
        "title": "Answer with a plan, after one follow-up: plans must not count",
        "subdimension": "Ownership and Stewardship",
        "messages": [
            ("respondent", "Each department looks after its own data. In finance it's clear, Petra owns the ledger data. Elsewhere it's more whoever created the spreadsheet."),
            ("chatbot", "Thank you. Is it written down anywhere who is responsible for which dataset?"),
            ("respondent", "Not really. We plan to introduce a data owner register next year, though."),
        ],
    },
    {
        "title": "Detailed answer: expect most criteria covered and a higher level",
        "subdimension": "Quality",
        "messages": [
            ("respondent", "We have documented quality rules for all our core datasets, for example completeness and valid ranges, and they apply across the company. They are checked automatically in the pipelines, and a failed check blocks the load. Data owners get a weekly report with the error rates, and we review the rules every quarter."),
        ],
    },
    {
        "title": "No evidence at all: expect level 0 and insufficient_evidence",
        "subdimension": "Quality",
        "messages": [
            ("respondent", "Honestly, I don't know, I'm not involved in that."),
        ],
    },
]


def read_settings() -> dict:
    path = WORKER / ".dev.vars"
    if not path.exists():
        sys.exit("Create worker/.dev.vars with OPENAI_API_KEY, MODEL_ID and TEMPERATURE (see the top of this file).")
    settings = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            settings[key.strip()] = value.strip().strip('"')
    for key in ["OPENAI_API_KEY", "MODEL_ID", "TEMPERATURE"]:
        if key not in settings:
            sys.exit(f"{key} is missing in worker/.dev.vars")
    return settings


async def run(client, settings: dict, prompt: str, answer_model, sub, to_log) -> None:
    try:
        result, model_version = await llm.call_model(
            client, settings["MODEL_ID"], float(settings["TEMPERATURE"]), prompt, answer_model
        )
    except APIStatusError as error:
        # Show OpenAI's explanation, e.g. if the model does not accept temperature 0.
        sys.exit(f"OpenAI returned {error.status_code}:\n{error.message}")
    print(f"  (answered by {model_version})")
    try:
        print(json.dumps(to_log(result, sub), indent=2, ensure_ascii=False))
    except ValueError as error:
        print(f"  NOT VALID: {error}")
        print(result.model_dump_json(indent=2))


async def main() -> None:
    settings = read_settings()
    client = AsyncOpenAI(api_key=settings["OPENAI_API_KEY"])
    rubric = load_rubric()
    subs = {sub.name: sub for dim in rubric.dimensions for sub in dim.subdimensions}
    check_version, check_template = llm.parse_prompt_file((PROMPTS / "check.md").read_text())
    score_version, score_template = llm.parse_prompt_file((PROMPTS / "score.md").read_text())
    print(f"Model {settings['MODEL_ID']}, temperature {settings['TEMPERATURE']}, "
          f"rubric {rubric.version}, check.md {check_version}, score.md {score_version}")

    chosen = [int(n) for n in sys.argv[1:]] or range(1, len(CASES) + 1)
    for number in chosen:
        case = CASES[number - 1]
        sub = subs[case["subdimension"]]
        messages = [{"role": "chatbot", "text": sub.opening_question}]
        messages += [{"role": role, "text": text} for role, text in case["messages"]]

        print(f"\n=== Case {number}: {case['title']} ({sub.name})")
        print(llm.format_transcript(messages))
        print("\n--- check")
        await run(client, settings, llm.check_prompt(check_template, rubric, sub, messages), llm.CheckResult, sub, llm.check_to_log)
        print("\n--- score")
        await run(client, settings, llm.score_prompt(score_template, rubric, sub, messages), llm.ScoreResult, sub, llm.score_to_log)


if __name__ == "__main__":
    asyncio.run(main())
