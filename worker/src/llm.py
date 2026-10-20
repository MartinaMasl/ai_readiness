"""The two model calls, check and score.

This module builds the prompt, calls OpenAI with the `openai` package and
checks the model's reply. The Worker (step 5) and the test script
(scripts/try_prompts.py) both use it, so they run the same code.

Structured output: the Pydantic models below are passed to the `openai`
package, which turns them into the JSON schema sent to OpenAI and
validates the reply with them.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from rubric import Rubric, Subdimension


class StrictModel(BaseModel):
    # OpenAI's strict mode needs "additionalProperties": false in the schema.
    model_config = ConfigDict(extra="forbid")


# --- What the model returns -------------------------------------------------

class CriterionCheck(StrictModel):
    criterion: str  # criterion id from the rubric
    covered: bool
    quote: str | None


class CheckResult(StrictModel):
    message_type: Literal["answer", "clarification_request"]
    criteria: list[CriterionCheck]
    follow_up: str | None
    targets: str | None


class CriterionEvidence(StrictModel):
    criterion: str  # criterion id from the rubric
    evidence: str  # a quote, or "no evidence"


class ScoreResult(StrictModel):
    # Evidence comes first, so the model writes it down before it picks a level.
    evidence: list[CriterionEvidence]
    level: Literal[0, 1, 2, 3, 4, 5]
    reason: str
    why_not_higher: str
    flags: list[Literal["insufficient_evidence"]]


# --- Prompts ----------------------------------------------------------------

def parse_prompt_file(text: str) -> tuple[str, str]:
    """Split a prompt file into its version (first line, "version: ...")
    and the template (the rest)."""
    first_line, _, template = text.partition("\n")
    if not first_line.startswith("version:"):
        raise ValueError("The first line of a prompt file must be 'version: ...'")
    return first_line.removeprefix("version:").strip(), template


def fill(template: str, values: dict[str, str]) -> str:
    """Replace each {{name}} in the template by its value."""
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)
    if "{{" in template:
        raise ValueError("The prompt still contains a {{placeholder}} without a value")
    return template


def format_criteria(sub: Subdimension) -> str:
    return "\n".join(
        f"- {c.id}: {c.description}\n  Look for: {c.look_for}" for c in sub.criteria
    )


def format_levels(sub: Subdimension) -> str:
    return "\n".join(f"Level {level}: {text}" for level, text in sorted(sub.levels.items()))


def format_transcript(messages: list[dict]) -> str:
    """messages: [{"role": "chatbot" | "respondent", "text": ...}] of this subdimension only."""
    names = {"chatbot": "Chatbot", "respondent": "Respondent"}
    return "\n\n".join(f"{names[m['role']]}: {m['text']}" for m in messages)


def check_prompt(template: str, rubric: Rubric, sub: Subdimension, messages: list[dict]) -> str:
    return fill(template, {
        "name": sub.name,
        "definition": sub.definition,
        "criteria": format_criteria(sub),
        "plans_rule": rubric.rules.plans,
        "transcript": format_transcript(messages),
    })


def score_prompt(template: str, rubric: Rubric, sub: Subdimension, messages: list[dict]) -> str:
    rules = rubric.rules
    return fill(template, {
        "name": sub.name,
        "definition": sub.definition,
        "criteria": format_criteria(sub),
        "levels": format_levels(sub),
        "rules": "\n".join(
            f"- {rule}"
            for rule in [rules.score_values, rules.mixed_evidence, rules.plans, rules.insufficient_evidence]
        ),
        "transcript": format_transcript(messages),
    })


# --- The model call ---------------------------------------------------------

async def call_model(client, model_id: str, temperature: float, prompt: str,
                     answer_model: type[StrictModel]) -> tuple[StrictModel, str]:
    """Send the prompt and return the validated answer and the exact model
    version that answered (OpenAI reports it in "model", which matters when
    the id is an alias). client is an openai.AsyncOpenAI."""
    completion = await client.chat.completions.parse(
        model=model_id,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
        response_format=answer_model,
    )
    message = completion.choices[0].message
    if message.refusal:
        raise ValueError(f"The model refused: {message.refusal}")
    return message.parsed, completion.model


# --- Into the log format (CLAUDE.md) ----------------------------------------

def _check_criterion_ids(sub: Subdimension, ids: list[str]) -> None:
    expected = [c.id for c in sub.criteria]
    if ids != expected:
        raise ValueError(f"Expected criteria {expected}, the model returned {ids}")


def check_to_log(result: CheckResult, sub: Subdimension) -> dict:
    """The check as it is stored in the state and the log. all_covered is
    worked out here, not by the model."""
    _check_criterion_ids(sub, [c.criterion for c in result.criteria])
    all_covered = all(c.covered for c in result.criteria)
    needs_follow_up = result.message_type == "clarification_request" or not all_covered
    if needs_follow_up and not result.follow_up:
        raise ValueError("A follow-up is needed but the model did not write one")
    if result.targets is not None and result.targets not in [c.id for c in sub.criteria]:
        raise ValueError(f"Unknown criterion in targets: {result.targets}")
    return {
        "message_type": result.message_type,
        "criteria": {c.criterion: {"covered": c.covered, "quote": c.quote} for c in result.criteria},
        "all_covered": all_covered,
        "follow_up": result.follow_up if needs_follow_up else None,
        "targets": result.targets if needs_follow_up else None,
    }


def score_to_log(result: ScoreResult, sub: Subdimension) -> dict:
    _check_criterion_ids(sub, [e.criterion for e in result.evidence])
    return {
        "level": result.level,
        "evidence": {e.criterion: e.evidence for e in result.evidence},
        "reason": result.reason,
        "why_not_higher": result.why_not_higher,
        "flags": list(result.flags),
    }
