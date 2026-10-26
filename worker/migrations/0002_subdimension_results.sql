-- One row per scored subdimension, written by the background score.
-- Holds the level (for the report) and the subdimension's log entry
-- (for the author). The conversation log is the header in `logs` plus
-- these rows, connected by the conversation id.
CREATE TABLE subdimension_results (
  conversation_id TEXT NOT NULL REFERENCES conversations (id),
  position        INTEGER NOT NULL,  -- order in the conversation (0, 1, 2, ...)
  dimension       TEXT NOT NULL,
  subdimension    TEXT NOT NULL,
  level           INTEGER NOT NULL,
  entry           TEXT NOT NULL,     -- JSON: core question, transcript, checks, counters, score, review
  created_at      TEXT NOT NULL,
  PRIMARY KEY (conversation_id, subdimension)
);
