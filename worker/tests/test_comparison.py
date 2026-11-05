import asyncio
import copy
import json

import pytest

import comparison
import storage
from fake_d1 import FakeD1

CONTENT = comparison.load_comparison()
ITEMS = {item["id"]: item for section in CONTENT["sections"] for item in section["items"]}


def broken(tmp_path, change):
    content = copy.deepcopy(CONTENT)
    change(content)
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(content))
    return path


@pytest.mark.parametrize("change", [
    lambda c: c.pop("version"),                                              # no version
    lambda c: c["sections"][0].update(id="something"),                        # no "chatbot" section
    lambda c: c["sections"][1]["items"][0].update(id="C1"),                   # a question id used twice
    lambda c: c["sections"][0]["items"][0].update(type="slider"),             # unknown type
    lambda c: c["sections"][2]["items"][0].update(options=["Only one"]),      # too few options
])
def test_a_broken_file_is_refused_when_the_worker_starts(tmp_path, change):
    with pytest.raises(ValueError):
        comparison.load_comparison(broken(tmp_path, change))


def test_the_current_file_is_valid_and_both_format_sections_exist():
    assert {"chatbot", "questionnaire"} <= {s["id"] for s in CONTENT["sections"]}


def first_of_type(kind):
    """The first question of this type in the current comparison.json."""
    return next(item for item in ITEMS.values() if item["type"] == kind)


def test_answers_are_checked_by_type():
    scale, choice, text = first_of_type("scale"), first_of_type("choice"), first_of_type("text")
    last_scale = len(scale["scale"]) - 1
    assert comparison.check_answer(scale, last_scale) == str(last_scale)
    assert comparison.check_answer(choice, 0) == "0"
    assert comparison.check_answer(text, "  Some words  ") == "Some words"
    assert comparison.check_answer(text, "   ") is None and comparison.check_answer(scale, None) is None
    for bad_item, bad_value in [(scale, last_scale + 1), (scale, -1), (choice, True), (choice, "1"), (text, 3),
                                (text, "x" * (comparison.MAX_TEXT + 1))]:
        with pytest.raises(ValueError):
            comparison.check_answer(bad_item, bad_value)


def test_required_questions_and_resume():
    sections = comparison.for_respondent(CONTENT, "questionnaire_first")
    assert [s["id"] for s in sections][:2] == ["questionnaire", "chatbot"]
    required = lambda section: {i["id"]: "0" for i in section["items"] if not i.get("optional")}  # noqa: E731
    assert comparison.resume_at(sections, {}) == 0
    first_done = required(sections[0])
    assert comparison.resume_at(sections, first_done) == 1
    all_done = {k: v for section in sections for k, v in required(section).items()}  # optional ones left out
    assert comparison.resume_at(sections, all_done) == len(sections)


# --- Storage, on the stand-in for D1 -------------------------------------------

@pytest.fixture
def db():
    d1 = FakeD1()
    d1.connection.execute("INSERT INTO respondents (code, role, token, created_at, format_order) "
                          "VALUES ('T01', 'technical', 'secret', 't', 'chatbot_first')")
    return d1


def run(coroutine):
    return asyncio.run(coroutine)


def test_answers_are_stored_with_the_content_version_and_replace_earlier_answers(db):
    run(storage.save_comparison_answer(db, "T01", "C1", "1", "0.1-placeholder"))
    run(storage.save_comparison_answer(db, "T01", "C1", "3", "0.2"))
    assert run(storage.comparison_answers(db, "T01")) == {"C1": "3"}
    rows = db.connection.execute("SELECT answer, content_version FROM comparison_responses").fetchall()
    assert [tuple(r) for r in rows] == [("3", "0.2")]


def test_excluded_answers_are_not_read_and_submission_is_stored_once(db):
    run(storage.save_comparison_answer(db, "T01", "C1", "1", "0.1"))
    db.connection.execute("UPDATE comparison_responses SET excluded = 1")
    assert run(storage.comparison_answers(db, "T01")) == {}
    run(storage.set_feedback_submitted(db, "T01"))
    first = db.connection.execute("SELECT feedback_submitted_at FROM respondents").fetchone()[0]
    run(storage.set_feedback_submitted(db, "T01"))
    assert db.connection.execute("SELECT feedback_submitted_at FROM respondents").fetchone()[0] == first
