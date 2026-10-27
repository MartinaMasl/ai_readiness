"""Score saved conversations again with the current score prompt, under
different versions of the rule `mixed_evidence`, and compare the levels
with the ones in the log. For trying out prompt and rule changes on real
answers without new conversations. The rubric file is not changed: the
variants below replace the rule only inside this script.

Each subdimension gets the other subdimensions of its dimension as
context, as in the chatbot.

Run from the worker/ folder (needs worker/.dev.vars like try_prompts.py):
    uv run python scripts/rescore.py <id> [<id> ...] [--runs 3] [--remote]
"""

import asyncio
import json
import sys
from pathlib import Path

from openai import AsyncOpenAI

WORKER = Path(__file__).parent.parent
sys.path.insert(0, str(WORKER / "src"))
sys.path.insert(0, str(WORKER / "scripts"))

import llm  # noqa: E402
from rubric import load_rubric  # noqa: E402
from show_log import query, quoted  # noqa: E402
from try_prompts import read_settings  # noqa: E402

BEST_FIT = (
    "Choose the level whose description fits the evidence best as a whole. A level fits when "
    "the evidence supports its main characteristics and does not contradict it; details that "
    "were not mentioned do not rule a level out on their own. A level does not fit when the "
    "evidence contradicts its description."
)

# Versions of the rule `mixed_evidence` to compare. None: the rule as it is in the rubric.
VARIANTS = {
    "rubric": None,
    "best fit, lower": BEST_FIT + " If the evidence still fits two levels equally, choose the lower one.",
    "best fit, higher": BEST_FIT + " If the evidence still fits two levels equally, choose the higher one.",
}

# At most this many model calls at the same time.
PARALLEL = 6


def with_rule(rubric, text):
    if text is None:
        return rubric
    return rubric.model_copy(update={"rules": rubric.rules.model_copy(update={"mixed_evidence": text})})


async def main() -> None:
    args = sys.argv[1:]
    remote = "--remote" in args
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else 3
    ids = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--runs")]
    if not ids:
        sys.exit(__doc__)

    settings = read_settings()
    model = llm.OpenAIModel(AsyncOpenAI(api_key=settings["OPENAI_API_KEY"], timeout=120.0),
                            settings["MODEL_ID"], float(settings["TEMPERATURE"]))
    rubric = load_rubric()
    subs = {s.name: s for d in rubric.dimensions for s in d.subdimensions}
    print(f"score.md {model.score_version}, model {model.model_id}, temperature {model.temperature}, "
          f"{runs} runs per variant\n")

    limit = asyncio.Semaphore(PARALLEL)

    async def score(variant_rubric, sub, transcript, context):
        async with limit:
            return (await model.score(variant_rubric, sub, transcript, context))["level"]

    for conversation_id in ids:
        rows = query("SELECT dimension, subdimension, level, entry FROM subdimension_results "
                     f"WHERE conversation_id = {quoted(conversation_id)} ORDER BY position", remote)
        entries = [dict(row, entry=json.loads(row["entry"])) for row in rows]
        print(f"=== {conversation_id}")
        print(f"{'subdimension':28} {'logged':>6}  " + "  ".join(f"{name:>17}" for name in VARIANTS))

        jobs = []
        for row in entries:
            context = [
                {"subdimension": other["subdimension"], "messages": other["entry"]["transcript"]}
                for other in entries
                if other["dimension"] == row["dimension"] and other["subdimension"] != row["subdimension"]
            ]
            for text in VARIANTS.values():
                jobs.append([score(with_rule(rubric, text), subs[row["subdimension"]], row["entry"]["transcript"], context)
                             for _ in range(runs)])
        results = await asyncio.gather(*[asyncio.gather(*job) for job in jobs])

        per_row = len(VARIANTS)
        for i, row in enumerate(entries):
            levels = results[i * per_row:(i + 1) * per_row]
            cells = "  ".join(f"{' '.join(map(str, ls)):>17}" for ls in levels)
            print(f"{row['subdimension']:28} {row['level']:>6}  {cells}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
