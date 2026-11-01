"""Time spent in a conversation, from the message timestamps ("at").

Used by scripts/export_scores.py, to compare the chatbot with the
questionnaire. Definitions:
- duration: from the first chatbot message (the question shown) to the
  respondent's last answer, so a question still unanswered does not count;
- waiting: the time the respondent waited for the chatbot, i.e. for every
  chatbot message that follows an answer, the time between the answer and
  that message;
- respondent time: duration minus waiting (reading, thinking, typing).

`messages` are in the order they were sent, each {"role", "text", "at"}.
Messages without "at" (conversations from before timestamps existed) give
None.
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
    within these messages."""
    if not _timed(messages):
        return None
    return sum(
        _seconds(before["at"], message["at"])
        for before, message in zip(messages, messages[1:])
        if before["role"] == "respondent" and message["role"] == "chatbot"
    )


def minutes(seconds: float | None) -> float | str:
    """Seconds as minutes with two decimals, for the CSV; "" when unknown."""
    return "" if seconds is None else round(seconds / 60, 2)
