"""Loads and validates the rubric file.

The rubric is the single source of the methodology texts. Code reads it
as it is and never rewrites it.
"""

from pathlib import Path

from pydantic import BaseModel, field_validator

# rubric.json sits next to this file. wrangler.jsonc bundles it into the Worker.
RUBRIC_PATH = Path(__file__).parent / "rubric.json"

LEVELS = [0, 1, 2, 3, 4, 5]


def _check_all_levels(value: dict) -> dict:
    """A level table must describe exactly the levels 0 to 5."""
    if sorted(value) != LEVELS:
        raise ValueError(f"levels must be exactly {LEVELS}, got {sorted(value)}")
    return value


class Rules(BaseModel):
    score_values: str
    max_follow_ups: int  # per subdimension
    max_follow_ups_total: int  # for the whole conversation
    max_redirects: int  # off-topic messages steered back, per subdimension
    max_clarifications: int  # respondent questions answered, per subdimension
    follow_up_purpose: str
    mixed_evidence: str
    plans: str
    insufficient_evidence: str
    not_applicable: str


class ScaleLevel(BaseModel):
    name: str
    description: str


class Criterion(BaseModel):
    id: str
    description: str
    look_for: str
    # When this condition is met, the criterion is not applicable (see rules.not_applicable).
    not_applicable_if: str | None = None


class Subdimension(BaseModel):
    name: str
    definition: str
    opening_question: str
    criteria: list[Criterion]
    levels: dict[int, str]
    examples: list = []

    _levels_complete = field_validator("levels")(_check_all_levels)


class Dimension(BaseModel):
    name: str
    definition: str
    report_levels: dict[int, str]
    subdimensions: list[Subdimension]

    _levels_complete = field_validator("report_levels")(_check_all_levels)


class Rubric(BaseModel):
    version: str
    rules: Rules
    scale: dict[int, ScaleLevel]
    dimensions: list[Dimension]

    _levels_complete = field_validator("scale")(_check_all_levels)


def load_rubric(path: Path = RUBRIC_PATH) -> Rubric:
    """Read the rubric file and check that it has the expected structure."""
    return Rubric.model_validate_json(Path(path).read_text(encoding="utf-8"))
