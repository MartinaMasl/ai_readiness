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
    assert RUBRIC.rules.not_applicable in prompt


def test_fill_refuses_a_missing_value():
    with pytest.raises(ValueError):
        llm.fill("{{name}} and {{other}}", {"name": "x"})


def make_check(sub, statuses, **fields):
    """A CheckResult with the given status per criterion (in rubric order)."""
    criteria = [
        llm.CriterionCheck(criterion=c.id, status=status, quote="q" if status == "evidence" else None)
        for c, status in zip(sub.criteria, statuses)
    ]
    values = {"message_type": "answer", "follow_up_needed": False, "targets": [], "reply": None}
    values.update(fields)
    return llm.CheckResult(criteria=criteria, **values)


SUB = SUBS[0]  # Discoverability: monitoring has a not_applicable_if condition


def test_check_to_log_has_the_log_shape():
    check = make_check(SUB, ["evidence", "missing", "dont_know", "not_applicable"],
                       follow_up_needed=True, targets=[SUB.criteria[1].id], reply="A follow-up?")
    entry = llm.check_to_log(check, SUB)
    assert entry["criteria"][SUB.criteria[0].id] == {"status": "evidence", "quote": "q"}
    assert entry["criteria"][SUB.criteria[3].id] == {"status": "not_applicable", "quote": None}
    assert entry["targets"] == [SUB.criteria[1].id] and entry["reply"] == "A follow-up?"


def test_follow_up_may_target_only_missing_criteria():
    for status in ["dont_know", "not_applicable", "evidence"]:
        check = make_check(SUB, ["missing", "missing", "missing", status],
                           follow_up_needed=True, targets=[SUB.criteria[3].id], reply="?")
        with pytest.raises(ValueError):
            llm.check_to_log(check, SUB)


def test_follow_up_targets_one_or_two_criteria():
    three = make_check(SUB, ["missing"] * 4, follow_up_needed=True,
                       targets=[c.id for c in SUB.criteria[:3]], reply="?")
    with pytest.raises(ValueError):
        llm.check_to_log(three, SUB)
    two = make_check(SUB, ["missing"] * 4, follow_up_needed=True,
                     targets=[c.id for c in SUB.criteria[:2]], reply="?")
    assert len(llm.check_to_log(two, SUB)["targets"]) == 2


def test_not_applicable_only_where_the_rubric_has_a_condition():
    check = make_check(SUB, ["not_applicable", "evidence", "evidence", "evidence"])
    with pytest.raises(ValueError):
        llm.check_to_log(check, SUB)


def test_question_and_off_topic_need_a_reply():
    for message_type in ["question", "off_topic"]:
        with pytest.raises(ValueError):
            llm.check_to_log(make_check(SUB, ["missing"] * 4, message_type=message_type), SUB)


def test_check_to_log_refuses_wrong_criteria_and_evidence_without_quote():
    wrong_ids = llm.CheckResult(
        message_type="answer",
        criteria=[llm.CriterionCheck(criterion="made_up", status="evidence", quote="q")],
        follow_up_needed=False, targets=[], reply=None,
    )
    with pytest.raises(ValueError):
        llm.check_to_log(wrong_ids, SUB)
    no_quote = make_check(SUB, ["evidence"] * 3 + ["not_applicable"])
    no_quote.criteria[0].quote = None
    with pytest.raises(ValueError):
        llm.check_to_log(no_quote, SUB)


def test_criteria_text_contains_not_applicable_condition():
    text = llm.format_criteria(SUB)
    assert f"Not applicable if: {SUB.criteria[3].not_applicable_if}" in text


def test_check_prompt_contains_levels_and_follow_up_rules():
    _, template = llm.parse_prompt_file((PROMPTS / "check.md").read_text())
    prompt = llm.check_prompt(template, RUBRIC, SUB, MESSAGES)
    for text in SUB.levels.values():
        assert text in prompt
    assert RUBRIC.rules.follow_up_purpose in prompt and RUBRIC.rules.not_applicable in prompt


def test_score_to_log_has_the_log_shape():
    sub = SUBS[0]
    result = llm.ScoreResult(
        evidence=[llm.CriterionEvidence(criterion=c.id, evidence="no evidence") for c in sub.criteria],
        level=0, reason="r", why_not_higher="w", flags=["insufficient_evidence"],
    )
    entry = llm.score_to_log(result, sub)
    assert list(entry) == ["level", "evidence", "reason", "why_not_higher", "flags"]
    assert entry["evidence"] == {c.id: "no evidence" for c in sub.criteria}


def test_score_not_applicable_only_where_the_rubric_has_a_condition():
    evidence = [llm.CriterionEvidence(criterion=c.id, evidence="no evidence") for c in SUB.criteria]
    evidence[3].evidence = f"not applicable: {SUB.criteria[3].not_applicable_if}"
    ok = llm.ScoreResult(evidence=evidence, level=1, reason="r", why_not_higher="w", flags=[])
    assert llm.score_to_log(ok, SUB)["evidence"][SUB.criteria[3].id].startswith("not applicable")
    evidence[0].evidence = "not applicable: something"
    with pytest.raises(ValueError):
        llm.score_to_log(ok, SUB)


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
