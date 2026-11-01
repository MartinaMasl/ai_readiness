import asyncio

import flow
import timing
from fakes import FakeModel
from rubric import load_rubric

RUBRIC = load_rubric()


def message(role, at):
    return {"role": role, "text": "...", "at": f"2026-11-01T10:{at}+00:00"}


# Question shown 10:00:00, answered 10:01:00, follow-up shown 10:01:08,
# answered 10:02:00, next question shown 10:02:05.
MESSAGES = [
    message("chatbot", "00:00"),
    message("respondent", "01:00"),
    message("chatbot", "01:08"),
    message("respondent", "02:00"),
    message("chatbot", "02:05"),
]


def test_duration_runs_from_first_message_to_last_answer():
    assert timing.duration_seconds(MESSAGES) == 120  # the last question, not yet answered, does not count


def test_waiting_is_the_time_between_answers_and_the_chatbot_messages_after_them():
    assert timing.waiting_seconds(MESSAGES) == 8 + 5


def test_unknown_without_timestamps_or_answers():
    old = [{"role": "chatbot", "text": "..."}, {"role": "respondent", "text": "..."}]
    assert timing.duration_seconds(old) is None and timing.waiting_seconds(old) is None
    assert timing.duration_seconds(MESSAGES[:1]) is None
    assert timing.minutes(None) == "" and timing.minutes(90) == 1.5 and timing.minutes(20) == 0.33


def test_every_message_of_a_conversation_has_a_timestamp():
    state, _ = flow.new_conversation(RUBRIC, "test-id", flow.now(), FakeModel().info())
    asyncio.run(flow.handle_answer(RUBRIC, state, "an answer", FakeModel()))
    asyncio.run(flow.handle_answer(RUBRIC, state, "an answer", FakeModel()))
    assert all(m["at"] for m in state["transcript"])
    assert timing.duration_seconds(state["transcript"]) >= 0
