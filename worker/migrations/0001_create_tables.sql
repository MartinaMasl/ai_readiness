-- Conversation state: what the flow needs to continue with the next answer.
-- Loaded at the start of every request and saved at the end.
CREATE TABLE conversations (
  id         TEXT PRIMARY KEY,
  state      TEXT NOT NULL,  -- JSON
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Conversation log for the author: why each score was given.
-- Written after every subdimension. The chatbot never reads it back.
CREATE TABLE logs (
  conversation_id TEXT PRIMARY KEY,
  log             TEXT NOT NULL,  -- JSON
  updated_at      TEXT NOT NULL
);
