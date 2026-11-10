import asyncio

import pytest

import llm
from rubric import load_rubric

RUBRIC = load_rubric()
SUBS = [sub for dim in RUBRIC.dimensions for sub in dim.subdimensions]
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
    version, template = llm.load_prompt(name)
    assert version
    build = llm.check_prompt if name == "check" else llm.score_prompt
    for sub in SUBS:
        args = (template, RUBRIC, sub, MESSAGES) + ((None, []) if name == "check" else ([],))
        prompt = build(*args)  # raises if a placeholder is left
        assert sub.definition in prompt and "Respondent: An answer." in prompt


def test_score_prompt_contains_rubric_levels_and_rules():
    _, template = llm.load_prompt("score")
    prompt = llm.score_prompt(template, RUBRIC, SUBS[0], MESSAGES, [])
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
    values = {"message_type": "answer", "follow_up_needed": False, "targets": [], "reply": None,
              "next_question": "Next?"}
    values.update(fields)
    return llm.CheckResult(criteria=criteria, **values)


SUB = SUBS[0]  # Discoverability: monitoring has a not_applicable_if condition
NEXT = SUBS[1]
NEXT_TOPIC = {"sub": NEXT, "phrase": "Moving on to", "ended_dimension": None, "next_dimension": None}


def test_next_question_is_required_except_for_the_last_subdimension():
    check = make_check(SUB, ["evidence"] * 3 + ["not_applicable"], next_question=None)
    with pytest.raises(ValueError):
        llm.check_to_log(check, SUB, NEXT_TOPIC)
    assert llm.check_to_log(check, SUB, None)["next_question"] is None
    check = make_check(SUB, ["evidence"] * 3 + ["not_applicable"], next_question="Next?")
    assert llm.check_to_log(check, SUB, NEXT_TOPIC)["next_question"] == "Next?"


def test_check_prompt_contains_the_next_topic():
    _, template = llm.load_prompt("check")
    prompt = llm.check_prompt(template, RUBRIC, SUB, MESSAGES, NEXT_TOPIC, [])
    assert NEXT.opening_question in prompt and NEXT.definition in prompt
    assert "There is no next topic" in llm.check_prompt(template, RUBRIC, SUB, MESSAGES, None, [])


def test_check_to_log_has_the_log_shape():
    check = make_check(SUB, ["evidence", "missing", "dont_know", "not_applicable"],
                       follow_up_needed=True, targets=[SUB.criteria[1].id], reply="A follow-up?")
    entry = llm.check_to_log(check, SUB, NEXT_TOPIC)
    assert entry["criteria"][SUB.criteria[0].id] == {"status": "evidence", "quote": "q"}
    assert entry["criteria"][SUB.criteria[3].id] == {"status": "not_applicable", "quote": None}
    assert entry["targets"] == [SUB.criteria[1].id] and entry["reply"] == "A follow-up?"


def test_follow_up_may_target_only_missing_criteria():
    for status in ["dont_know", "not_applicable", "evidence"]:
        check = make_check(SUB, ["missing", "missing", "missing", status],
                           follow_up_needed=True, targets=[SUB.criteria[3].id], reply="?")
        with pytest.raises(ValueError):
            llm.check_to_log(check, SUB, NEXT_TOPIC)


def test_follow_up_targets_one_or_two_criteria():
    three = make_check(SUB, ["missing"] * 4, follow_up_needed=True,
                       targets=[c.id for c in SUB.criteria[:3]], reply="?")
    with pytest.raises(ValueError):
        llm.check_to_log(three, SUB, NEXT_TOPIC)
    two = make_check(SUB, ["missing"] * 4, follow_up_needed=True,
                     targets=[c.id for c in SUB.criteria[:2]], reply="?")
    assert len(llm.check_to_log(two, SUB, NEXT_TOPIC)["targets"]) == 2


def test_not_applicable_only_where_the_rubric_has_a_condition():
    check = make_check(SUB, ["not_applicable", "evidence", "evidence", "evidence"])
    with pytest.raises(ValueError):
        llm.check_to_log(check, SUB, NEXT_TOPIC)


def test_question_and_off_topic_need_a_reply():
    for message_type in ["question", "off_topic"]:
        with pytest.raises(ValueError):
            llm.check_to_log(make_check(SUB, ["missing"] * 4, message_type=message_type), SUB, NEXT)


def test_check_to_log_refuses_wrong_criteria_and_evidence_without_quote():
    wrong_ids = llm.CheckResult(
        message_type="answer",
        criteria=[llm.CriterionCheck(criterion="made_up", status="evidence", quote="q")],
        follow_up_needed=False, targets=[], reply=None, next_question="Next?",
    )
    with pytest.raises(ValueError):
        llm.check_to_log(wrong_ids, SUB, NEXT_TOPIC)
    no_quote = make_check(SUB, ["evidence"] * 3 + ["not_applicable"])
    no_quote.criteria[0].quote = None
    with pytest.raises(ValueError):
        llm.check_to_log(no_quote, SUB, NEXT_TOPIC)


def test_criteria_text_contains_not_applicable_condition():
    text = llm.format_criteria(SUB)
    assert f"Not applicable if: {SUB.criteria[3].not_applicable_if}" in text


def test_check_prompt_contains_levels_and_follow_up_rules():
    _, template = llm.load_prompt("check")
    prompt = llm.check_prompt(template, RUBRIC, SUB, MESSAGES, NEXT_TOPIC, [])
    for text in SUB.levels.values():
        assert text in prompt
    assert RUBRIC.rules.follow_up_purpose in prompt and RUBRIC.rules.not_applicable in prompt
    assert RUBRIC.rules.language in prompt and RUBRIC.rules.transitions in prompt
    assert RUBRIC.rules.question_style in prompt


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


def reply_with(answer):
    """OpenAI's reply (as a dict) carrying this answer."""
    return {"model": "gpt-6.1-sol-2026-01-01",
            "choices": [{"message": {"content": answer.model_dump_json(), "refusal": None}}]}


class FakeSend:
    """Stands in for the function that sends the HTTP request: answers with
    the given (status, reply) pairs in turn and keeps what was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    async def __call__(self, url, headers, body):
        self.sent.append({"url": url, "headers": headers, "body": body})
        return self.responses.pop(0)


SCORE = llm.ScoreResult(evidence=[], level=2, reason="r", why_not_higher="w", flags=[])


def test_call_model_sends_the_request_and_returns_answer_and_model_version():
    send = FakeSend((200, reply_with(SCORE)))
    result, version = asyncio.run(llm.call_model(send, "sk-test", "gpt-6.1-sol", 1.0, "the prompt", llm.ScoreResult))
    assert result.level == 2 and version == "gpt-6.1-sol-2026-01-01"
    sent = send.sent[0]
    assert sent["url"] == llm.OPENAI_URL and sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["body"]["model"] == "gpt-6.1-sol" and sent["body"]["temperature"] == 1.0
    assert sent["body"]["messages"] == [{"role": "user", "content": "the prompt"}]
    schema = sent["body"]["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"] == llm.ScoreResult.model_json_schema()


def test_call_model_retries_once_when_openai_is_briefly_unavailable():
    send = FakeSend((503, {}), (200, reply_with(SCORE)))
    result, _ = asyncio.run(llm.call_model(send, "k", "m", 1.0, "p", llm.ScoreResult))
    assert result.level == 2 and len(send.sent) == 2
    with pytest.raises(llm.ModelCallError):
        asyncio.run(llm.call_model(FakeSend((429, {}), (500, {})), "k", "m", 1.0, "p", llm.ScoreResult))


def test_call_model_does_not_retry_a_rejected_request_and_says_why():
    send = FakeSend((400, {"error": {"message": "Unsupported value: 'temperature'"}}))
    with pytest.raises(llm.ModelCallError, match="400.*temperature"):
        asyncio.run(llm.call_model(send, "k", "m", 0.0, "p", llm.ScoreResult))
    assert len(send.sent) == 1


def test_call_model_refuses_a_refusal_and_an_answer_that_does_not_fit():
    refusal = {"model": "m", "choices": [{"message": {"content": None, "refusal": "no"}}]}
    with pytest.raises(llm.ModelCallError):
        asyncio.run(llm.call_model(FakeSend((200, refusal)), "k", "m", 1.0, "p", llm.ScoreResult))
    wrong = {"model": "m", "choices": [{"message": {"content": '{"level": 9}', "refusal": None}}]}
    with pytest.raises(ValueError):  # Pydantic's ValidationError is a ValueError
        asyncio.run(llm.call_model(FakeSend((200, wrong)), "k", "m", 1.0, "p", llm.ScoreResult))


def test_openai_model_check_and_score_return_the_log_format():
    sub = SUBS[0]
    check = make_check(sub, ["evidence", "evidence", "evidence", "not_applicable"])
    score = llm.ScoreResult(
        evidence=[llm.CriterionEvidence(criterion=c.id, evidence="q") for c in sub.criteria],
        level=3, reason="r", why_not_higher="w", flags=[],
    )
    model = llm.OpenAIModel(FakeSend((200, reply_with(check)), (200, reply_with(score))), "k", "gpt-6.1-sol", 1.0)
    assert asyncio.run(model.check(RUBRIC, sub, MESSAGES, NEXT_TOPIC, []))["criteria"][sub.criteria[3].id]["status"] == "not_applicable"
    assert asyncio.run(model.score(RUBRIC, sub, MESSAGES, []))["level"] == 3
    info = model.info()
    assert info["model"] == {"id": "gpt-6.1-sol", "settings": {"temperature": 1.0}}
    assert info["prompt_versions"]["check"] == llm.load_prompt("check")[0]


def test_prompts_contain_the_dimension_context():
    context = [{"subdimension": "Earlier topic", "messages": [{"role": "respondent", "text": "As said before."}]}]
    for name in ["check", "score"]:
        _, template = llm.load_prompt(name)
        if name == "check":
            prompt = llm.check_prompt(template, RUBRIC, SUB, MESSAGES, NEXT_TOPIC, context)
            empty = llm.check_prompt(template, RUBRIC, SUB, MESSAGES, NEXT_TOPIC, [])
        else:
            prompt = llm.score_prompt(template, RUBRIC, SUB, MESSAGES, context)
            empty = llm.score_prompt(template, RUBRIC, SUB, MESSAGES, [])
        assert "## Earlier topic" in prompt and "Respondent: As said before." in prompt
        assert "this is the first topic of the dimension" in empty


def test_check_prompt_gets_the_transition_phrase_and_the_end_of_an_area():
    _, template = llm.load_prompt("check")
    prompt = llm.check_prompt(template, RUBRIC, SUB, MESSAGES, NEXT_TOPIC, [])
    assert 'Transition phrase: "Moving on to"' in prompt and "same area" in prompt
    new_area = dict(NEXT_TOPIC, phrase="", ended_dimension="Data", next_dimension="People")
    prompt = llm.check_prompt(template, RUBRIC, SUB, MESSAGES, new_area, [])
    assert "Transition phrase: none" in prompt
    assert 'starts a new area, "People"' in prompt and 'The area "Data" has just ended' in prompt


def test_check_prompt_contains_the_examples_of_this_topic_and_of_the_next():
    _, template = llm.load_prompt("check")
    with_examples = [s for s in SUBS if s.examples]
    talent = next(s for s in SUBS if s.name == "Talent")
    for sub in with_examples:
        prompt = llm.check_prompt(template, RUBRIC, sub, MESSAGES, None, [])
        for e in sub.examples:
            assert all(text in prompt for text in [e.respondent, e.chatbot, e.avoid, e.better, e.why] if text)
    prompt = llm.check_prompt(template, RUBRIC, SUBS[0], MESSAGES, dict(NEXT_TOPIC, sub=talent), [])
    assert "Examples for this topic:\n- Respondent asks: Involved in what?" in prompt
    assert "Follow-up to avoid: " + talent.examples[1].avoid in prompt


def test_a_topic_without_examples_says_none():
    sub = next(s for s in SUBS if not s.examples)
    assert llm.format_examples(sub) == "None."
