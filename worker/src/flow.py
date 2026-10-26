"""The conversation flow, one step per request.

Code controls the flow: ask the opening question, check the answer,
decide what happens next (next_action), score, move to the next
subdimension, and finish. The model only reports what the message is and
what it covers; the limits in the rubric rules are applied here.

Step 3: the check and the score are FAKE (fake_check, fake_score). Step 5
replaces only these two functions with the model calls; the rest of the
flow stays the same.

The state is a plain dict so it can be saved to D1 as JSON:
    subdimension_index  position in the list of all subdimensions
    follow_ups          follow-ups asked in the current subdimension
    clarifications      respondent questions answered in the current subdimension
    redirects           off-topic messages steered back in the current subdimension
    follow_ups_total    follow-ups asked in the whole conversation
    transcript          all messages, each tagged with its subdimension
    checks              check results of the current subdimension
    scores              {dimension: {subdimension: level}}
    done                True when every subdimension is scored
"""

import llm
from rubric import Rubric, Rules, Subdimension

# Draft text, to be reviewed by the author.
INTRO = (
    "Hello! This conversation assesses how ready your organization is for AI. "
    "I will ask one question per topic and sometimes a short follow-up. "
    "Please describe how things work in your organization today, not what is planned."
)
CLOSING = "Thank you, that was the last question. Your results are ready."

# Fake scores, by position of the subdimension within its dimension.
FAKE_SCORES = [4, 2, 5]


def all_subdimensions(rubric: Rubric) -> list[tuple[str, Subdimension]]:
    """Every subdimension in rubric order, with the name of its dimension."""
    return [(dim.name, sub) for dim in rubric.dimensions for sub in dim.subdimensions]


def outline(rubric: Rubric) -> list[dict]:
    """Dimension and subdimension names, for the progress sidebar on the chat page."""
    return [
        {"name": dim.name, "subdimensions": [sub.name for sub in dim.subdimensions]}
        for dim in rubric.dimensions
    ]


def check_summary(rubric: Rubric, state: dict) -> dict | None:
    """What the chat page shows in test mode about the last check: the
    message type, what the code did, how many criteria have evidence or are
    not applicable, and the criteria a follow-up asks about. None when the
    current subdimension has no check yet (it was just scored).
    """
    if state["done"] or not state["checks"]:
        return None
    _, sub = all_subdimensions(rubric)[state["subdimension_index"]]
    check = state["checks"][-1]
    descriptions = {c.id: c.description for c in sub.criteria}
    return {
        "subdimension": sub.name,
        "message_type": check["message_type"],
        "action": check["action"],
        "covered": sum(1 for c in check["criteria"].values() if c["status"] in ("evidence", "not_applicable")),
        "total": len(check["criteria"]),
        "follow_up_criteria": [descriptions[t] for t in check["targets"]] if check["action"] == "follow_up" else [],
    }


def new_conversation(rubric: Rubric, conversation_id: str, started_at: str) -> tuple[dict, dict]:
    """Return the initial state and the initial log."""
    _, first = all_subdimensions(rubric)[0]
    state = {
        "subdimension_index": 0,
        "follow_ups": 0,
        "clarifications": 0,
        "redirects": 0,
        "follow_ups_total": 0,
        "transcript": [
            {"subdimension": None, "role": "chatbot", "text": INTRO},
            {"subdimension": first.name, "role": "chatbot", "text": first.opening_question},
        ],
        "checks": [],
        "scores": {},
        "done": False,
    }
    log = {
        "id": conversation_id,
        "started_at": started_at,
        "model": {"id": "fake", "settings": {}},
        "rubric_version": rubric.version,
        "prompt_versions": {"check": None, "score": None},
        "subdimensions": [],
    }
    return state, log


# --- FAKE: replaced by model calls in step 5 --------------------------------

def fake_check(sub: Subdimension, checks: list[dict], answer: str) -> llm.CheckResult:
    """Simple keyword rules, so every path can be tried without a model:
    - ends with "?": a question
    - contains "[off]": off topic
    - contains "don't know": nothing known about any criterion
    - first answer in the subdimension: the first two criteria missing
      (follow-up needed), a criterion with a not_applicable_if condition
      not applicable, the rest evidence
    - later answers: everything has evidence or is not applicable
    """
    def criteria(status_for) -> list[llm.CriterionCheck]:
        result = []
        for c in sub.criteria:
            status = status_for(c)
            result.append(llm.CriterionCheck(criterion=c.id, status=status,
                                             quote="[fake quote]" if status == "evidence" else None))
        return result

    if answer.endswith("?"):
        return llm.CheckResult(
            message_type="question", criteria=criteria(lambda c: "missing"),
            follow_up_needed=False, targets=[],
            reply=f"[fake] The model would answer your question here. {sub.opening_question}",
        )
    if "[off]" in answer:
        return llm.CheckResult(
            message_type="off_topic", criteria=criteria(lambda c: "missing"),
            follow_up_needed=False, targets=[],
            reply=f"[fake] The model would steer back here. {sub.opening_question}",
        )
    if "don't know" in answer.lower():
        return llm.CheckResult(
            message_type="dont_know", criteria=criteria(lambda c: "dont_know"),
            follow_up_needed=False, targets=[], reply=None,
        )
    answered_before = any(c["message_type"] in ("answer", "partly_relevant") for c in checks)
    if not answered_before:
        missing = [c.id for c in sub.criteria if not c.not_applicable_if][:2]
        return llm.CheckResult(
            message_type="answer",
            criteria=criteria(lambda c: "missing" if c.id in missing
                              else "not_applicable" if c.not_applicable_if else "evidence"),
            follow_up_needed=True, targets=missing,
            reply="[fake] Could you tell me a bit more? "
                  + " ".join(c.description for c in sub.criteria if c.id in missing),
        )
    return llm.CheckResult(
        message_type="answer",
        criteria=criteria(lambda c: "not_applicable" if c.not_applicable_if else "evidence"),
        follow_up_needed=False, targets=[], reply=None,
    )


def fake_score(sub: Subdimension, position: int) -> dict:
    return {
        "level": FAKE_SCORES[position % len(FAKE_SCORES)],
        "evidence": {c.id: "fake chatbot, no evidence used" for c in sub.criteria},
        "reason": "Fixed score from the fake chatbot (step 3).",
        "why_not_higher": "",
        "flags": ["fake"],
    }

# ---------------------------------------------------------------------------


def next_action(rules: Rules, state: dict, check: dict) -> str:
    """Decide what happens after a check: "clarify", "redirect",
    "follow_up" or "score". The limits come from the rubric rules."""
    message_type = check["message_type"]
    if message_type == "question":
        return "clarify" if state["clarifications"] < rules.max_clarifications else "score"
    if message_type == "off_topic":
        return "redirect" if state["redirects"] < rules.max_redirects else "score"
    # answer, partly_relevant, dont_know
    if (
        check["follow_up_needed"]
        and state["follow_ups"] < rules.max_follow_ups
        and state["follow_ups_total"] < rules.max_follow_ups_total
    ):
        return "follow_up"
    return "score"


def handle_answer(rubric: Rubric, state: dict, log: dict, answer: str) -> tuple[str, bool, bool]:
    """Run one step of the flow. Changes state and log in place.

    Returns (chatbot message, done, log_changed).
    """
    subdimensions = all_subdimensions(rubric)
    dimension_name, sub = subdimensions[state["subdimension_index"]]
    state["transcript"].append({"subdimension": sub.name, "role": "respondent", "text": answer})

    # Check the message, then let the code decide what happens next.
    check = llm.check_to_log(fake_check(sub, state["checks"], answer), sub)
    action = next_action(rubric.rules, state, check)
    check["action"] = action  # recorded in the log: what the code did with this check
    state["checks"].append(check)

    if action != "score":
        if action == "clarify":
            state["clarifications"] += 1
        elif action == "redirect":
            state["redirects"] += 1
        else:
            state["follow_ups"] += 1
            state["follow_ups_total"] += 1
        state["transcript"].append({"subdimension": sub.name, "role": "chatbot", "text": check["reply"]})
        return check["reply"], False, False

    # Score the subdimension and write its log entry.
    dimension = next(d for d in rubric.dimensions if d.name == dimension_name)
    position = dimension.subdimensions.index(sub)
    score = fake_score(sub, position)
    state["scores"].setdefault(dimension_name, {})[sub.name] = score["level"]
    log["subdimensions"].append(
        {
            "subdimension": sub.name,
            "transcript": [
                {"role": m["role"], "text": m["text"]}
                for m in state["transcript"]
                if m["subdimension"] == sub.name
            ],
            "checks": state["checks"],
            "counters": {
                "follow_ups": state["follow_ups"],
                "clarifications": state["clarifications"],
                "redirects": state["redirects"],
                "follow_ups_total": state["follow_ups_total"],
            },
            "score": score,
            "review": {"my_level": None, "error_type": None, "note": None},
        }
    )

    # Next subdimension, or finish. follow_ups_total is kept.
    state["subdimension_index"] += 1
    state["follow_ups"] = 0
    state["clarifications"] = 0
    state["redirects"] = 0
    state["checks"] = []
    if state["subdimension_index"] == len(subdimensions):
        state["done"] = True
        state["transcript"].append({"subdimension": None, "role": "chatbot", "text": CLOSING})
        return CLOSING, True, True

    _, next_sub = subdimensions[state["subdimension_index"]]
    state["transcript"].append({"subdimension": next_sub.name, "role": "chatbot", "text": next_sub.opening_question})
    return next_sub.opening_question, False, True
