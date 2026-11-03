"""The study: what a respondent may do next.

Every respondent does two assessments, the chatbot and the questionnaire,
in the order given by their format_order, then compares both reports and
gives feedback. This module only decides the status of the three steps
from what is stored; it reads and writes nothing itself.

Rules:
- Nothing can be started before consent is given.
- The second assessment unlocks when the first is finished.
- "Compare and give feedback" unlocks when both are finished.
- A finished assessment is final: it can only be viewed (its report).

Status of a step: "not_started", "in_progress", "completed" or "locked"
(with the reason shown to the respondent).
"""

FORMATS = ("chatbot", "questionnaire")
ORDERS = {
    "chatbot_first": ["chatbot", "questionnaire"],
    "questionnaire_first": ["questionnaire", "chatbot"],
}

NEED_CONSENT = "Available after you give your consent"
NEED_FIRST = "Available after the first assessment"
NEED_BOTH = "Available after both assessments"


def assessment_status(row: dict | None) -> str:
    """row: the respondent's conversations row for one format (excluded rows
    left out), with at least finished_at; None if not started."""
    if row is None:
        return "not_started"
    return "completed" if row.get("finished_at") else "in_progress"


def steps(respondent: dict, rows: dict, feedback_answers: int) -> list[dict]:
    """The three steps in the respondent's order.

    respondent: format_order, consent_at, feedback_submitted_at
    rows: {"chatbot": row or None, "questionnaire": row or None}
    feedback_answers: how many comparison answers are saved so far
    """
    first, second = ORDERS[respondent["format_order"]]
    result = []
    for position, fmt in enumerate([first, second]):
        row = rows.get(fmt)
        status = assessment_status(row)
        reason = None
        if status == "not_started":
            if not respondent.get("consent_at"):
                status, reason = "locked", NEED_CONSENT
            elif position == 1 and assessment_status(rows.get(first)) != "completed":
                status, reason = "locked", NEED_FIRST
        result.append({
            "key": fmt,
            "position": position + 1,  # "Assessment 1 of 2" / "2 of 2"
            "status": status,
            "reason": reason,
            "conversation_id": row["id"] if row else None,
            "finished_at": row.get("finished_at") if row else None,
        })

    both_done = all(step["status"] == "completed" for step in result)
    if respondent.get("feedback_submitted_at"):
        feedback = "completed"
    elif not both_done:
        feedback = "locked"
    else:
        feedback = "in_progress" if feedback_answers else "not_started"
    result.append({
        "key": "feedback",
        "position": 3,
        "status": feedback,
        "reason": NEED_BOTH if feedback == "locked" else None,
        "finished_at": respondent.get("feedback_submitted_at"),
    })
    return result


def can_start(step_list: list[dict], key: str) -> bool:
    """Whether the respondent may open this step now (start or continue)."""
    step = next(s for s in step_list if s["key"] == key)
    return step["status"] in ("not_started", "in_progress")


def comparison_sections(content: dict, format_order: str) -> list[dict]:
    """The comparison questionnaire's sections in the respondent's order:
    the sections about the chatbot and the questionnaire (their id is the
    format) come in the order the respondent did the assessments; any
    other section keeps its place after them."""
    by_format = {s["id"]: s for s in content["sections"] if s["id"] in FORMATS}
    ordered = [by_format[fmt] for fmt in ORDERS[format_order] if fmt in by_format]
    return ordered + [s for s in content["sections"] if s["id"] not in FORMATS]


def next_format_order(existing: list[str]) -> str:
    """The order for a new respondent: whichever is less common among the
    existing respondents of the same role (excluding test codes), so both
    orders are used about equally within each role. On a tie,
    chatbot_first."""
    chatbot_first = existing.count("chatbot_first")
    questionnaire_first = existing.count("questionnaire_first")
    return "questionnaire_first" if questionnaire_first < chatbot_first else "chatbot_first"
