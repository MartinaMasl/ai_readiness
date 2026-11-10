"""Loads and validates the rubric file.

The rubric is the single source of the methodology texts. Code reads it
as it is and never rewrites it.
"""

from pathlib import Path

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# rubric.json sits next to this file. wrangler.jsonc bundles it into the Worker.
RUBRIC_PATH = Path(__file__).parent / "rubric.json"

LEVELS = [0, 1, 2, 3, 4, 5]


def _check_all_levels(value: dict) -> dict:
    """A level table must describe exactly the levels 0 to 5."""
    if sorted(value) != LEVELS:
        raise ValueError(f"levels must be exactly {LEVELS}, got {sorted(value)}")
    return value


class RubricPart(BaseModel):
    """Every part of the rubric refuses keys it does not know, so a new rule
    or a misspelt key stops the Worker at startup instead of being ignored."""
    model_config = ConfigDict(extra="forbid")


class Rules(RubricPart):
    score_values: str
    max_follow_ups: int  # per subdimension
    max_follow_ups_per_dimension: int
    max_redirects: int  # off-topic messages steered back, per subdimension
    max_clarifications: int  # respondent questions answered, per subdimension
    follow_up_purpose: str
    mixed_evidence: str
    plans: str
    insufficient_evidence: str
    not_applicable: str
    language: str  # how questions and replies are worded
    question_style: str  # how concrete and simple every question must be
    transitions: str  # how the model moves to a new topic
    # Opening phrases for a new topic, chosen in turn by the code (flow.py);
    # "" means no transition phrase.
    transition_phrases: list[str]


class ScaleLevel(RubricPart):
    name: str
    description: str


class Criterion(RubricPart):
    id: str
    description: str
    look_for: str
    # When this condition is met, the criterion is not applicable (see rules.not_applicable).
    not_applicable_if: str | None = None


class Example(RubricPart):
    """An example by the author of how the chatbot should explain or ask,
    given to the check prompt as a model. A "clarification" has the
    respondent's question and the chatbot's answer; a "follow_up" has a
    question to avoid, a better one, and why."""
    type: Literal["clarification", "follow_up"]
    respondent: str | None = None
    chatbot: str | None = None
    avoid: str | None = None
    better: str | None = None
    why: str | None = None

    @model_validator(mode="after")
    def _fields_of_its_type(self):
        needed = ["respondent", "chatbot"] if self.type == "clarification" else ["avoid", "better", "why"]
        given = [f for f in ["respondent", "chatbot", "avoid", "better", "why"] if getattr(self, f)]
        if sorted(given) != sorted(needed):
            raise ValueError(f"an example of type {self.type!r} needs exactly {needed}, got {given}")
        return self


class Subdimension(RubricPart):
    name: str
    definition: str
    opening_question: str
    criteria: list[Criterion]
    levels: dict[int, str]
    examples: list[Example] = []

    _levels_complete = field_validator("levels")(_check_all_levels)


class Dimension(RubricPart):
    name: str
    definition: str
    report_levels: dict[int, str]
    subdimensions: list[Subdimension]

    _levels_complete = field_validator("report_levels")(_check_all_levels)


class Rubric(RubricPart):
    version: str
    rules: Rules
    scale: dict[int, ScaleLevel]
    dimensions: list[Dimension]

    _levels_complete = field_validator("scale")(_check_all_levels)


def load_rubric(path: Path = RUBRIC_PATH) -> Rubric:
    """Read the rubric file and check that it has the expected structure."""
    return Rubric.model_validate_json(Path(path).read_text(encoding="utf-8"))


def only_dimensions(rubric: Rubric, names: list[str]) -> Rubric:
    """A copy of the rubric with only the named dimensions, in rubric order.
    For testing with fewer dimensions (the DIMENSIONS setting)."""
    unknown = set(names) - {d.name for d in rubric.dimensions}
    if unknown:
        raise ValueError(f"Unknown dimensions: {sorted(unknown)}")
    return rubric.model_copy(update={"dimensions": [d for d in rubric.dimensions if d.name in names]})
