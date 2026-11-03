-- The version of comparison.json (its "version" field) that was shown when
-- an answer was given. Answers to scale and choice questions are stored as
-- the index of the chosen option, so if the content changes after some
-- respondents have answered, this tells which wording an answer belongs to.
ALTER TABLE comparison_responses ADD COLUMN content_version TEXT NOT NULL DEFAULT '';
