-- The study: every respondent does two assessments (chatbot and
-- questionnaire) in the order set by format_order, sees a report after
-- each, then compares both reports and answers a short comparison
-- questionnaire. See study.py for the rules that use these columns.

-- respondents --------------------------------------------------------------

-- Which assessment the respondent does first. Assigned alternately within
-- each role (scripts/add_respondent.py), so that in both roles about half
-- start with each format and the order effect can be balanced out.
ALTER TABLE respondents ADD COLUMN format_order TEXT
  CHECK (format_order IN ('chatbot_first', 'questionnaire_first'));

-- When the respondent gave consent on the home page (UTC, ISO 8601). Empty
-- until then; both assessments stay locked without consent.
ALTER TABLE respondents ADD COLUMN consent_at TEXT;

-- When the respondent submitted the comparison questionnaire, i.e. the
-- moment the whole study is complete for them.
ALTER TABLE respondents ADD COLUMN feedback_submitted_at TEXT;

-- conversations --------------------------------------------------------------
-- One row per assessment: a chatbot conversation or a questionnaire.

-- Which assessment this row is. All rows from before the study are chatbot
-- conversations.
ALTER TABLE conversations ADD COLUMN format TEXT NOT NULL DEFAULT 'chatbot'
  CHECK (format IN ('chatbot', 'questionnaire'));

-- When the assessment was finished (UTC). Empty while it is in progress.
ALTER TABLE conversations ADD COLUMN finished_at TEXT;

-- When the respondent first opened this assessment's report page
-- (result.html), to see whether they saw the first report before starting
-- the second assessment. The compare page does not count.
ALTER TABLE conversations ADD COLUMN first_report_viewed_at TEXT;

-- 1 = left out of the study, for example a test respondent's earlier
-- attempt after a reset (scripts/add_respondent.py --reset). Rows are
-- never deleted; excluded rows are ignored by the study and the exports.
ALTER TABLE conversations ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0
  CHECK (excluded IN (0, 1));

-- Finished conversations from before this migration: finished when last saved.
UPDATE conversations SET finished_at = updated_at
  WHERE finished_at IS NULL AND json_extract(state, '$.done') = 1;

-- At most one counting row per respondent and format. Excluded rows and
-- rows without a respondent (from before personal links) do not count.
CREATE UNIQUE INDEX one_assessment_per_format
  ON conversations (respondent_code, format)
  WHERE excluded = 0 AND respondent_code IS NOT NULL;

-- subdimension_results --------------------------------------------------------

-- 1 when the score has the flag insufficient_evidence (chatbot) or the
-- answer was "I don't know" (questionnaire). Until now the flag was only
-- inside `entry`; as a column it can be counted directly in the analysis.
ALTER TABLE subdimension_results ADD COLUMN insufficient_evidence INTEGER NOT NULL DEFAULT 0
  CHECK (insufficient_evidence IN (0, 1));

-- Existing rows: take the flag from the score in `entry`.
UPDATE subdimension_results SET insufficient_evidence = 1
  WHERE EXISTS (SELECT 1 FROM json_each(entry, '$.score.flags') WHERE value = 'insufficient_evidence');

-- comparison_responses -------------------------------------------------------

-- comparison_responses: the answers to the comparison questionnaire
-- (content in src/comparison.json), one row per respondent and question,
-- saved after each section. Answering again replaces the earlier answer.
CREATE TABLE comparison_responses (
  -- The respondent who answered (= respondents.code).
  respondent_code TEXT NOT NULL REFERENCES respondents (code),
  -- The question's id in comparison.json (e.g. "C1", "X3").
  question_id     TEXT NOT NULL,
  -- The answer: the index of the chosen option for scale and choice
  -- questions (0 = first option), the text for text questions.
  answer          TEXT NOT NULL,
  -- When the answer was saved (UTC, ISO 8601).
  created_at      TEXT NOT NULL,
  -- 1 = left out after a reset of a test respondent; never deleted.
  excluded        INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1))
);

-- One counting answer per respondent and question. This replaces a primary
-- key on (respondent_code, question_id): with a primary key, a reset test
-- respondent could not answer again without deleting the old answers.
CREATE UNIQUE INDEX one_answer_per_question
  ON comparison_responses (respondent_code, question_id)
  WHERE excluded = 0;
