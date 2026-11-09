"""Time spent in an assessment, to compare the chatbot with the questionnaire.
Used by scripts/export_scores.py.

Chatbot, from the message timestamps ("at"):
- duration: from the first chatbot message (the question shown) to the
  respondent's last answer, so a question still unanswered does not count;
- waiting: the time the respondent waited for the chatbot, i.e. for every
  chatbot message that follows an answer within that window, the time
  between the answer and that message (the wait for a question that is not
  answered yet lies outside the window and does not count);
- respondent time: duration minus waiting (reading, thinking, typing).
`messages` are in the order they were sent, each {"role", "text", "at"}.
Messages without "at" (conversations from before timestamps existed) give
None.

Questionnaire, from the time each question was first answered
(questionnaire_seconds): the questionnaire records no "shown" times, so a
question's time runs from the previous answer (or the start) to its first
answer, which is the same logic as the chatbot (a question appears right
after the previous answer). There is no waiting time.
"""

from datetime import datetime


def _seconds(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def _timed(messages: list[dict]) -> bool:
    return bool(messages) and all(m.get("at") for m in messages)


def duration_seconds(messages: list[dict]) -> float | None:
    """From the first message to the last answer of the respondent."""
    answers = [m for m in messages if m["role"] == "respondent"]
    if not _timed(messages) or not answers:
        return None
    return _seconds(messages[0]["at"], answers[-1]["at"])


def waiting_seconds(messages: list[dict]) -> float | None:
    """Total time between an answer and the chatbot message that follows it,
    within the window of duration_seconds (up to the last answer)."""
    if not _timed(messages):
        return None
    answers = [m for m in messages if m["role"] == "respondent"]
    end = answers[-1]["at"] if answers else messages[0]["at"]
    return sum(
        _seconds(before["at"], message["at"])
        for before, message in zip(messages, messages[1:])
        if before["role"] == "respondent" and message["role"] == "chatbot"
        and _seconds(message["at"], end) >= 0
    )


def questionnaire_seconds(started_at: str, finished_at: str | None, question_list: list[dict],
                          answers: dict) -> dict:
    """The questionnaire's times in seconds: {"total": start to Finish (None
    if not finished), dimension name: time for that dimension (None if not
    all its questions are answered)}.

    question_list: questionnaire.items() in the order shown; answers: the
    state's answers with "first_answered_at". A dimension's time runs from
    the previous dimension's last first answer (or the start) to its own."""
    times = {"total": _seconds(started_at, finished_at) if finished_at else None}
    previous_end = started_at
    for dimension in dict.fromkeys(q["dimension"] for q in question_list):
        ids = [q["item"]["id"] for q in question_list if q["dimension"] == dimension]
        if not all(i in answers for i in ids):
            times[dimension] = None
            continue
        end = max(answers[i]["first_answered_at"] for i in ids)
        times[dimension] = _seconds(previous_end, end)
        previous_end = end
    return times


# Active time: the time an assessment was worked on, without long pauses
# (respondents may stop and continue another day). It is the sum of the gaps
# between consecutive actions; a gap longer than PAUSE_LIMIT_SECONDS is a
# pause and counts as 0. In both formats it runs from the moment the first
# question is first shown to the respondent's last action:
# - chatbot: from the first question (shown together with the intro when the
#   conversation starts) over every message to the respondent's last answer;
# - questionnaire: from the first question (shown when "Start the
#   questionnaire" is pressed, first_question_shown_at) over the times every
#   question was first answered and last changed, to Finish.
PAUSE_LIMIT_SECONDS = 10 * 60


def _gaps(timestamps: list[str]) -> list[float]:
    ordered = sorted(timestamps, key=datetime.fromisoformat)
    return [_seconds(a, b) for a, b in zip(ordered, ordered[1:])]


def active_seconds(timestamps: list[str] | None) -> float | None:
    """Sum of the gaps between consecutive actions, gaps longer than
    PAUSE_LIMIT_SECONDS counting as 0. None when the actions are unknown."""
    if not timestamps:
        return None
    return sum(gap for gap in _gaps(timestamps) if gap <= PAUSE_LIMIT_SECONDS)


def pauses(timestamps: list[str] | None) -> int | None:
    """How many gaps were longer than PAUSE_LIMIT_SECONDS (left out of active time)."""
    if not timestamps:
        return None
    return sum(1 for gap in _gaps(timestamps) if gap > PAUSE_LIMIT_SECONDS)


def chatbot_actions(messages: list[dict]) -> list[str] | None:
    """The chatbot's action times: every message up to the respondent's last
    answer. The first question has the same time as the start. None for
    conversations without timestamps or without an answer."""
    answers = [i for i, m in enumerate(messages) if m["role"] == "respondent"]
    if not _timed(messages) or not answers:
        return None
    return [m["at"] for m in messages[:answers[-1] + 1]]


def questionnaire_actions(state: dict, finished_at: str | None) -> list[str] | None:
    """The questionnaire's action times: the first question shown, every
    answer's first and last time, and Finish. None if it is not finished or
    was started before first_question_shown_at was recorded (2026-11-09)."""
    shown = state.get("first_question_shown_at")
    if not shown or not finished_at:
        return None
    times = [shown, finished_at]
    for answer in state["answers"].values():
        times += [answer["first_answered_at"], answer["answered_at"]]
    return times


def minutes(seconds: float | None) -> float | str:
    """Seconds as minutes with two decimals, for the CSV; "" when unknown."""
    return "" if seconds is None else round(seconds / 60, 2)
