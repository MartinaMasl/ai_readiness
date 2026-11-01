-- respondents: one row per person invited to the chatbot. The chatbot can
-- only be started with a personal link (/?r=<token>), and every
-- conversation is linked to the respondent whose link was used. Only a
-- pseudonymous code and the role are stored; the author keeps the list
-- that connects codes to names outside the database. Rows are created by
-- the author with scripts/add_respondent.py.
CREATE TABLE respondents (
  -- Pseudonymous code chosen by the author, e.g. "R01". Shown in the logs
  -- and used to connect a person's conversations and scores.
  code       TEXT PRIMARY KEY,
  -- "technical" or "business", set by the author when creating the link,
  -- so results can be compared between the two groups.
  role       TEXT NOT NULL CHECK (role IN ('technical', 'business')),
  -- Long random secret in the personal link. Nobody can guess another
  -- person's link from their own.
  token      TEXT NOT NULL UNIQUE,
  -- When the link was created (UTC, ISO 8601).
  created_at TEXT NOT NULL
);

-- The respondent whose link started the conversation (= respondents.code).
-- Empty for conversations from before personal links existed.
ALTER TABLE conversations ADD COLUMN respondent_code TEXT REFERENCES respondents (code);
