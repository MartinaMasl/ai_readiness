"""A fake model with the same two methods as llm.OpenAIModel, so the flow
can be tested without calling OpenAI.

Keyword rules for the check:
- ends with "?": a question
- contains "[off]": off topic
- contains "don't know": nothing known about any criterion
- first answer in the subdimension: the first two criteria missing
  (follow-up needed), a criterion with a not_applicable_if condition not
  applicable, the rest evidence
- later answers: everything has evidence or is not applicable

The next question is "[fake next] " + the next opening question.
The score is fixed by position in the dimension: 4, 2, 5.
"""

import llm

FAKE_SCORES = [4, 2, 5]


def is_answer(text: str) -> bool:
    return not (text.endswith("?") or "[off]" in text or "don't know" in text.lower())


class FakeModel:
    def info(self) -> dict:
        return {"model": {"id": "fake", "settings": {}}, "prompt_versions": {"check": None, "score": None}}

    def __init__(self):
        self.contexts = []  # (call, subdimension, names of the context subdimensions), for the tests

    async def check(self, rubric, sub, messages, next_sub, context) -> dict:
        self.contexts.append(("check", sub.name, [part["subdimension"] for part in context]))
        answer = messages[-1]["text"]
        earlier = [m["text"] for m in messages[:-1] if m["role"] == "respondent"]

        def result(message_type, status_for, follow_up_needed=False, targets=(), reply=None):
            criteria = [
                llm.CriterionCheck(criterion=c.id, status=status_for(c),
                                   quote="[fake quote]" if status_for(c) == "evidence" else None)
                for c in sub.criteria
            ]
            next_question = f"[fake next] {next_sub.opening_question}" if next_sub else None
            check = llm.CheckResult(message_type=message_type, criteria=criteria,
                                    follow_up_needed=follow_up_needed, targets=list(targets), reply=reply,
                                    next_question=next_question)
            return llm.check_to_log(check, sub, next_sub)

        if answer.endswith("?"):
            return result("question", lambda c: "missing",
                          reply=f"[fake] The model would answer your question here. {sub.opening_question}")
        if "[off]" in answer:
            return result("off_topic", lambda c: "missing",
                          reply=f"[fake] The model would steer back here. {sub.opening_question}")
        if "don't know" in answer.lower():
            return result("dont_know", lambda c: "dont_know")
        if not any(is_answer(text) for text in earlier):
            missing = [c.id for c in sub.criteria if not c.not_applicable_if][:2]
            return result(
                "answer",
                lambda c: "missing" if c.id in missing else "not_applicable" if c.not_applicable_if else "evidence",
                follow_up_needed=True, targets=missing,
                reply="[fake] Could you tell me a bit more? "
                      + " ".join(c.description for c in sub.criteria if c.id in missing),
            )
        return result("answer", lambda c: "not_applicable" if c.not_applicable_if else "evidence")

    async def score(self, rubric, sub, messages, context) -> dict:
        self.contexts.append(("score", sub.name, [part["subdimension"] for part in context]))
        dimension = next(d for d in rubric.dimensions if sub in d.subdimensions)
        return {
            "level": FAKE_SCORES[dimension.subdimensions.index(sub) % len(FAKE_SCORES)],
            "evidence": {c.id: "fake, no evidence used" for c in sub.criteria},
            "reason": "Fixed score from the fake model.",
            "why_not_higher": "",
            "flags": ["fake"],
        }
