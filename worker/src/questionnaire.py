"""The questionnaire: one multiple-choice question per subdimension.

The questions are in questionnaire.json (written by the author). Scoring:
the chosen option's value is the subdimension score, a whole number from 0
to 5; "I don't know" scores 0 and sets the flag insufficient_evidence, as
in the chatbot. Dimension and overall scores are then calculated by the
same code as for the chatbot (scoring.py, report.py), from the
subdimension_results rows written by results().

The answers are kept in the questionnaire's conversations row (its state)
while the respondent answers: {"answers": {item id: {"option": index,
"first_answered_at": ..., "answered_at": ...}}, ...}.
"""

import json
from pathlib import Path

from rubric import Rubric

# questionnaire.json sits next to this file; wrangler.jsonc bundles it into the Worker.
QUESTIONNAIRE_PATH = Path(__file__).parent / "questionnaire.json"


def load_questionnaire(rubric: Rubric, path: Path = QUESTIONNAIRE_PATH) -> dict:
    """Read questionnaire.json and check that it fits the rubric: one question
    for every subdimension, with the same names, and options scored 0 to 5
    plus one "I don't know"."""
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = [(d.name, s.name) for d in rubric.dimensions for s in d.subdimensions]
    found = [(d["name"], s["name"]) for d in content["dimensions"] for s in d["subdimensions"]]
    if found != expected:
        raise ValueError(f"questionnaire.json does not match the rubric's subdimensions: {found}")
    for d in content["dimensions"]:
        for s in d["subdimensions"]:
            if len(s["items"]) != 1:
                raise ValueError(f"{s['name']}: expected exactly one question")
            options = s["items"][0]["options"]
            scored = sorted(o["value"] for o in options if not o.get("dont_know"))
            if scored != [0, 1, 2, 3, 4, 5] or sum(1 for o in options if o.get("dont_know")) != 1:
                raise ValueError(f"{s['name']}: options must be scored 0 to 5, plus one \"I don't know\"")
    return content


def items(content: dict, rubric: Rubric) -> list[dict]:
    """The questions in rubric order, only for the dimensions in this rubric
    (which DIMENSIONS may have limited): [{"dimension", "subdimension", "item"}]."""
    names = {d.name for d in rubric.dimensions}
    return [
        {"dimension": d["name"], "subdimension": s["name"], "item": s["items"][0]}
        for d in content["dimensions"] if d["name"] in names
        for s in d["subdimensions"]
    ]


def score(item: dict, option: int) -> tuple[int, bool]:
    """(subdimension score, insufficient_evidence) for the chosen option."""
    chosen = item["options"][option]
    return chosen["value"], bool(chosen.get("dont_know"))


def first_unanswered(question_list: list[dict], answers: dict) -> int:
    """Where "Continue" resumes: the index of the first unanswered question,
    or len(question_list) if all are answered."""
    for index, q in enumerate(question_list):
        if q["item"]["id"] not in answers:
            return index
    return len(question_list)


def results(question_list: list[dict], answers: dict) -> list[tuple[dict, dict]]:
    """One (finished, entry) pair per question, in the form storage.save_result
    expects, so the questionnaire's scores are stored exactly like the
    chatbot's. entry is the subdimension's log entry: the chosen option and
    its score."""
    pairs = []
    for position, q in enumerate(question_list):
        answer = answers[q["item"]["id"]]
        option = q["item"]["options"][answer["option"]]
        level, dont_know = score(q["item"], answer["option"])
        finished = {"position": position, "dimension": q["dimension"], "subdimension": q["subdimension"]}
        entry = {
            "subdimension": q["subdimension"],
            "item": q["item"]["id"],
            "question": q["item"]["question"],
            "answer": {"option": answer["option"], "text": option["text"], "dont_know": dont_know},
            "first_answered_at": answer["first_answered_at"],
            "answered_at": answer["answered_at"],
            "score": {"level": level, "flags": ["insufficient_evidence"] if dont_know else []},
            "review": {"my_level": None, "error_type": None, "note": None},
        }
        pairs.append((finished, entry))
    return pairs


def for_page(question_list: list[dict]) -> list[dict]:
    """The questions as the page shows them: no option values, so the scores
    are never sent to the browser."""
    return [
        {
            "id": q["item"]["id"],
            "dimension": q["dimension"],
            "question": q["item"]["question"],
            "options": [{"text": o["text"], "dont_know": bool(o.get("dont_know"))} for o in q["item"]["options"]],
        }
        for q in question_list
    ]
