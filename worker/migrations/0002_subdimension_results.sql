-- subdimension_results: one row per scored subdimension. Holds the level
-- (the report is calculated from these) and the subdimension's entry of the
-- conversation log (for the author). The whole log of a conversation is
-- the header in `logs` plus these rows; scripts/show_log.py puts them
-- together. Written only by the background score, after a dimension has
-- ended, so it never collides with the answer requests that write
-- `conversations`. (Comments were added after this migration was applied;
-- they do not change the table.)
CREATE TABLE subdimension_results (
  -- The conversation this result belongs to. Must exist in `conversations`
  -- (foreign key), so a result can never belong to an unknown conversation.
  conversation_id TEXT NOT NULL REFERENCES conversations (id),
  -- Order of the subdimension in the conversation (0, 1, 2, ...); used to
  -- print the log in the order the questions were asked.
  position        INTEGER NOT NULL,
  -- Name of the dimension, as in the rubric (e.g. "Data").
  dimension       TEXT NOT NULL,
  -- Name of the subdimension, as in the rubric (e.g. "Discoverability").
  subdimension    TEXT NOT NULL,
  -- The score of the subdimension, a whole number from 0 to 5. Never shown
  -- to the respondent; the report shows only dimension and overall scores.
  level           INTEGER NOT NULL,
  -- The log entry as JSON: core question, the other subdimensions given as
  -- context, the transcript of this subdimension, every check (message
  -- type, status and quote per criterion, follow-up, reply, next question,
  -- what the code did), the counters, the full score (level, evidence per
  -- criterion, reason, why not higher, flags) and the `review` block the
  -- author fills in by hand.
  entry           TEXT NOT NULL,
  -- When the score was saved (UTC, ISO 8601).
  created_at      TEXT NOT NULL,
  -- One result per subdimension and conversation: if the background score
  -- and the report's fallback both finish, the first saved result is kept.
  PRIMARY KEY (conversation_id, subdimension)
);
