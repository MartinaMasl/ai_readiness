"""The database rules, checked on an in-memory SQLite database built from
the migration files (D1 is SQLite)."""

import sqlite3
from pathlib import Path

import pytest

MIGRATIONS = sorted((Path(__file__).parent.parent / "migrations").glob("*.sql"))
INSERT = ("INSERT INTO conversations (id, state, created_at, updated_at, respondent_code, format) "
          "VALUES (?, '{}', 't', 't', ?, ?)")


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    connection.execute("INSERT INTO respondents (code, role, token, created_at, format_order) "
                       "VALUES ('T01', 'technical', 'secret', 't', 'chatbot_first')")
    return connection


def test_one_assessment_per_respondent_and_format(db):
    db.execute(INSERT, ("a", "T01", "chatbot"))
    db.execute(INSERT, ("q", "T01", "questionnaire"))  # the other format is fine
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(INSERT, ("b", "T01", "chatbot"))


def test_a_reset_excludes_rows_instead_of_deleting_them(db):
    db.execute(INSERT, ("a", "T01", "chatbot"))
    db.execute("UPDATE conversations SET excluded = 1 WHERE id = 'a'")
    db.execute(INSERT, ("b", "T01", "chatbot"))  # allowed again after the reset
    assert db.execute("SELECT id, excluded FROM conversations ORDER BY id").fetchall() == [("a", 1), ("b", 0)]


def test_one_counting_comparison_answer_per_question(db):
    upsert = ("INSERT INTO comparison_responses (respondent_code, question_id, answer, created_at) "
              "VALUES ('T01', 'C1', ?, 't') ON CONFLICT (respondent_code, question_id) WHERE excluded = 0 "
              "DO UPDATE SET answer = excluded.answer")
    db.execute(upsert, ("1",))
    db.execute(upsert, ("3",))  # answering again replaces the answer
    db.execute("UPDATE comparison_responses SET excluded = 1")  # reset
    db.execute(upsert, ("4",))
    assert db.execute("SELECT answer, excluded FROM comparison_responses ORDER BY excluded").fetchall() == [
        ("4", 0), ("3", 1)]


def test_only_known_formats_orders_and_roles(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(INSERT, ("x", "T01", "survey"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE respondents SET format_order = 'random'")
