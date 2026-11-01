-- The first two tables. Comments describe what each table and column is
-- for; see also storage.py, which is the only code that reads and writes
-- them. (Comments were added after this migration was applied; they do
-- not change the tables.)

-- conversations: the chatbot's working memory, one row per conversation.
-- Cloudflare Workers forget everything between requests, so every answer
-- loads this row, runs one step of the flow (flow.py) and saves it again.
-- Written only by the answer requests.
CREATE TABLE conversations (
  -- Random id (UUID) of the conversation, created when it starts. The same
  -- id connects the rows in `logs` and `subdimension_results`, and is the
  -- id in the report link (result.html?id=...).
  id         TEXT PRIMARY KEY,
  -- The state as JSON: the dimensions covered, the current subdimension,
  -- the counters (follow-ups, clarifications, redirects), the whole
  -- transcript, the checks of the current subdimension, the finished
  -- subdimensions waiting to be scored, and whether the conversation is done.
  state      TEXT NOT NULL,
  -- When the conversation started (UTC, ISO 8601).
  created_at TEXT NOT NULL,
  -- When the state was last saved, i.e. the respondent's last answer (UTC).
  updated_at TEXT NOT NULL
);

-- logs: the header of the conversation log, one row per conversation.
-- The log is the author's research record of why each score was given; the
-- chatbot never reads it back. Since migration 0002 this row holds only the
-- header; the entries per subdimension are in `subdimension_results`.
-- Written once, when the conversation starts.
CREATE TABLE logs (
  -- The conversation this header belongs to (= conversations.id).
  conversation_id TEXT PRIMARY KEY,
  -- The header as JSON: conversation id, start time, model id and settings
  -- (temperature), rubric version, and the versions of the check and score
  -- prompts.
  log             TEXT NOT NULL,
  -- When the header was saved (UTC, ISO 8601).
  updated_at      TEXT NOT NULL
);
