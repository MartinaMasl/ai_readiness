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

Context: the check and the score also get the other subdimensions of the
same dimension, so evidence the respondent gave earlier in the dimension
("as I already said ...") counts. The check sees the earlier ones; the
score sees all of them, because a dimension is scored as a whole when it
ends.

Scoring is not part of handle_answer: when the last subdimension of a
dimension is finished, handle_answer returns the dimension's finished
subdimensions, and main.py scores them in the background with
score_subdimension while the respondent reads the next question.
"""

from datetime import datetime, timezone

from rubric import Rubric, Rules, Subdimension


def now() -> str:
    """The current time (UTC, ISO 8601), stored with every message ("at") so
    the time per conversation and per dimension can be measured."""
    return datetime.now(timezone.utc).isoformat()

# Draft text, to be reviewed by the author.
# Written by the author. {areas} is filled with the dimensions this
# conversation covers (see intro), so a Data-only run does not promise four.
INTRO_TEMPLATE = (
    "Hello! This conversation looks at how ready your organization is for AI. "
    "We'll go through these areas: {areas}. "
    "I'll ask about one topic at a time, sometimes with a short follow-up. "
    "Please describe how things work in your organization today, not what is planned. "
    "If you're not sure about something, just say so. "
    "At the end, you can view your report."
)


def intro(rubric: Rubric) -> str:
    """The intro message, listing the dimensions of this conversation:
    "Data", "Data and People", "Data, People, Strategy and Technology"."""
    names = [dim.name for dim in rubric.dimensions]
    areas = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return INTRO_TEMPLATE.format(areas=areas)
CLOSING = ("Thank you, that was the last question. You can now view your report. "
           "Once you open it, you cannot return to the conversation.")


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
                     model_info: dict, respondent: dict | None = None) -> tuple[dict, dict]:
    """Return the initial state and the log header. model_info: model id,
    settings and prompt versions (OpenAIModel.info()). respondent: code and
    role of the person whose link started it. The log entries of the
    subdimensions are saved separately, one per scored subdimension."""
    _, first = all_subdimensions(rubric)[0]
    state = {
        "subdimension_index": 0,
        "follow_ups": 0,
        "clarifications": 0,
        "redirects": 0,
        "follow_ups_dimension": 0,
        "dimensions": [dim.name for dim in rubric.dimensions],
        "transcript": [
            {"subdimension": None, "role": "chatbot", "text": intro(rubric), "at": started_at},
            {"subdimension": first.name, "role": "chatbot", "text": first.opening_question, "at": started_at},
        ],
        "checks": [],
        "finished": [],
        "done": False,
    }
    log = {
        "id": conversation_id,
        "respondent": respondent,
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


def transition_phrase(rules: Rules, position: int) -> str:
    """The transition phrase for the topic at this position in the
    conversation (0 = the first topic, which has none). The code chooses it,
    not the model, because the model cannot see which phrase it used before:
    the second topic gets the first phrase, the third the second, and so on,
    starting again after the last. "" means no transition phrase."""
    phrases = rules.transition_phrases
    if position == 0 or not phrases:
        return ""
    return phrases[(position - 1) % len(phrases)]


def next_topic(rubric: Rubric, subdimensions: list, index: int) -> dict | None:
    """What the check needs to write the question for the next topic: the
    next subdimension, its transition phrase, and whether a dimension ends
    there. None if the current subdimension is the last one."""
    if index + 1 == len(subdimensions):
        return None
    dimension_name, _ = subdimensions[index]
    next_dimension_name, next_sub = subdimensions[index + 1]
    new_dimension = next_dimension_name != dimension_name
    return {
        "sub": next_sub,
        "phrase": transition_phrase(rubric.rules, index + 1),
        "ended_dimension": dimension_name if new_dimension else None,
        "next_dimension": next_dimension_name if new_dimension else None,
    }


def earlier_in_dimension(rubric: Rubric, state: dict, dimension_name: str, sub: Subdimension) -> list[dict]:
    """The transcripts of the subdimensions before sub in the same dimension."""
    dimension = next(d for d in rubric.dimensions if d.name == dimension_name)
    earlier = dimension.subdimensions[: dimension.subdimensions.index(sub)]
    return [{"subdimension": s.name, "messages": messages_of(state, s)} for s in earlier]


def messages_of(state: dict, sub: Subdimension) -> list[dict]:
    """The transcript of one subdimension, as the prompts get it."""
    return [
        {"role": m["role"], "text": m["text"], "at": m.get("at")}
        for m in state["transcript"]
        if m["subdimension"] == sub.name
    ]


async def handle_answer(rubric: Rubric, state: dict, answer: str, model) -> tuple[str, bool, list[dict]]:
    """Run one step of the flow. Changes the state in place.

    Returns (chatbot message, done, to_score): to_score is the finished
    subdimensions of a dimension that has just ended, to be scored with
    score_subdimension; otherwise an empty list.
    """
    subdimensions = all_subdimensions(rubric)
    dimension_name, sub = subdimensions[state["subdimension_index"]]
    state["transcript"].append({"subdimension": sub.name, "role": "respondent", "text": answer, "at": now()})

    # Check the message, then let the code decide what happens next.
    is_last = state["subdimension_index"] + 1 == len(subdimensions)
    topic = next_topic(rubric, subdimensions, state["subdimension_index"])
    context = earlier_in_dimension(rubric, state, dimension_name, sub)
    check = await model.check(rubric, sub, messages_of(state, sub), topic, context)
    action = next_action(rubric.rules, state, check)
    check["action"] = action  # recorded in the log: what the code did with this check
    # Recorded in the log: the phrase the code chose for next_question.
    check["transition_phrase"] = topic["phrase"] if topic else None
    state["checks"].append(check)

    if action != "score":
        if action == "clarify":
            state["clarifications"] += 1
        elif action == "redirect":
            state["redirects"] += 1
        else:
            state["follow_ups"] += 1
            state["follow_ups_dimension"] += 1
        state["transcript"].append({"subdimension": sub.name, "role": "chatbot", "text": check["reply"], "at": now()})
        return check["reply"], False, []

    # The subdimension is finished. Keep what is needed to score it; the
    # score itself is done when the dimension ends (score_subdimension).
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
    dimension_ended = is_last or subdimensions[state["subdimension_index"] + 1][0] != dimension_name
    to_score = [f for f in state["finished"] if f["dimension"] == dimension_name] if dimension_ended else []

    # Next subdimension, or finish.
    state["subdimension_index"] += 1
    state["follow_ups"] = 0
    state["clarifications"] = 0
    state["redirects"] = 0
    state["checks"] = []
    if state["subdimension_index"] == len(subdimensions):
        state["done"] = True
        state["transcript"].append({"subdimension": None, "role": "chatbot", "text": CLOSING, "at": now()})
        return CLOSING, True, to_score

    next_dimension_name, next_sub = subdimensions[state["subdimension_index"]]
    if next_dimension_name != dimension_name:
        state["follow_ups_dimension"] = 0  # a new dimension has its own follow-up budget
    question = check["next_question"]  # written by the model in the check
    state["transcript"].append({"subdimension": next_sub.name, "role": "chatbot", "text": question, "at": now()})
    return question, False, to_score


async def score_subdimension(rubric: Rubric, finished: dict, model, dimension_records: list[dict]) -> dict:
    """Score a finished subdimension, with the other finished subdimensions
    of its dimension (dimension_records) as context. Returns its log entry;
    the level is entry["score"]["level"]."""
    sub = next(s for _, s in all_subdimensions(rubric) if s.name == finished["subdimension"])
    context = [
        {"subdimension": r["subdimension"], "messages": r["transcript"]}
        for r in dimension_records
        if r["subdimension"] != finished["subdimension"]
    ]
    score = await model.score(rubric, sub, finished["transcript"], context)
    return {
        "subdimension": finished["subdimension"],
        "core_question": finished["core_question"],
        "context": [part["subdimension"] for part in context],
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
