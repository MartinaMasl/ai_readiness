import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import llm
from rubric import load_rubric

RUBRIC = load_rubric()
SUBS = [sub for dim in RUBRIC.dimensions for sub in dim.subdimensions]
PROMPTS = Path(__file__).parent.parent.parent / "prompts"
MESSAGES = [
    {"role": "chatbot", "text": "An opening question?"},
    {"role": "respondent", "text": "An answer."},
]


def objects_in(schema):
    """Every object definition in a JSON schema, nested ones included."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from objects_in(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from objects_in(value)


@pytest.mark.parametrize("answer_model", [llm.CheckResult, llm.ScoreResult])
def test_schema_fits_openai_strict_mode(answer_model):
    # Strict mode: no extra fields, and every field is required.
    for obj in objects_in(answer_model.model_json_schema()):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


@pytest.mark.parametrize("name", ["check", "score"])
def test_prompt_files_have_a_version_and_fill_completely(name):
    version, template = llm.parse_prompt_file((PROMPTS / f"{name}.md").read_text())
    assert version
    build = llm.check_prompt if name == "check" else llm.score_prompt
    for sub in SUBS:
        prompt = build(template, RUBRIC, sub, MESSAGES)  # raises if a placeholder is left
        assert sub.definition in prompt and "Respondent: An answer." in prompt


def test_score_prompt_contains_rubric_levels_and_rules():
    _, template = llm.parse_prompt_file((PROMPTS / "score.md").read_text())
    prompt = llm.score_prompt(template, RUBRIC, SUBS[0], MESSAGES)
    for text in SUBS[0].levels.values():
        assert text in prompt
    assert RUBRIC.rules.mixed_evidence in prompt and RUBRIC.rules.insufficient_evidence in prompt


def test_fill_refuses_a_missing_value():
    with pytest.raises(ValueError):
        llm.fill("{{name}} and {{other}}", {"name": "x"})


def test_check_to_log_works_out_all_covered():
    sub = SUBS[0]
    result = llm.CheckResult(
        message_type="answer",
        criteria=[llm.CriterionCheck(criterion=c.id, covered=i == 0, quote="q" if i == 0 else None)
                  for i, c in enumerate(sub.criteria)],
        follow_up="A follow-up?",
        targets=sub.criteria[1].id,
    )
    entry = llm.check_to_log(result, sub)
    assert entry["all_covered"] is False
    assert entry["criteria"][sub.criteria[0].id] == {"covered": True, "quote": "q"}
    assert entry["targets"] == sub.criteria[1].id


def test_check_to_log_refuses_wrong_criteria_and_missing_follow_up():
    sub = SUBS[0]
    wrong_ids = llm.CheckResult(
        message_type="answer",
        criteria=[llm.CriterionCheck(criterion="made_up", covered=True, quote="q")],
        follow_up=None, targets=None,
    )
    with pytest.raises(ValueError):
        llm.check_to_log(wrong_ids, sub)

    clarification_without_reply = llm.CheckResult(
        message_type="clarification_request",
        criteria=[llm.CriterionCheck(criterion=c.id, covered=True, quote="q") for c in sub.criteria],
        follow_up=None, targets=None,
    )
    with pytest.raises(ValueError):
        llm.check_to_log(clarification_without_reply, sub)


def test_score_to_log_has_the_log_shape():
    sub = SUBS[0]
    result = llm.ScoreResult(
        evidence=[llm.CriterionEvidence(criterion=c.id, evidence="no evidence") for c in sub.criteria],
        level=0, reason="r", why_not_higher="w", flags=["insufficient_evidence"],
    )
    entry = llm.score_to_log(result, sub)
    assert list(entry) == ["level", "evidence", "reason", "why_not_higher", "flags"]
    assert entry["evidence"] == {c.id: "no evidence" for c in sub.criteria}


class FakeClient:
    """Stands in for openai.AsyncOpenAI: returns a fixed reply."""

    def __init__(self, parsed, refusal=None):
        message = SimpleNamespace(parsed=parsed, refusal=refusal)
        completion = SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-6.1-sol-2026-01-01")
        self.sent = None

        async def parse(**request):
            self.sent = request
            return completion

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


def test_call_model_sends_settings_and_returns_answer_and_model_version():
    answer = llm.ScoreResult(evidence=[], level=2, reason="r", why_not_higher="w", flags=[])
    client = FakeClient(answer)
    result, version = asyncio.run(llm.call_model(client, "gpt-6.1-sol", 0, "the prompt", llm.ScoreResult))
    assert result.level == 2 and version == "gpt-6.1-sol-2026-01-01"
    assert client.sent["temperature"] == 0 and client.sent["response_format"] is llm.ScoreResult
    assert client.sent["messages"] == [{"role": "user", "content": "the prompt"}]


def test_call_model_refuses_a_refusal():
    with pytest.raises(ValueError):
        asyncio.run(llm.call_model(FakeClient(None, refusal="no"), "m", 0, "p", llm.ScoreResult))
