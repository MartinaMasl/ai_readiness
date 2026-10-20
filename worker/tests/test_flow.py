import flow
from report import build_report
from rubric import load_rubric

RUBRIC = load_rubric()
SUBS = flow.all_subdimensions(RUBRIC)


def run_whole_conversation():
    state, log = flow.new_conversation(RUBRIC, "test-id", "2026-10-15T00:00:00+00:00")
    replies = []
    while not state["done"]:
        replies.append(flow.handle_answer(RUBRIC, state, log, "an answer"))
    return state, log, replies


def test_starts_with_intro_and_first_opening_question():
    state, log = flow.new_conversation(RUBRIC, "test-id", "2026-10-15T00:00:00+00:00")
    assert state["transcript"][0]["text"] == flow.INTRO
    assert state["transcript"][1]["text"] == SUBS[0][1].opening_question
    assert log["rubric_version"] == RUBRIC.version
    assert log["subdimensions"] == []


def test_first_answer_gets_a_follow_up_then_next_opening_question():
    state, log = flow.new_conversation(RUBRIC, "test-id", "2026-10-15T00:00:00+00:00")
    message, done, log_changed = flow.handle_answer(RUBRIC, state, log, "first answer")
    assert message.startswith("Thank you. Could you tell me a bit more?")
    assert (done, log_changed) == (False, False)

    message, done, log_changed = flow.handle_answer(RUBRIC, state, log, "second answer")
    assert message == SUBS[1][1].opening_question
    assert (done, log_changed) == (False, True)


def test_whole_conversation_scores_every_subdimension():
    state, log, replies = run_whole_conversation()
    assert len(replies) == 2 * len(SUBS)  # one answer + one follow-up answer each
    assert replies[-1] == (flow.CLOSING, True, True)
    for dimension_name, sub in SUBS:
        assert sub.name in state["scores"][dimension_name]
    build_report(RUBRIC, state["scores"])  # report works on the result


def test_follow_ups_never_exceed_the_rubric_limit():
    state, log, _ = run_whole_conversation()
    for entry in log["subdimensions"]:
        chatbot_messages = [m for m in entry["transcript"] if m["role"] == "chatbot"]
        follow_ups = len(chatbot_messages) - 1  # minus the opening question
        assert follow_ups <= RUBRIC.rules.max_follow_ups


def test_clarification_request_is_answered_and_counts_towards_the_limit():
    state, log = flow.new_conversation(RUBRIC, "test-id", "2026-10-15T00:00:00+00:00")
    first, second = SUBS[0][1], SUBS[1][1]

    message, done, _ = flow.handle_answer(RUBRIC, state, log, "What do you mean?")
    assert message.startswith("[fake]") and first.opening_question in message
    assert flow.check_summary(RUBRIC, state) is None  # no check box for a clarification

    flow.handle_answer(RUBRIC, state, log, "Sorry, what?")  # second and last follow-up
    message, _, log_changed = flow.handle_answer(RUBRIC, state, log, "Still unclear?")
    assert message == second.opening_question  # limit reached: scored and moved on
    assert log_changed
    assert [c["message_type"] for c in log["subdimensions"][0]["checks"]] == ["clarification_request"] * 3


def test_log_has_one_entry_per_subdimension_in_the_agreed_shape():
    _, log, _ = run_whole_conversation()
    assert [e["subdimension"] for e in log["subdimensions"]] == [sub.name for _, sub in SUBS]
    entry = log["subdimensions"][0]
    assert set(entry) == {"subdimension", "transcript", "checks", "score", "review"}
    assert [m["role"] for m in entry["transcript"]] == ["chatbot", "respondent", "chatbot", "respondent"]
    assert entry["review"] == {"my_level": None, "error_type": None, "note": None}


def test_outline_lists_every_subdimension_in_order():
    names = [sub for dim in flow.outline(RUBRIC) for sub in dim["subdimensions"]]
    assert names == [sub.name for _, sub in SUBS]


def test_check_summary_only_while_a_follow_up_is_open():
    state, log = flow.new_conversation(RUBRIC, "test-id", "2026-10-15T00:00:00+00:00")
    assert flow.check_summary(RUBRIC, state) is None

    flow.handle_answer(RUBRIC, state, log, "first answer")  # follow-up asked
    summary = flow.check_summary(RUBRIC, state)
    first = SUBS[0][1]
    assert summary == {
        "subdimension": first.name,
        "covered": 0,
        "total": len(first.criteria),
        "follow_up_criterion": first.criteria[0].description,
    }

    flow.handle_answer(RUBRIC, state, log, "second answer")  # scored, next subdimension
    assert flow.check_summary(RUBRIC, state) is None
