"""The conversation flow, one step per request.

Code controls the flow: ask the opening question, check the answer, ask
a follow-up while something is missing (at most max_follow_ups), score,
move to the next subdimension, and finish. If the respondent asks for
clarification instead of answering, the follow-up explains and asks again;
it counts towards the same limit.

Step 3: the check and the score are FAKE (fake_check, fake_score). Step 5
replaces only these two functions with the model calls; the rest of the
flow stays the same.

The state is a plain dict so it can be saved to D1 as JSON:
    subdimension_index  position in the list of all subdimensions
    follow_ups          follow-ups (including re-asked questions after a
                        clarification request) in the current subdimension
    transcript          all messages, each tagged with its subdimension
    checks              check results of the current subdimension
    scores              {dimension: {subdimension: level}}
    done                True when every subdimension is scored
"""

from rubric import Criterion, Rubric, Subdimension

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
    """What the chat page shows with a follow-up: how many criteria the last
    check found covered, and the criterion the follow-up targets. None when
    no follow-up is open.

    After a subdimension is scored, state["checks"] is empty again, so a
    non-empty list means the last message was a follow-up. A re-asked
    question after a clarification request gets no check box.
    """
    if state["done"] or not state["checks"]:
        return None
    if state["checks"][-1]["message_type"] == "clarification_request":
        return None
    _, sub = all_subdimensions(rubric)[state["subdimension_index"]]
    check = state["checks"][-1]
    target = next(c for c in sub.criteria if c.id == check["targets"])
    return {
        "subdimension": sub.name,
        "covered": sum(1 for c in check["criteria"].values() if c["covered"]),
        "total": len(check["criteria"]),
        "follow_up_criterion": target.description,
    }


def new_conversation(rubric: Rubric, conversation_id: str, started_at: str) -> tuple[dict, dict]:
    """Return the initial state and the initial log."""
    _, first = all_subdimensions(rubric)[0]
    state = {
        "subdimension_index": 0,
        "follow_ups": 0,
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

def fake_check(sub: Subdimension, follow_ups: int, answer: str) -> dict:
    """A message ending in "?" is a clarification request: a placeholder
    reply (the real model answers the actual question), then the opening
    question again. Otherwise the first answer
    covers nothing (follow-up about the first criterion) and after one
    follow-up everything is covered. Shows the follow-up loop without a model."""
    if answer.endswith("?"):
        return {
            "message_type": "clarification_request",
            "criteria": {c.id: {"covered": False, "quote": None} for c in sub.criteria},
            "all_covered": False,
            "follow_up": f"[fake] The model would answer your question here. {sub.opening_question}",
            "targets": None,
        }
    covered = follow_ups >= 1
    target: Criterion = sub.criteria[0]
    return {
        "message_type": "answer",
        "criteria": {c.id: {"covered": covered, "quote": None} for c in sub.criteria},
        "all_covered": covered,
        "follow_up": None if covered else f"Thank you. Could you tell me a bit more? {target.description}",
        "targets": None if covered else target.id,
    }


def fake_score(sub: Subdimension, position: int) -> dict:
    return {
        "level": FAKE_SCORES[position % len(FAKE_SCORES)],
        "evidence": {c.id: "fake chatbot, no evidence used" for c in sub.criteria},
        "reason": "Fixed score from the fake chatbot (step 3).",
        "why_not_higher": "",
        "flags": ["fake"],
    }

# ---------------------------------------------------------------------------


def handle_answer(rubric: Rubric, state: dict, log: dict, answer: str) -> tuple[str, bool, bool]:
    """Run one step of the flow. Changes state and log in place.

    Returns (chatbot message, done, log_changed).
    """
    subdimensions = all_subdimensions(rubric)
    dimension_name, sub = subdimensions[state["subdimension_index"]]
    state["transcript"].append({"subdimension": sub.name, "role": "respondent", "text": answer})

    # Check: is every criterion covered? If not, or if the respondent asked
    # for clarification, ask a follow-up. Clarifications and follow-ups
    # share one limit (max_follow_ups).
    check = fake_check(sub, state["follow_ups"], answer)
    state["checks"].append(check)
    needs_follow_up = check["message_type"] == "clarification_request" or not check["all_covered"]
    if needs_follow_up and state["follow_ups"] < rubric.rules.max_follow_ups:
        state["follow_ups"] += 1
        state["transcript"].append({"subdimension": sub.name, "role": "chatbot", "text": check["follow_up"]})
        return check["follow_up"], False, False

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
            "score": score,
            "review": {"my_level": None, "error_type": None, "note": None},
        }
    )

    # Next subdimension, or finish.
    state["subdimension_index"] += 1
    state["follow_ups"] = 0
    state["checks"] = []
    if state["subdimension_index"] == len(subdimensions):
        state["done"] = True
        state["transcript"].append({"subdimension": None, "role": "chatbot", "text": CLOSING})
        return CLOSING, True, True

    _, next_sub = subdimensions[state["subdimension_index"]]
    state["transcript"].append({"subdimension": next_sub.name, "role": "chatbot", "text": next_sub.opening_question})
    return next_sub.opening_question, False, True
