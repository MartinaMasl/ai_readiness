import asyncio

import flow
from fakes import FakeModel
from report import build_report
from rubric import load_rubric, only_dimensions

RUBRIC = load_rubric()
RULES = RUBRIC.rules
SUBS = flow.all_subdimensions(RUBRIC)


def start(rubric=RUBRIC):
    state, log = flow.new_conversation(rubric, "test-id", "2026-10-15T00:00:00+00:00", FakeModel().info())
    # Stand-ins for the subdimension_results table: the log entries and the levels.
    log["subdimensions"] = []
    log["levels"] = {}
    return state, log


def say(state, log, text, rubric=RUBRIC, model=None):
    """One answer. Like main.py, when a dimension has ended its
    subdimensions are scored and their results kept. Returns (message,
    done, the subdimension was finished)."""
    model = model or FakeModel()
    finished_before = len(state["finished"])
    message, done, to_score = asyncio.run(flow.handle_answer(rubric, state, text, model))
    for finished in to_score:
        entry = asyncio.run(flow.score_subdimension(rubric, finished, model, to_score))
        log["subdimensions"].append(entry)
        log["levels"].setdefault(finished["dimension"], {})[finished["subdimension"]] = entry["score"]["level"]
    return message, done, len(state["finished"]) > finished_before


def finish_subdimension(state, log, text, model=None):
    """Answer with the same text until the current subdimension is finished."""
    while not say(state, log, text, model=model)[2]:
        pass


def run_whole_conversation(rubric=RUBRIC):
    state, log = start(rubric)
    replies = []
    while not state["done"]:
        replies.append(say(state, log, "an answer", rubric))
    return state, log, replies


def test_starts_with_intro_and_first_opening_question():
    state, header = flow.new_conversation(RUBRIC, "test-id", "2026-10-15T00:00:00+00:00", FakeModel().info())
    assert state["transcript"][0]["text"] == flow.INTRO
    assert state["transcript"][1]["text"] == SUBS[0][1].opening_question
    assert set(header) == {"id", "started_at", "model", "rubric_version", "prompt_versions"}
    assert header["rubric_version"] == RUBRIC.version


def test_first_answer_gets_a_follow_up_then_next_opening_question():
    state, log = start()
    message, done, log_changed = say(state, log, "first answer")
    assert message.startswith("[fake] Could you tell me a bit more?")
    assert (done, log_changed) == (False, False)
    assert state["follow_ups"] == 1 and state["follow_ups_dimension"] == 1

    message, done, log_changed = say(state, log, "second answer")
    assert message == f"[fake next] {SUBS[1][1].opening_question}"
    assert (done, log_changed) == (False, True)


def test_whole_conversation_scores_every_subdimension():
    state, log, replies = run_whole_conversation()
    assert replies[-1] == (flow.CLOSING, True, True)
    for dimension_name, sub in SUBS:
        assert sub.name in log["levels"][dimension_name]
    build_report(RUBRIC, log["levels"])  # report works on the result


def follow_ups_per_dimension(log):
    """{dimension: [follow-ups per subdimension]} from the log."""
    result = {}
    for (dimension_name, _), entry in zip(SUBS, log["subdimensions"]):
        result.setdefault(dimension_name, []).append(entry["counters"]["follow_ups"])
    return result


def test_follow_ups_respect_both_limits():
    _, log, _ = run_whole_conversation()
    for follow_ups in follow_ups_per_dimension(log).values():
        assert all(n <= RULES.max_follow_ups for n in follow_ups)
        assert sum(follow_ups) <= RULES.max_follow_ups_per_dimension


def test_dimension_follow_up_budget_runs_out_and_starts_again():
    # The fake check wants a follow-up after every first answer, so within a
    # dimension the budget is used up after max_follow_ups_per_dimension
    # subdimensions; the next dimension starts with a new budget.
    _, log, replies = run_whole_conversation()
    budget_used = 0
    for dimension in RUBRIC.dimensions:
        follow_ups = follow_ups_per_dimension(log)[dimension.name]
        budget = min(RULES.max_follow_ups_per_dimension, len(dimension.subdimensions) * RULES.max_follow_ups)
        assert sum(follow_ups) == budget
        if len(dimension.subdimensions) * RULES.max_follow_ups > RULES.max_follow_ups_per_dimension:
            assert follow_ups[-1] == 0  # the last subdimension got none
        budget_used += budget
    assert len(replies) == len(SUBS) + budget_used


def test_respondent_question_is_answered_and_has_its_own_limit():
    state, log = start()
    first, second = SUBS[0][1], SUBS[1][1]
    for _ in range(RULES.max_clarifications):
        message, _, _ = say(state, log, "What do you mean?")
        assert message.startswith("[fake] The model would answer") and first.opening_question in message
    assert state["clarifications"] == RULES.max_clarifications
    assert state["follow_ups"] == 0 and state["redirects"] == 0  # other counters unchanged

    message, _, _ = say(state, log, "And what else?")
    assert message == f"[fake next] {second.opening_question}"  # limit reached: scored with what there is
    assert state["finished"][0]["counters"]["clarifications"] == RULES.max_clarifications


def test_off_topic_twice_is_redirected_then_scored():
    state, log = start()
    first, second = SUBS[0][1], SUBS[1][1]
    for _ in range(RULES.max_redirects):
        message, _, _ = say(state, log, "[off] Nice weather today.")
        assert message.startswith("[fake] The model would steer back") and first.opening_question in message
    assert state["redirects"] == RULES.max_redirects and state["subdimension_index"] == 0

    message, _, _ = say(state, log, "[off] Still the weather.")
    assert message == f"[fake next] {second.opening_question}"
    entry = state["finished"][0]
    assert [c["action"] for c in entry["checks"]] == ["redirect"] * RULES.max_redirects + ["score"]
    assert entry["counters"]["redirects"] == RULES.max_redirects


def test_dont_know_is_not_asked_again():
    state, log = start()
    message, _, log_changed = say(state, log, "I don't know, sorry.")
    assert message == f"[fake next] {SUBS[1][1].opening_question}"  # nothing left to ask: scored
    assert log_changed and state["follow_ups"] == 0
    check = state["finished"][0]["checks"][0]
    assert check["message_type"] == "dont_know"
    assert {c["status"] for c in check["criteria"].values()} == {"dont_know"}


def test_not_applicable_monitoring_is_logged_and_never_a_follow_up_target():
    state, log = start()
    say(state, log, "first answer")
    check = state["checks"][0]
    sub = SUBS[0][1]
    monitoring = next(c for c in sub.criteria if c.not_applicable_if)
    assert check["criteria"][monitoring.id]["status"] == "not_applicable"
    assert monitoring.id not in check["targets"]


def test_log_entry_has_the_agreed_shape():
    _, log, _ = run_whole_conversation()
    assert [e["subdimension"] for e in log["subdimensions"]] == [sub.name for _, sub in SUBS]
    entry = log["subdimensions"][0]
    assert set(entry) == {"subdimension", "core_question", "context", "transcript", "checks", "counters", "score", "review"}
    assert set(entry["counters"]) == {"follow_ups", "clarifications", "redirects", "follow_ups_dimension"}
    assert set(entry["checks"][0]) == {"message_type", "criteria", "follow_up_needed", "targets", "reply",
                                       "next_question", "action"}
    assert [m["role"] for m in entry["transcript"]] == ["chatbot", "respondent", "chatbot", "respondent"]
    assert entry["review"] == {"my_level": None, "error_type": None, "note": None}


def test_outline_lists_every_subdimension_in_order():
    names = [sub for dim in flow.outline(RUBRIC) for sub in dim["subdimensions"]]
    assert names == [sub.name for _, sub in SUBS]


def test_check_summary_for_the_test_mode_box():
    state, log = start()
    assert flow.check_summary(RUBRIC, state) is None

    say(state, log, "first answer")  # follow-up asked
    first = SUBS[0][1]
    summary = flow.check_summary(RUBRIC, state)
    assert summary["message_type"] == "answer" and summary["action"] == "follow_up"
    assert summary["total"] == len(first.criteria)
    assert summary["covered"] == len(first.criteria) - 2  # two criteria missing in the fake check
    assert summary["follow_up_criteria"] == [c.description for c in first.criteria[:2]]

    say(state, log, "second answer")  # scored, next subdimension
    assert flow.check_summary(RUBRIC, state) is None


def test_conversation_with_only_some_dimensions():
    data_only = only_dimensions(RUBRIC, ["Data"])
    state, log, replies = run_whole_conversation(data_only)
    assert state["dimensions"] == ["Data"]
    assert list(log["levels"]) == ["Data"]
    assert [e["subdimension"] for e in log["subdimensions"]] == [s.name for s in data_only.dimensions[0].subdimensions]
    report = build_report(data_only, log["levels"])
    assert [d["name"] for d in report["dimensions"]] == ["Data"]


def test_log_records_model_and_prompt_versions():
    _, log = start()
    assert log["model"] == {"id": "fake", "settings": {}}
    assert log["prompt_versions"] == {"check": None, "score": None}


def test_only_the_first_question_is_fixed_and_the_log_keeps_the_core_question():
    state, log, _ = run_whole_conversation()
    first, second = log["subdimensions"][0], log["subdimensions"][1]
    assert first["transcript"][0]["text"] == SUBS[0][1].opening_question  # fixed
    assert second["transcript"][0]["text"] == f"[fake next] {SUBS[1][1].opening_question}"  # written by the model
    assert second["core_question"] == SUBS[1][1].opening_question


def test_a_dimension_is_scored_as_a_whole_when_it_ends():
    state, log = flow.new_conversation(RUBRIC, "test-id", "2026-10-15T00:00:00+00:00", FakeModel().info())
    dimension = RUBRIC.dimensions[0]
    returned = []  # what each answer returns to be scored, until the dimension has ended
    while len(state["finished"]) < len(dimension.subdimensions):
        returned.append(asyncio.run(flow.handle_answer(RUBRIC, state, "an answer", FakeModel()))[2])
    assert all(to_score == [] for to_score in returned[:-1])  # nothing before the end
    last = returned[-1]
    assert [f["subdimension"] for f in last] == [s.name for s in dimension.subdimensions]
    assert all("score" not in f for f in last)  # scored afterwards, by score_subdimension
    assert [m["role"] for m in last[0]["transcript"]] == ["chatbot", "respondent", "chatbot", "respondent"]


def test_check_sees_earlier_and_score_sees_all_other_subdimensions_of_the_dimension():
    state, log = start()
    model = FakeModel()
    dimension = RUBRIC.dimensions[0]
    names = [s.name for s in dimension.subdimensions]
    for _ in names:
        finish_subdimension(state, log, "an answer", model=model)
    checks = {name: ctx for call, name, ctx in model.contexts if call == "check"}
    scores = {name: ctx for call, name, ctx in model.contexts if call == "score"}
    assert checks == {names[0]: [], names[1]: names[:1], names[2]: names[:2]}
    assert scores == {name: [n for n in names if n != name] for name in names}
    assert log["subdimensions"][0]["context"] == names[1:]
    # The next dimension starts without context.
    say(state, log, "an answer", model=model)
    assert model.contexts[-1] == ("check", RUBRIC.dimensions[1].subdimensions[0].name, [])


def test_unscored_lists_finished_subdimensions_without_a_level():
    state, log = start()
    for _ in RUBRIC.dimensions[0].subdimensions:
        say(state, log, "I don't know")  # finished at once; the dimension is scored at its end
    assert flow.unscored(state, log["levels"]) == []
    first_dimension = SUBS[0][0]
    only_first = {first_dimension: {SUBS[0][1].name: 4}}
    assert [f["subdimension"] for f in flow.unscored(state, only_first)] == [s.name for _, s in SUBS[1:3]]
