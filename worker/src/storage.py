"""Reads and writes the conversation state and the log in D1.

Both are stored as JSON text, one row per conversation. `db` is the D1
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


async def load_log(db, conversation_id: str) -> dict | None:
    """Only used to add the next subdimension to the log; the chatbot never reads it."""
    row = await db.prepare("SELECT log FROM logs WHERE conversation_id = ?").bind(conversation_id).first()
    if row is None:
        return None
    return json.loads(row["log"])
