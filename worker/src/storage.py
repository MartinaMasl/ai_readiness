"""Reads and writes the conversation state, the log and the results in D1.

Three tables, connected by the conversation id (see migrations/):
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


async def save_result(db, conversation_id: str, finished: dict, entry: dict) -> None:
    """Save a scored subdimension. If it is already saved (the report's
    fallback and the background score both finished), keep the first."""
    await db.prepare(
        "INSERT INTO subdimension_results "
        "(conversation_id, position, dimension, subdimension, level, entry, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (conversation_id, subdimension) DO NOTHING"
    ).bind(
        conversation_id, finished["position"], finished["dimension"], finished["subdimension"],
        entry["score"]["level"], json.dumps(entry), now(),
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
