"""Reads and writes the conversation state, the log and the results in D1.

Five tables (see migrations/ for every column):
- respondents: one row per invited person (code, role, link token, format
  order, consent), created by the author with scripts/add_respondent.py
- comparison_responses: the comparison questionnaire's answers
- conversations: the state, one row per conversation, written by every answer
- logs: the log header, one row per conversation, written at the start
- subdimension_results: one row per scored subdimension (level and log
  entry), written by the background score

Each table has one kind of writer, so a background score and an answer
never overwrite each other. JSON is stored as text. `db` is the D1
binding from wrangler.jsonc (env.DB).
"""

import json
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def load_state(db, conversation_id: str) -> dict | None:
    row = await db.prepare("SELECT state FROM conversations WHERE id = ?").bind(conversation_id).first()
    if row is None:
        return None
    return json.loads(row["state"])


RESPONDENT_COLUMNS = ["code", "role", "format_order", "consent_at", "feedback_submitted_at"]
ASSESSMENT_COLUMNS = ["id", "format", "respondent_code", "created_at", "finished_at",
                      "first_report_viewed_at", "excluded"]


def _row(row, columns: list[str]) -> dict:
    """A D1 row as a plain dict with these columns."""
    return {column: row[column] for column in columns}


async def find_respondent(db, token: str) -> dict | None:
    """The respondent whose personal link has this token, or None."""
    row = await db.prepare(
        f"SELECT {', '.join(RESPONDENT_COLUMNS)} FROM respondents WHERE token = ?"
    ).bind(token).first()
    return None if row is None else _row(row, RESPONDENT_COLUMNS)


async def set_consent(db, respondent_code: str) -> None:
    """Store when consent was given (only the first time)."""
    await db.prepare(
        "UPDATE respondents SET consent_at = ? WHERE code = ? AND consent_at IS NULL"
    ).bind(now(), respondent_code).run()


async def assessments(db, respondent_code: str) -> dict:
    """The respondent's counting assessments: {"chatbot": row or None,
    "questionnaire": row or None}. Excluded rows are left out."""
    result = await db.prepare(
        f"SELECT {', '.join(ASSESSMENT_COLUMNS)} FROM conversations "
        "WHERE respondent_code = ? AND excluded = 0"
    ).bind(respondent_code).all()
    rows = {"chatbot": None, "questionnaire": None}
    for row in result.results:
        rows[row["format"]] = _row(row, ASSESSMENT_COLUMNS)
    return rows


async def count_feedback_answers(db, respondent_code: str) -> int:
    row = await db.prepare(
        "SELECT COUNT(*) AS n FROM comparison_responses WHERE respondent_code = ? AND excluded = 0"
    ).bind(respondent_code).first()
    return row["n"]


async def comparison_answers(db, respondent_code: str) -> dict:
    """The respondent's comparison answers: {question id: answer}."""
    result = await db.prepare(
        "SELECT question_id, answer FROM comparison_responses WHERE respondent_code = ? AND excluded = 0"
    ).bind(respondent_code).all()
    return {row["question_id"]: row["answer"] for row in result.results}


async def save_comparison_answer(db, respondent_code: str, question_id: str, answer: str,
                                 content_version: str) -> None:
    """Save one comparison answer, replacing an earlier answer to the same
    question, together with the version of comparison.json it was given for."""
    await db.prepare(
        "INSERT INTO comparison_responses (respondent_code, question_id, answer, created_at, content_version) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (respondent_code, question_id) WHERE excluded = 0 "
        "DO UPDATE SET answer = excluded.answer, created_at = excluded.created_at, "
        "content_version = excluded.content_version"
    ).bind(respondent_code, question_id, answer, now(), content_version).run()


async def set_feedback_submitted(db, respondent_code: str) -> None:
    """Store when the comparison questionnaire was submitted (only the first time)."""
    await db.prepare(
        "UPDATE respondents SET feedback_submitted_at = ? WHERE code = ? AND feedback_submitted_at IS NULL"
    ).bind(now(), respondent_code).run()


async def assessment(db, conversation_id: str) -> dict | None:
    """One assessment's columns (format, respondent, dates), without its state."""
    row = await db.prepare(
        f"SELECT {', '.join(ASSESSMENT_COLUMNS)} FROM conversations WHERE id = ?"
    ).bind(conversation_id).first()
    return None if row is None else _row(row, ASSESSMENT_COLUMNS)


async def create_conversation(db, conversation_id: str, state: dict, respondent_code: str,
                              format: str = "chatbot") -> None:
    """Save a new assessment, linked to the respondent whose link started it.
    The database allows only one counting row per respondent and format."""
    timestamp = now()
    await db.prepare(
        "INSERT INTO conversations (id, state, created_at, updated_at, respondent_code, format) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    ).bind(conversation_id, json.dumps(state), timestamp, timestamp, respondent_code, format).run()


async def set_finished(db, conversation_id: str) -> None:
    """Store when the assessment was finished (only the first time)."""
    await db.prepare(
        "UPDATE conversations SET finished_at = ? WHERE id = ? AND finished_at IS NULL"
    ).bind(now(), conversation_id).run()


async def set_report_viewed(db, conversation_id: str) -> None:
    """Store when the report page was first opened (only the first time)."""
    await db.prepare(
        "UPDATE conversations SET first_report_viewed_at = ? WHERE id = ? AND first_report_viewed_at IS NULL"
    ).bind(now(), conversation_id).run()


async def save_state(db, conversation_id: str, state: dict) -> None:
    timestamp = now()
    await db.prepare(
        "INSERT INTO conversations (id, state, created_at, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at"
    ).bind(conversation_id, json.dumps(state), timestamp, timestamp).run()


async def save_log(db, conversation_id: str, log: dict) -> None:
    await db.prepare(
        "INSERT INTO logs (conversation_id, log, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (conversation_id) DO UPDATE SET log = excluded.log, updated_at = excluded.updated_at"
    ).bind(conversation_id, json.dumps(log), now()).run()


async def load_log(db, conversation_id: str) -> dict | None:
    """The log header, e.g. to add the questionnaire's raw answers when it is
    finished. The chatbot flow never reads the log."""
    row = await db.prepare("SELECT log FROM logs WHERE conversation_id = ?").bind(conversation_id).first()
    return None if row is None else json.loads(row["log"])


async def save_result(db, conversation_id: str, finished: dict, entry: dict) -> None:
    """Save a scored subdimension. If it is already saved (the report's
    fallback and the background score both finished), keep the first."""
    await db.prepare(
        "INSERT INTO subdimension_results "
        "(conversation_id, position, dimension, subdimension, level, entry, created_at, insufficient_evidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (conversation_id, subdimension) DO NOTHING"
    ).bind(
        conversation_id, finished["position"], finished["dimension"], finished["subdimension"],
        entry["score"]["level"], json.dumps(entry), now(),
        1 if "insufficient_evidence" in entry["score"]["flags"] else 0,
    ).run()


async def load_levels(db, conversation_id: str) -> dict[str, dict[str, int]]:
    """The saved levels: {dimension: {subdimension: level}}."""
    result = await db.prepare(
        "SELECT dimension, subdimension, level FROM subdimension_results WHERE conversation_id = ?"
    ).bind(conversation_id).all()
    levels = {}
    for row in result.results:
        levels.setdefault(row["dimension"], {})[row["subdimension"]] = row["level"]
    return levels
