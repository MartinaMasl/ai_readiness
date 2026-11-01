"""Export scores and conversation metrics as CSV, for the analysis per
respondent and per role (technical / business).

Two tables:
- conversations   one row per conversation: respondent, role, time
                  (total, waiting, per dimension), dimension scores and
                  levels, overall score and level, and totals of the
                  conversation metrics
- subdimensions   one row per scored subdimension: its level, time and the
                  metrics of that part of the conversation

Times are in minutes, from the message timestamps (see src/timing.py for
the definitions); empty for conversations from before timestamps existed.

Scores are calculated with scoring.py, exactly as in the report. The CSV
contains no answer texts, only codes and numbers. Conversations of test
links (codes starting with "T") and conversations without a respondent
are left out unless --include-tests is given.

Run from the worker/ folder:
    uv run python scripts/export_scores.py conversations [--remote] > ~/Desktop/conversations.csv
    uv run python scripts/export_scores.py subdimensions [--remote] > ~/Desktop/subdimensions.csv
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import scoring  # noqa: E402
import timing  # noqa: E402
from rubric import load_rubric  # noqa: E402
from show_log import query  # noqa: E402


def words(text: str) -> int:
    return len(text.split())


def subdimension_metrics(entry: dict) -> dict:
    """Metrics of one scored subdimension, from its log entry."""
    answers = [m["text"] for m in entry["transcript"] if m["role"] == "respondent"]
    evidence = entry["score"]["evidence"].values()
    last_check = entry["checks"][-1]["criteria"] if entry["checks"] else {}
    return {
        "criteria": len(entry["score"]["evidence"]),
        "criteria_with_evidence": sum(1 for e in evidence if e != "no evidence" and not e.startswith("not applicable")),
        "criteria_not_applicable": sum(1 for e in evidence if e.startswith("not applicable")),
        "criteria_no_evidence": sum(1 for e in evidence if e == "no evidence"),
        "criteria_dont_know": sum(1 for c in last_check.values() if c["status"] == "dont_know"),
        "insufficient_evidence": int("insufficient_evidence" in entry["score"]["flags"]),
        "follow_ups": entry["counters"]["follow_ups"],
        "clarifications": entry["counters"]["clarifications"],
        "redirects": entry["counters"]["redirects"],
        "respondent_messages": len(answers),
        "respondent_words": sum(words(a) for a in answers),
        "review_level": entry["review"]["my_level"],
    }


def load(remote: bool, include_tests: bool) -> tuple[list[dict], dict[str, list[dict]]]:
    conversations = query(
        "SELECT c.id, c.respondent_code, r.role, c.created_at, c.updated_at, "
        "json_extract(c.state, '$.done') AS done, json_extract(c.state, '$.dimensions') AS dimensions, "
        "json_extract(c.state, '$.transcript') AS transcript "
        "FROM conversations c LEFT JOIN respondents r ON r.code = c.respondent_code "
        "ORDER BY c.respondent_code, c.created_at", remote)
    if not include_tests:
        conversations = [c for c in conversations
                         if c["respondent_code"] and not c["respondent_code"].startswith("T")]
    results = {}
    for row in query("SELECT conversation_id, position, dimension, subdimension, level, entry "
                     "FROM subdimension_results ORDER BY conversation_id, position", remote):
        results.setdefault(row["conversation_id"], []).append(row)
    return conversations, results


def conversation_rows(conversations, results) -> list[dict]:
    # Columns for every dimension that occurs, so all rows have the same columns in the same order.
    all_dimensions = list(dict.fromkeys(
        name for c in conversations for name in (json.loads(c["dimensions"]) if c["dimensions"] else [])))
    dimension_of = {s.name: d.name for d in load_rubric().dimensions for s in d.subdimensions}
    rows = []
    for c in conversations:
        scored = results.get(c["id"], [])
        dimensions = json.loads(c["dimensions"]) if c["dimensions"] else []
        transcript = json.loads(c["transcript"]) if c["transcript"] else []
        total = timing.duration_seconds(transcript)
        waiting = timing.waiting_seconds(transcript)
        row = {
            "respondent": c["respondent_code"], "role": c["role"], "conversation": c["id"],
            "started_at": c["created_at"], "finished": int(bool(c["done"])), "subdimensions_scored": len(scored),
            "minutes": timing.minutes(total),
            "waiting_minutes": timing.minutes(waiting),
            "respondent_minutes": timing.minutes(None if total is None else total - waiting),
        }
        for name in all_dimensions:
            # The messages of this dimension's subdimensions, in the order they were sent.
            part = [m for m in transcript if dimension_of.get(m["subdimension"]) == name]
            row[f"{name}_minutes"] = timing.minutes(timing.duration_seconds(part))
            row[f"{name}_waiting_minutes"] = timing.minutes(timing.waiting_seconds(part))
        dimension_scores = []
        for name in all_dimensions:
            levels = [r["level"] for r in scored if r["dimension"] == name]
            expected = 3  # every dimension has three subdimensions
            if len(levels) == expected:
                score = scoring.dimension_score(levels)
                dimension_scores.append(score)
                row[f"{name}_score"] = scoring.display(score)
                row[f"{name}_level"] = scoring.maturity_level(score)
            else:
                row[f"{name}_score"] = row[f"{name}_level"] = ""
        if dimensions and len(dimension_scores) == len(dimensions):
            overall = scoring.overall_score(dimension_scores)
            row["overall_score"] = scoring.display(overall)
            row["overall_level"] = scoring.maturity_level(overall)
        else:
            row["overall_score"] = row["overall_level"] = ""
        totals = [subdimension_metrics(json.loads(r["entry"])) for r in scored]
        for key in ["follow_ups", "clarifications", "redirects", "respondent_words",
                    "criteria_no_evidence", "criteria_dont_know", "insufficient_evidence"]:
            row[f"total_{key}"] = sum(t[key] for t in totals)
        rows.append(row)
    return rows


def subdimension_rows(conversations, results) -> list[dict]:
    rows = []
    for c in conversations:
        for r in results.get(c["id"], []):
            rows.append({
                "respondent": c["respondent_code"], "role": c["role"], "conversation": c["id"],
                "position": r["position"], "dimension": r["dimension"], "subdimension": r["subdimension"],
                "level": r["level"], **subdimension_metrics(json.loads(r["entry"])),
                "minutes": timing.minutes(timing.duration_seconds(json.loads(r["entry"])["transcript"])),
                "waiting_minutes": timing.minutes(timing.waiting_seconds(json.loads(r["entry"])["transcript"])),
            })
    return rows


def main() -> None:
    args = sys.argv[1:]
    table = next((a for a in args if not a.startswith("--")), None)
    if table not in ("conversations", "subdimensions"):
        sys.exit(__doc__)
    conversations, results = load("--remote" in args, "--include-tests" in args)
    rows = (conversation_rows if table == "conversations" else subdimension_rows)(conversations, results)
    if not rows:
        sys.exit("Nothing to export.")
    columns = list(dict.fromkeys(key for row in rows for key in row))  # all columns, in first-seen order
    writer = csv.DictWriter(sys.stdout, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)


if __name__ == "__main__":
    main()
