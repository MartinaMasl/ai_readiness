"""The two model calls, check and score.

This module builds the prompt, calls OpenAI with the `openai` package and
checks the model's reply. The Worker (through OpenAIModel) and the test
script (scripts/try_prompts.py) both use it, so they run the same code.

Structured output: the Pydantic models below are passed to the `openai`
package, which turns them into the JSON schema sent to OpenAI and
validates the reply with them.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from rubric import Rubric, Subdimension


class StrictModel(BaseModel):
    # OpenAI's strict mode needs "additionalProperties": false in the schema.
    model_config = ConfigDict(extra="forbid")


# --- What the model returns -------------------------------------------------

MessageType = Literal["answer", "partly_relevant", "question", "dont_know", "off_topic"]
Status = Literal["evidence", "not_applicable", "dont_know", "missing"]


class CriterionCheck(StrictModel):
    criterion: str  # criterion id from the rubric
    status: Status
    quote: str | None  # only for "evidence"


class CheckResult(StrictModel):
    message_type: MessageType
    criteria: list[CriterionCheck]
    follow_up_needed: bool
    targets: list[str]  # up to two missing criteria the follow-up asks about
    reply: str | None
    next_question: str | None  # opens the next subdimension if the flow moves on


class CriterionEvidence(StrictModel):
    criterion: str  # criterion id from the rubric
    evidence: str  # a quote, "not applicable: <condition>" or "no evidence"


class ScoreResult(StrictModel):
    # Evidence comes first, so the model writes it down before it picks a level.
    evidence: list[CriterionEvidence]
    level: Literal[0, 1, 2, 3, 4, 5]
    reason: str
    why_not_higher: str
    flags: list[Literal["insufficient_evidence"]]


# --- Prompts ----------------------------------------------------------------

# The prompt files sit next to this file; wrangler.jsonc bundles them into the Worker.
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    """The version and the template of prompts/<name>.md."""
    return parse_prompt_file((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"))


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
    lines = []
    for c in sub.criteria:
        lines.append(f"- {c.id}: {c.description}\n  Look for: {c.look_for}")
        if c.not_applicable_if:
            lines.append(f"  Not applicable if: {c.not_applicable_if}")
    return "\n".join(lines)


def format_rules(rules: list[str]) -> str:
    return "\n".join(f"- {rule}" for rule in rules)


def format_levels(sub: Subdimension) -> str:
    return "\n".join(f"Level {level}: {text}" for level, text in sorted(sub.levels.items()))


def format_transcript(messages: list[dict]) -> str:
    """messages: [{"role": "chatbot" | "respondent", "text": ...}] of this subdimension only."""
    names = {"chatbot": "Chatbot", "respondent": "Respondent"}
    return "\n\n".join(f"{names[m['role']]}: {m['text']}" for m in messages)


def format_context(context: list[dict]) -> str:
    """context: [{"subdimension": name, "messages": [...]}] of other
    subdimensions of the same dimension."""
    if not context:
        return "None: this is the first topic of the dimension."
    return "\n\n".join(
        f"## {part['subdimension']}\n\n{format_transcript(part['messages'])}" for part in context
    )


def format_next_topic(next_sub: Subdimension | None) -> str:
    if next_sub is None:
        return "There is no next topic; this is the last one. next_question is null."
    return (
        f"Name: {next_sub.name}\n"
        f"Definition: {next_sub.definition}\n"
        f"Core question: {next_sub.opening_question}"
    )


def check_prompt(template: str, rubric: Rubric, sub: Subdimension, messages: list[dict],
                 next_sub: Subdimension | None, context: list[dict]) -> str:
    return fill(template, {
        "name": sub.name,
        "definition": sub.definition,
        "next_topic": format_next_topic(next_sub),
        "context": format_context(context),
        "criteria": format_criteria(sub),
        "levels": format_levels(sub),
        "rules": format_rules([rubric.rules.follow_up_purpose, rubric.rules.not_applicable, rubric.rules.plans]),
        "transcript": format_transcript(messages),
    })


def score_prompt(template: str, rubric: Rubric, sub: Subdimension, messages: list[dict],
                 context: list[dict]) -> str:
    rules = rubric.rules
    return fill(template, {
        "name": sub.name,
        "definition": sub.definition,
        "context": format_context(context),
        "criteria": format_criteria(sub),
        "levels": format_levels(sub),
        "rules": format_rules([
            rules.score_values, rules.mixed_evidence, rules.plans,
            rules.insufficient_evidence, rules.not_applicable,
        ]),
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


class OpenAIModel:
    """What the flow calls: the check and the score with fixed settings.
    Both return the log format below. (The tests use a fake with the same
    two methods.)"""

    def __init__(self, client, model_id: str, temperature: float):
        self.client = client  # an openai.AsyncOpenAI
        self.model_id = model_id
        self.temperature = temperature
        self.check_version, self.check_template = load_prompt("check")
        self.score_version, self.score_template = load_prompt("score")

    def info(self) -> dict:
        """Model and prompt versions for the conversation log."""
        return {
            "model": {"id": self.model_id, "settings": {"temperature": self.temperature}},
            "prompt_versions": {"check": self.check_version, "score": self.score_version},
        }

    async def check(self, rubric: Rubric, sub: Subdimension, messages: list[dict],
                    next_sub: Subdimension | None, context: list[dict]) -> dict:
        prompt = check_prompt(self.check_template, rubric, sub, messages, next_sub, context)
        result, _ = await call_model(self.client, self.model_id, self.temperature, prompt, CheckResult)
        return check_to_log(result, sub, next_sub)

    async def score(self, rubric: Rubric, sub: Subdimension, messages: list[dict],
                    context: list[dict]) -> dict:
        prompt = score_prompt(self.score_template, rubric, sub, messages, context)
        result, _ = await call_model(self.client, self.model_id, self.temperature, prompt, ScoreResult)
        return score_to_log(result, sub)


# --- Into the log format (CLAUDE.md) ----------------------------------------

def _check_criterion_ids(sub: Subdimension, ids: list[str]) -> None:
    expected = [c.id for c in sub.criteria]
    if ids != expected:
        raise ValueError(f"Expected criteria {expected}, the model returned {ids}")


def check_to_log(result: CheckResult, sub: Subdimension, next_sub: Subdimension | None) -> dict:
    """The check as it is stored in the state and the log, after checking
    that the model's answer is consistent."""
    _check_criterion_ids(sub, [c.criterion for c in result.criteria])
    status = {c.criterion: c.status for c in result.criteria}
    for c in result.criteria:
        if c.status == "evidence" and not c.quote:
            raise ValueError(f"{c.criterion}: status evidence without a quote")
    has_condition = {c.id for c in sub.criteria if c.not_applicable_if}
    for criterion, value in status.items():
        if value == "not_applicable" and criterion not in has_condition:
            raise ValueError(f"{criterion}: not_applicable, but the rubric gives no condition for it")
    if result.follow_up_needed:
        if not 1 <= len(result.targets) <= 2:
            raise ValueError(f"A follow-up must target one or two criteria, got {result.targets}")
        for target in result.targets:
            if status.get(target) != "missing":
                raise ValueError(f"Follow-up target {target} is not a missing criterion")
    elif result.targets:
        raise ValueError("Targets given although no follow-up is needed")
    needs_reply = result.message_type in ("question", "off_topic") or result.follow_up_needed
    if needs_reply and not result.reply:
        raise ValueError("A reply is needed but the model did not write one")
    if next_sub is not None and not result.next_question:
        raise ValueError("There is a next subdimension but the model wrote no next question")
    return {
        "message_type": result.message_type,
        "criteria": {c.criterion: {"status": c.status, "quote": c.quote} for c in result.criteria},
        "follow_up_needed": result.follow_up_needed,
        "targets": list(result.targets),
        "reply": result.reply if needs_reply else None,
        "next_question": result.next_question if next_sub is not None else None,
    }


def score_to_log(result: ScoreResult, sub: Subdimension) -> dict:
    _check_criterion_ids(sub, [e.criterion for e in result.evidence])
    has_condition = {c.id for c in sub.criteria if c.not_applicable_if}
    for e in result.evidence:
        if e.evidence.startswith("not applicable") and e.criterion not in has_condition:
            raise ValueError(f"{e.criterion}: not applicable, but the rubric gives no condition for it")
    return {
        "level": result.level,
        "evidence": {e.criterion: e.evidence for e in result.evidence},
        "reason": result.reason,
        "why_not_higher": result.why_not_higher,
        "flags": list(result.flags),
    }
