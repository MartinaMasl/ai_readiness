"""The conversation flow, one step per request.

Code controls the flow: ask the opening question, check the answer,
decide what happens next (next_action), score, move to the next
subdimension, and finish. The model only reports what the message is and
what it covers; the limits in the rubric rules are applied here.

The check and the score come from `model` (llm.OpenAIModel in the Worker,
a fake in the tests). Each gets only the messages of the current
subdimension. Only the very first question is fixed (the first
opening_question); every later question is written by the model: the
check also writes the opening of the next subdimension, which is shown
if the code moves on.

The state is a plain dict so it can be saved to D1 as JSON:
    subdimension_index  position in the list of all subdimensions
    follow_ups          follow-ups asked in the current subdimension
    clarifications      respondent questions answered in the current subdimension
    redirects           off-topic messages steered back in the current subdimension
    follow_ups_dimension  follow-ups asked in the current dimension
    dimensions          names of the dimensions this conversation covers
    transcript          all messages, each tagged with its subdimension
    checks              check results of the current subdimension
    finished            the finished subdimensions, each with what is needed
                        to score it (see handle_answer)
    done                True when the last subdimension is finished

Scoring is not part of handle_answer: when a subdimension is finished,
handle_answer returns it, and main.py scores it in the background with
score_subdimension while the respondent reads the next question.
"""

from rubric import Rubric, Rules, Subdimension

# Draft text, to be reviewed by the author.
INTRO = (
    "Hello! This conversation assesses how ready your organization is for AI. "
    "I will ask one question per topic and sometimes a short follow-up. "
    "Please describe how things work in your organization today, not what is planned."
)
CLOSING = "Thank you, that was the last question. Your results are ready."


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


def new_conversation(rubric: Rubric, conversation_id: str, started_at: str,
                     model_info: dict) -> tuple[dict, dict]:
    """Return the initial state and the log header. model_info: model id,
    settings and prompt versions (OpenAIModel.info()). The log entries of
    the subdimensions are saved separately, one per scored subdimension."""
    _, first = all_subdimensions(rubric)[0]
    state = {
        "subdimension_index": 0,
        "follow_ups": 0,
        "clarifications": 0,
        "redirects": 0,
        "follow_ups_dimension": 0,
        "dimensions": [dim.name for dim in rubric.dimensions],
        "transcript": [
            {"subdimension": None, "role": "chatbot", "text": INTRO},
            {"subdimension": first.name, "role": "chatbot", "text": first.opening_question},
        ],
        "checks": [],
        "finished": [],
        "done": False,
    }
    log = {
        "id": conversation_id,
        "started_at": started_at,
        "model": model_info["model"],
        "rubric_version": rubric.version,
        "prompt_versions": model_info["prompt_versions"],
    }
    return state, log


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
        and state["follow_ups_dimension"] < rules.max_follow_ups_per_dimension
    ):
        return "follow_up"
    return "score"


def messages_of(state: dict, sub: Subdimension) -> list[dict]:
    """The transcript of one subdimension, as the prompts get it."""
    return [
        {"role": m["role"], "text": m["text"]}
        for m in state["transcript"]
        if m["subdimension"] == sub.name
    ]


async def handle_answer(rubric: Rubric, state: dict, answer: str, model) -> tuple[str, bool, dict | None]:
    """Run one step of the flow. Changes the state in place.

    Returns (chatbot message, done, finished): finished is the subdimension
    that has just ended, to be scored with score_subdimension, or None.
    """
    subdimensions = all_subdimensions(rubric)
    dimension_name, sub = subdimensions[state["subdimension_index"]]
    state["transcript"].append({"subdimension": sub.name, "role": "respondent", "text": answer})

    # Check the message, then let the code decide what happens next.
    is_last = state["subdimension_index"] + 1 == len(subdimensions)
    next_sub = None if is_last else subdimensions[state["subdimension_index"] + 1][1]
    check = await model.check(rubric, sub, messages_of(state, sub), next_sub)
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
            state["follow_ups_dimension"] += 1
        state["transcript"].append({"subdimension": sub.name, "role": "chatbot", "text": check["reply"]})
        return check["reply"], False, None

    # The subdimension is finished. Keep what is needed to score it; the
    # score itself is done afterwards (score_subdimension).
    finished = {
        "position": state["subdimension_index"],
        "dimension": dimension_name,
        "subdimension": sub.name,
        # The rubric's question; the first chatbot message in the transcript
        # is what was actually asked (written by the model, except the very first).
        "core_question": sub.opening_question,
        "transcript": messages_of(state, sub),
        "checks": state["checks"],
        "counters": {
            "follow_ups": state["follow_ups"],
            "clarifications": state["clarifications"],
            "redirects": state["redirects"],
            "follow_ups_dimension": state["follow_ups_dimension"],
        },
    }
    state["finished"].append(finished)

    # Next subdimension, or finish.
    state["subdimension_index"] += 1
    state["follow_ups"] = 0
    state["clarifications"] = 0
    state["redirects"] = 0
    state["checks"] = []
    if state["subdimension_index"] == len(subdimensions):
        state["done"] = True
        state["transcript"].append({"subdimension": None, "role": "chatbot", "text": CLOSING})
        return CLOSING, True, finished

    next_dimension_name, next_sub = subdimensions[state["subdimension_index"]]
    if next_dimension_name != dimension_name:
        state["follow_ups_dimension"] = 0  # a new dimension has its own follow-up budget
    question = check["next_question"]  # written by the model in the check
    state["transcript"].append({"subdimension": next_sub.name, "role": "chatbot", "text": question})
    return question, False, finished


async def score_subdimension(rubric: Rubric, finished: dict, model) -> dict:
    """Score a finished subdimension. Returns its log entry; the level is
    entry["score"]["level"]."""
    sub = next(s for _, s in all_subdimensions(rubric) if s.name == finished["subdimension"])
    score = await model.score(rubric, sub, finished["transcript"])
    return {
        "subdimension": finished["subdimension"],
        "core_question": finished["core_question"],
        "transcript": finished["transcript"],
        "checks": finished["checks"],
        "counters": finished["counters"],
        "score": score,
        "review": {"my_level": None, "error_type": None, "note": None},
    }


def unscored(state: dict, levels: dict[str, dict[str, int]]) -> list[dict]:
    """The finished subdimensions that have no saved level yet."""
    return [
        f for f in state["finished"]
        if f["subdimension"] not in levels.get(f["dimension"], {})
    ]
