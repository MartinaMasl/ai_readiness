import json
from pathlib import Path

import pytest

import study

CONSENT = "2026-11-03T10:00:00+00:00"


def respondent(order="chatbot_first", consent=CONSENT, submitted=None):
    return {"format_order": order, "consent_at": consent, "feedback_submitted_at": submitted}


def row(finished=False):
    return {"id": "x", "finished_at": "2026-11-03T11:00:00+00:00" if finished else None}


def statuses(step_list):
    return [(s["key"], s["status"]) for s in step_list]


def test_new_respondent_without_consent_can_start_nothing():
    s = study.steps(respondent(consent=None), {}, 0)
    assert statuses(s) == [("chatbot", "locked"), ("questionnaire", "locked"), ("feedback", "locked")]
    assert s[0]["reason"] == study.NEED_CONSENT
    assert not study.can_start(s, "chatbot")


def test_after_consent_only_the_first_assessment_is_open():
    s = study.steps(respondent(), {}, 0)
    assert statuses(s) == [("chatbot", "not_started"), ("questionnaire", "locked"), ("feedback", "locked")]
    assert s[1]["reason"] == study.NEED_FIRST and s[2]["reason"] == study.NEED_BOTH
    assert study.can_start(s, "chatbot") and not study.can_start(s, "questionnaire")


def test_first_in_progress_keeps_the_second_locked():
    s = study.steps(respondent(), {"chatbot": row()}, 0)
    assert statuses(s)[:2] == [("chatbot", "in_progress"), ("questionnaire", "locked")]
    assert study.can_start(s, "chatbot")  # "Continue"


def test_first_finished_unlocks_the_second():
    s = study.steps(respondent(), {"chatbot": row(finished=True)}, 0)
    assert statuses(s) == [("chatbot", "completed"), ("questionnaire", "not_started"), ("feedback", "locked")]
    assert not study.can_start(s, "chatbot")  # a finished assessment is final


def test_both_finished_unlocks_feedback_then_in_progress_then_completed():
    rows = {"chatbot": row(finished=True), "questionnaire": row(finished=True)}
    assert statuses(study.steps(respondent(), rows, 0))[2] == ("feedback", "not_started")
    assert statuses(study.steps(respondent(), rows, 3))[2] == ("feedback", "in_progress")
    done = study.steps(respondent(submitted="2026-11-03T12:00:00+00:00"), rows, 5)
    assert statuses(done)[2] == ("feedback", "completed")
    assert not study.can_start(done, "feedback")


def test_questionnaire_first_reverses_the_order():
    s = study.steps(respondent("questionnaire_first"), {"questionnaire": row(finished=True)}, 0)
    assert [(x["key"], x["position"]) for x in s] == [("questionnaire", 1), ("chatbot", 2), ("feedback", 3)]
    assert statuses(s)[:2] == [("questionnaire", "completed"), ("chatbot", "not_started")]
    # The chatbot is locked while the questionnaire (first) is not finished.
    assert statuses(study.steps(respondent("questionnaire_first"), {}, 0))[1] == ("chatbot", "locked")


def test_comparison_sections_follow_the_order_of_the_assessments():
    content = json.loads((Path(__file__).parent.parent / "src" / "comparison.json").read_text())
    ids = lambda order: [s["id"] for s in study.comparison_sections(content, order)]  # noqa: E731
    assert ids("chatbot_first") == ["chatbot", "questionnaire", "compare"]
    assert ids("questionnaire_first") == ["questionnaire", "chatbot", "compare"]


@pytest.mark.parametrize("existing, expected", [
    ([], "chatbot_first"),
    (["chatbot_first"], "questionnaire_first"),
    (["chatbot_first", "questionnaire_first"], "chatbot_first"),
    (["chatbot_first", "questionnaire_first", "chatbot_first"], "questionnaire_first"),
])
def test_format_order_alternates(existing, expected):
    assert study.next_format_order(existing) == expected
