import asyncio
import json

import pytest

import questionnaire
import storage
from fake_d1 import FakeD1
from report import build_report
from rubric import load_rubric, only_dimensions

RUBRIC = load_rubric()
CONTENT = questionnaire.load_questionnaire(RUBRIC)
QUESTIONS = questionnaire.items(CONTENT, RUBRIC)


def option_with_value(item, value):
    return next(i for i, o in enumerate(item["options"]) if o["value"] == value and not o.get("dont_know"))


def dont_know(item):
    return next(i for i, o in enumerate(item["options"]) if o.get("dont_know"))


def answers(choices):
    """{item id: answer} with option indexes from choices[item id]."""
    return {item_id: {"option": option, "first_answered_at": "t1", "answered_at": "t2"}
            for item_id, option in choices.items()}


def test_questionnaire_matches_the_rubric():
    assert len(QUESTIONS) == sum(len(d.subdimensions) for d in RUBRIC.dimensions)
    assert [(q["dimension"], q["subdimension"]) for q in QUESTIONS] == [
        (d.name, s.name) for d in RUBRIC.dimensions for s in d.subdimensions]


def test_a_questionnaire_that_does_not_fit_the_rubric_is_refused(tmp_path):
    broken = json.loads(questionnaire.QUESTIONNAIRE_PATH.read_text())
    broken["dimensions"][0]["subdimensions"][0]["name"] = "Something else"
    path = tmp_path / "q.json"
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        questionnaire.load_questionnaire(RUBRIC, path)


def test_the_chosen_option_is_the_score_and_dont_know_scores_0_with_the_flag():
    item = QUESTIONS[0]["item"]
    for value in range(6):
        assert questionnaire.score(item, option_with_value(item, value)) == (value, False)
    assert questionnaire.score(item, dont_know(item)) == (0, True)


def test_dimensions_setting_limits_the_questions():
    data_only = questionnaire.items(CONTENT, only_dimensions(RUBRIC, ["Data"]))
    assert {q["dimension"] for q in data_only} == {"Data"} and len(data_only) == 3


def test_continue_resumes_at_the_first_unanswered_question():
    ids = [q["item"]["id"] for q in QUESTIONS]
    assert questionnaire.first_unanswered(QUESTIONS, {}) == 0
    assert questionnaire.first_unanswered(QUESTIONS, answers({ids[0]: 1, ids[2]: 1})) == 1  # a gap is found first
    assert questionnaire.first_unanswered(QUESTIONS, answers({i: 1 for i in ids})) == len(ids)


def test_the_page_never_gets_the_scores():
    page = json.dumps(questionnaire.for_page(QUESTIONS))
    assert '"value"' not in page


def test_results_give_one_row_per_subdimension_and_the_same_report_code_works():
    data = questionnaire.items(CONTENT, only_dimensions(RUBRIC, ["Data"]))
    chosen = {data[0]["item"]["id"]: option_with_value(data[0]["item"], 4),
              data[1]["item"]["id"]: option_with_value(data[1]["item"], 2),
              data[2]["item"]["id"]: dont_know(data[2]["item"])}
    pairs = questionnaire.results(data, answers(chosen))
    assert [(f["subdimension"], e["score"]["level"], e["score"]["flags"]) for f, e in pairs] == [
        ("Discoverability", 4, []), ("Ownership and Stewardship", 2, []), ("Quality", 0, ["insufficient_evidence"])]
    levels = {"Data": {f["subdimension"]: e["score"]["level"] for f, e in pairs}}
    report = build_report(only_dimensions(RUBRIC, ["Data"]), levels)
    assert report["dimensions"][0]["score_display"] == "2.00"  # (4 + 2 + 0) / 3, as for the chatbot


# --- Storage, on the stand-in for D1 -------------------------------------------

def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def db():
    d1 = FakeD1()
    d1.connection.execute("INSERT INTO respondents (code, role, token, created_at, format_order) "
                          "VALUES ('T01', 'technical', 'secret', 't', 'chatbot_first')")
    run(storage.create_conversation(d1, "chat", {}, "T01", "chatbot"))
    run(storage.create_conversation(d1, "quest", {}, "T01", "questionnaire"))
    return d1


def save(db, conversation_id, subdimension, level):
    finished = {"position": 0, "dimension": "Data", "subdimension": subdimension}
    run(storage.save_result(db, conversation_id, finished, {"score": {"level": level, "flags": []}}))


def test_each_report_reads_only_its_own_assessments_scores(db):
    save(db, "chat", "Discoverability", 1)
    save(db, "quest", "Discoverability", 5)
    assert run(storage.load_levels(db, "chat")) == {"Data": {"Discoverability": 1}}
    assert run(storage.load_levels(db, "quest")) == {"Data": {"Discoverability": 5}}


def test_assessments_are_found_per_format_and_excluded_rows_do_not_count(db):
    rows = run(storage.assessments(db, "T01"))
    assert rows["chatbot"]["id"] == "chat" and rows["questionnaire"]["id"] == "quest"
    db.connection.execute("UPDATE conversations SET excluded = 1 WHERE id = 'chat'")
    assert run(storage.assessments(db, "T01"))["chatbot"] is None


def test_finished_and_first_report_view_are_stored_only_the_first_time(db):
    run(storage.set_finished(db, "quest"))
    run(storage.set_report_viewed(db, "quest"))
    first = run(storage.assessment(db, "quest"))
    run(storage.set_finished(db, "quest"))
    run(storage.set_report_viewed(db, "quest"))
    again = run(storage.assessment(db, "quest"))
    assert first["finished_at"] and first["first_report_viewed_at"]
    assert (again["finished_at"], again["first_report_viewed_at"]) == (first["finished_at"], first["first_report_viewed_at"])


def test_insufficient_evidence_is_stored_as_a_column(db):
    finished = {"position": 2, "dimension": "Data", "subdimension": "Quality"}
    run(storage.save_result(db, "quest", finished, {"score": {"level": 0, "flags": ["insufficient_evidence"]}}))
    flag = db.connection.execute("SELECT insufficient_evidence FROM subdimension_results").fetchone()[0]
    assert flag == 1
