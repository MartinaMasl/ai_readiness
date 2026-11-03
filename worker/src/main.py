"""FastAPI app running on Cloudflare Workers (Python Workers).

Run locally (from the worker/ folder):
    uv run pywrangler d1 migrations apply ai-readiness --local   (once)
    uv run pywrangler dev

Deploy:
    uv run pywrangler deploy

Every answer is one request: load the state from D1, run one step of the
flow, save the state again. Nothing is kept in memory between requests.
When a subdimension is finished, it is scored in the background (after
the response is sent), so the respondent does not wait for the score.

Settings (wrangler.jsonc "vars", overridden locally by .dev.vars):
MODEL_ID, TEMPERATURE, TEST_MODE, DIMENSIONS. Secret: OPENAI_API_KEY.
"""

import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from workers import asgi, fetch

import comparison
import demo
import flow
import llm
import questionnaire
import storage
import study
from report import build_report
from rubric import load_rubric, only_dimensions

# Read once when the Worker starts; the rubric is the same for every conversation.
RUBRIC = load_rubric()
# The questionnaire's questions, checked against the rubric when the Worker starts.
QUESTIONNAIRE = questionnaire.load_questionnaire(RUBRIC)
# The comparison questionnaire's content, checked when the Worker starts.
COMPARISON = comparison.load_comparison()

app = FastAPI()

# All endpoints live under /api.
api = APIRouter(prefix="/api")


def database(request: Request):
    """The D1 binding "DB" from wrangler.jsonc."""
    return request.scope["env"].DB


def test_mode(request: Request) -> bool:
    """TEST_MODE is "0" in wrangler.jsonc (live site) and can be set to "1"
    locally in .dev.vars. Only in test mode does the API return the check
    details, so respondents never receive them."""
    return getattr(request.scope["env"], "TEST_MODE", "0") == "1"


# If a finished subdimension has no saved score after this many seconds,
# the report scores it itself (the background score failed).
BACKGROUND_SCORE_SECONDS = 60


async def send_with_fetch(url: str, headers: dict, body: dict) -> tuple[int, dict]:
    """Send one request with the Worker's built-in fetch. No package is
    loaded for it, so it costs almost no CPU time (the free plan allows
    10 ms per request; waiting for OpenAI does not count)."""
    response = await fetch(url, method="POST", headers=headers, body=json.dumps(body))
    text = await response.text()
    try:
        reply = json.loads(text)
    except ValueError:
        reply = {}
    return response.status, reply


def model(env) -> llm.OpenAIModel:
    """The model with the settings of this Worker. env: request.scope["env"]."""
    return llm.OpenAIModel(send_with_fetch, env.OPENAI_API_KEY, env.MODEL_ID, float(env.TEMPERATURE))


def dimensions_setting(request: Request) -> list[str]:
    """DIMENSIONS, e.g. "Data" or "Data,People": the dimensions a new
    conversation covers. Empty (the live site): all dimensions."""
    value = getattr(request.scope["env"], "DIMENSIONS", "") or ""
    names = [name.strip() for name in value.split(",") if name.strip()]
    return names or [d.name for d in RUBRIC.dimensions]


def conversation_rubric(state: dict):
    """The rubric with the dimensions this conversation covers (fixed at its start)."""
    return only_dimensions(RUBRIC, state["dimensions"])


class StartRequest(BaseModel):
    # The token from the personal link (/?r=<token>).
    respondent: str = Field(min_length=1, max_length=100)


async def respondent_for(request: Request, token: str) -> dict:
    """The respondent of a personal link; 403 for an unknown token."""
    person = await storage.find_respondent(database(request), token)
    if person is None:
        raise HTTPException(status_code=403, detail="This link is not valid.")
    return person


async def study_steps(request: Request, person: dict) -> tuple[list[dict], dict]:
    """The respondent's three steps (study.py) and their assessment rows."""
    db = database(request)
    rows = await storage.assessments(db, person["code"])
    answers = await storage.count_feedback_answers(db, person["code"])
    return study.steps(person, rows, answers), rows


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@api.get("")
def home():
    return {"message": "Hello from Cloudflare Workers"}


# --- The study: home page ----------------------------------------------------

@api.get("/study")
async def study_status(r: str, request: Request):
    """Everything the home page shows: consent, the three steps in the
    respondent's order with their status, and the number of topics."""
    person = await respondent_for(request, r)
    steps, rows = await study_steps(request, person)
    topics = sum(len(d.subdimensions) for d in only_dimensions(RUBRIC, dimensions_setting(request)).dimensions)
    for step in steps:
        if step["key"] in ("chatbot", "questionnaire") and step["status"] == "in_progress":
            state = await storage.load_state(database(request), rows[step["key"]]["id"])
            total = sum(len(d["subdimensions"]) for d in flow.outline(conversation_rubric(state)))
            if step["key"] == "chatbot":
                step["progress"] = f"{state['subdimension_index']} of {total} topics answered"
            else:
                step["progress"] = f"{len(state['answers'])} of {total} questions answered"
    return {
        "consent": person["consent_at"] is not None,
        "complete": person["feedback_submitted_at"] is not None,
        "topics": topics,
        "steps": steps,
    }


@api.post("/study/consent")
async def give_consent(body: StartRequest, request: Request):
    person = await respondent_for(request, body.respondent)
    await storage.set_consent(database(request), person["code"])
    return {"consent": True}


# --- The chatbot ---------------------------------------------------------------

@api.post("/conversations")
async def start_conversation(body: StartRequest, request: Request):
    """Start the respondent's chatbot conversation, or return the one they
    already have (each respondent has at most one; "Continue" resumes it)."""
    db = database(request)
    person = await respondent_for(request, body.respondent)
    steps, rows = await study_steps(request, person)
    if rows["chatbot"]:
        return {"id": rows["chatbot"]["id"]}
    if not study.can_start(steps, "chatbot"):
        step = next(s for s in steps if s["key"] == "chatbot")
        raise HTTPException(status_code=409, detail=step["reason"] or "This assessment is not available.")
    conversation_id = str(uuid.uuid4())
    rubric = only_dimensions(RUBRIC, dimensions_setting(request))
    state, log = flow.new_conversation(rubric, conversation_id, storage.now(),
                                       model(request.scope["env"]).info(),
                                       {"code": person["code"], "role": person["role"]})
    await storage.create_conversation(db, conversation_id, state, person["code"], "chatbot")
    await storage.save_log(db, conversation_id, log)
    return {"id": conversation_id}


@api.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, r: str, request: Request):
    """The conversation so far, so the chat page can show it and continue."""
    person = await respondent_for(request, r)
    info = await storage.assessment(database(request), conversation_id)
    if info is None or info["respondent_code"] != person["code"] or info["format"] != "chatbot":
        raise HTTPException(status_code=404, detail="Conversation not found")
    state = await storage.load_state(database(request), conversation_id)
    rubric = conversation_rubric(state)
    positions = {sub.name: i for i, (_, sub) in enumerate(flow.all_subdimensions(rubric))}
    return {
        "id": conversation_id,
        "started_at": info["created_at"],
        # For the progress sidebar: all dimensions and subdimensions, and
        # the position of the current subdimension in that list.
        "outline": flow.outline(rubric),
        "position": state["subdimension_index"],
        "done": state["done"],
        # Each message with the position of its subdimension (None for the
        # intro and the closing message), so the page can show the dividers.
        "messages": [
            {"role": m["role"], "text": m["text"], "position": positions.get(m["subdimension"])}
            for m in state["transcript"]
        ],
    }


# --- The questionnaire ---------------------------------------------------------

class QuestionnaireAnswer(BaseModel):
    respondent: str = Field(min_length=1, max_length=100)
    item: str = Field(min_length=1, max_length=20)  # question id, e.g. "Q01"
    option: int = Field(ge=0)  # index of the chosen option in questionnaire.json


@api.post("/questionnaire/start")
async def start_questionnaire(body: StartRequest, request: Request):
    """Start the respondent's questionnaire, or return the one they already
    have (each respondent has at most one; "Continue" resumes it)."""
    db = database(request)
    person = await respondent_for(request, body.respondent)
    steps, rows = await study_steps(request, person)
    if rows["questionnaire"]:
        return {"id": rows["questionnaire"]["id"]}
    if not study.can_start(steps, "questionnaire"):
        step = next(s for s in steps if s["key"] == "questionnaire")
        raise HTTPException(status_code=409, detail=step["reason"] or "This assessment is not available.")
    conversation_id = str(uuid.uuid4())
    rubric = only_dimensions(RUBRIC, dimensions_setting(request))
    # The same state fields the report needs from a chatbot conversation
    # (dimensions, done, finished), plus the answers.
    state = {"dimensions": [d.name for d in rubric.dimensions], "answers": {}, "done": False, "finished": []}
    await storage.create_conversation(db, conversation_id, state, person["code"], "questionnaire")
    await storage.save_log(db, conversation_id, {
        "id": conversation_id,
        "format": "questionnaire",
        "respondent": {"code": person["code"], "role": person["role"]},
        "started_at": storage.now(),
        "questionnaire_version": QUESTIONNAIRE["version"],
        "rubric_version": RUBRIC.version,
    })
    return {"id": conversation_id}


async def open_questionnaire(request: Request, token: str) -> tuple[dict, dict, list[dict], dict]:
    """The respondent's questionnaire: (its row, its state, its questions,
    the respondent). 404 if not started."""
    person = await respondent_for(request, token)
    rows = await storage.assessments(database(request), person["code"])
    row = rows["questionnaire"]
    if row is None:
        raise HTTPException(status_code=404, detail="The questionnaire has not been started")
    state = await storage.load_state(database(request), row["id"])
    return row, state, questionnaire.items(QUESTIONNAIRE, conversation_rubric(state)), person


@api.get("/questionnaire")
async def get_questionnaire(r: str, request: Request):
    """The questions (without their scores), the answers so far and where to
    continue."""
    row, state, questions, person = await open_questionnaire(request, r)
    steps, _ = await study_steps(request, person)
    step = next(s for s in steps if s["key"] == "questionnaire")
    return {
        "id": row["id"],
        "position": step["position"],  # "Assessment 1 of 2" / "2 of 2"
        "dimensions": state["dimensions"],
        "questions": questionnaire.for_page(questions),
        "answers": {item: answer["option"] for item, answer in state["answers"].items()},
        "resume_at": questionnaire.first_unanswered(questions, state["answers"]),
        "done": state["done"],
        "finished_at": row["finished_at"],
    }


@api.post("/questionnaire/answer")
async def answer_question(body: QuestionnaireAnswer, request: Request):
    """Save one answer immediately. Answers can be changed until the
    respondent finishes."""
    row, state, questions, _ = await open_questionnaire(request, body.respondent)
    if state["done"]:
        raise HTTPException(status_code=409, detail="The questionnaire is already finished")
    question = next((q for q in questions if q["item"]["id"] == body.item), None)
    if question is None or body.option >= len(question["item"]["options"]):
        raise HTTPException(status_code=400, detail="Unknown question or option")
    timestamp = storage.now()
    earlier = state["answers"].get(body.item)
    state["answers"][body.item] = {
        "option": body.option,
        "first_answered_at": earlier["first_answered_at"] if earlier else timestamp,
        "answered_at": timestamp,
    }
    await storage.save_state(database(request), row["id"], state)
    return {"answered": len(state["answers"]), "total": len(questions)}


@api.post("/questionnaire/finish")
async def finish_questionnaire(body: StartRequest, request: Request):
    """Score every subdimension from its answer and save the results like
    the chatbot's, so the same report code works for both."""
    db = database(request)
    row, state, questions, _ = await open_questionnaire(request, body.respondent)
    if not state["done"]:
        if questionnaire.first_unanswered(questions, state["answers"]) < len(questions):
            raise HTTPException(status_code=409, detail="Not every question is answered yet")
        for finished, entry in questionnaire.results(questions, state["answers"]):
            await storage.save_result(db, row["id"], finished, entry)
        state["done"] = True
        await storage.save_state(db, row["id"], state)
        await storage.set_finished(db, row["id"])
        log = await storage.load_log(db, row["id"])
        log["answers"] = state["answers"]  # the raw answers, for the author
        await storage.save_log(db, row["id"], log)
    return {"id": row["id"]}


# --- The comparison questionnaire ----------------------------------------------

class ComparisonSection(BaseModel):
    respondent: str = Field(min_length=1, max_length=100)
    section: str = Field(min_length=1, max_length=50)  # section id in comparison.json
    # {question id: option index (scale, choice) or text}
    answers: dict[str, int | str | None]


async def open_comparison(request: Request, token: str) -> tuple[dict, list[dict], dict]:
    """(respondent, their sections in order, their answers so far). 409 if
    the feedback is not available yet (both assessments must be finished)."""
    person = await respondent_for(request, token)
    steps, _ = await study_steps(request, person)
    feedback = next(s for s in steps if s["key"] == "feedback")
    if feedback["status"] == "locked":
        raise HTTPException(status_code=409, detail=feedback["reason"])
    answers = await storage.comparison_answers(database(request), person["code"])
    return person, comparison.for_respondent(COMPARISON, person["format_order"]), answers


@api.get("/comparison")
async def get_comparison(r: str, request: Request):
    """The sections in the respondent's order, the answers so far and where
    to continue."""
    person, sections, answers = await open_comparison(request, r)
    return {
        "version": COMPARISON["version"],
        "sections": sections,
        "answers": {qid: (answer if _is_text(qid) else int(answer)) for qid, answer in answers.items()},
        "resume_at": comparison.resume_at(sections, answers),
        "submitted": person["feedback_submitted_at"] is not None,
    }


def _is_text(question_id: str) -> bool:
    return any(item["id"] == question_id and item["type"] == "text"
               for section in COMPARISON["sections"] for item in section["items"])


@api.post("/comparison/section")
async def save_comparison_section(body: ComparisonSection, request: Request):
    """Save one section's answers (every question that is not optional must
    be answered). Answers can be changed until the respondent submits."""
    person, sections, _ = await open_comparison(request, body.respondent)
    if person["feedback_submitted_at"]:
        raise HTTPException(status_code=409, detail="The feedback is already submitted")
    section = next((s for s in sections if s["id"] == body.section), None)
    if section is None:
        raise HTTPException(status_code=400, detail="Unknown section")
    try:
        checked = {item["id"]: comparison.check_answer(item, body.answers.get(item["id"])) for item in section["items"]}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    given = {qid: answer for qid, answer in checked.items() if answer is not None}
    if not comparison.section_complete(section, given):
        raise HTTPException(status_code=400, detail="Please answer every question that is not optional")
    # An optional text left empty is saved as "", so clearing an earlier text works.
    for item in section["items"]:
        if item["type"] == "text" and item["id"] not in given:
            given[item["id"]] = ""
    for qid, answer in given.items():
        await storage.save_comparison_answer(database(request), person["code"], qid, answer, COMPARISON["version"])
    return {"saved": len(given)}


@api.post("/comparison/submit")
async def submit_comparison(body: StartRequest, request: Request):
    """Finish the study: every section must be complete."""
    person, sections, answers = await open_comparison(request, body.respondent)
    if comparison.resume_at(sections, answers) < len(sections):
        raise HTTPException(status_code=409, detail="Not every section is complete yet")
    await storage.set_feedback_submitted(database(request), person["code"])
    return {"submitted": True}


async def score_and_save(env, conversation_id: str, rubric, finished: dict, dimension_records: list[dict]) -> None:
    """Score a finished subdimension and save its result (level and log entry)."""
    entry = await flow.score_subdimension(rubric, finished, model(env), dimension_records)
    await storage.save_result(env.DB, conversation_id, finished, entry)


async def score_in_background(env, conversation_id: str, rubric, finished: dict, dimension_records: list[dict]) -> None:
    """Runs after the response is sent. One retry; if both attempts fail,
    the report scores the subdimension later (see report())."""
    for attempt in (1, 2):
        try:
            started = datetime.now()
            await score_and_save(env, conversation_id, rubric, finished, dimension_records)
            seconds = (datetime.now() - started).total_seconds()
            print(f"Background score saved for {finished['subdimension']} in {seconds:.1f}s")
            return
        except Exception as error:
            print(f"Background score failed ({attempt}/2) for {conversation_id}, "
                  f"{finished['subdimension']}: {type(error).__name__}: {error}")


async def score_dimension_in_background(env, conversation_id: str, rubric, to_score: list[dict]) -> None:
    """Score the subdimensions of an ended dimension at the same time, each
    with the others as context."""
    await asyncio.gather(*[
        score_in_background(env, conversation_id, rubric, finished, to_score) for finished in to_score
    ])


@api.post("/conversations/{conversation_id}/answer")
async def answer(conversation_id: str, body: AnswerRequest, request: Request):
    from workers import wait_until

    env = request.scope["env"]
    db = database(request)
    state = await storage.load_state(db, conversation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if state["done"]:
        raise HTTPException(status_code=409, detail="Conversation already finished")
    info = await storage.assessment(db, conversation_id)
    if info["excluded"]:
        raise HTTPException(status_code=409, detail="This conversation is no longer part of the study")

    rubric = conversation_rubric(state)
    try:
        message, done, to_score = await flow.handle_answer(rubric, state, body.text.strip(), model(env))
    except ValueError as error:
        # The model call failed (llm.ModelCallError) or its answer did not
        # pass the checks in llm.py. Nothing is saved, so the respondent can send the answer again.
        print(f"Model step failed for {conversation_id}: {type(error).__name__}: {error}")
        raise HTTPException(status_code=502, detail="The chatbot could not process the answer. Please try again.")

    for finished in to_score:
        finished["finished_at"] = storage.now()  # when scoring started (see report())
    await storage.save_state(db, conversation_id, state)
    if done:
        await storage.set_finished(db, conversation_id)
    if to_score:
        # A dimension has ended: score its subdimensions together, after the
        # response is sent; the Worker keeps running until they are done.
        wait_until(score_dimension_in_background(env, conversation_id, rubric, to_score))
    return {
        "message": message,
        "done": done,
        "position": state["subdimension_index"],
        "check": flow.check_summary(rubric, state) if test_mode(request) else None,
    }


@api.get("/conversations/{conversation_id}/report")
async def report(conversation_id: str, request: Request, view: int = 0):
    """The report of one assessment (chatbot or questionnaire), from its own
    subdimension_results only. view=1: opened on the report page itself
    (not the compare page), which is stored the first time."""
    # Made-up scores for checking the report design (see demo.py).
    if conversation_id == "demo":
        return build_report(RUBRIC, demo.worked_example_scores(RUBRIC))
    if conversation_id == "demo-four":
        rubric = demo.four_dimension_placeholder(RUBRIC)
        return build_report(rubric, demo.worked_example_scores(rubric))

    env = request.scope["env"]
    db = database(request)
    state = await storage.load_state(db, conversation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not state["done"]:
        raise HTTPException(status_code=409, detail="Conversation not finished yet")

    rubric = conversation_rubric(state)
    levels = await storage.load_levels(db, conversation_id)
    missing = flow.unscored(state, levels)
    if missing:
        now = datetime.fromisoformat(storage.now())
        waiting = [f for f in missing
                   if (now - datetime.fromisoformat(f["finished_at"])).total_seconds() < BACKGROUND_SCORE_SECONDS]
        if waiting:
            # The background score is probably still running; the page asks again.
            return JSONResponse(status_code=202, content={"status": "scoring"})
        # The background score failed: score what is missing now.
        for finished in missing:
            dimension_records = [f for f in state["finished"] if f["dimension"] == finished["dimension"]]
            await score_and_save(env, conversation_id, rubric, finished, dimension_records)
        levels = await storage.load_levels(db, conversation_id)
    result = build_report(rubric, levels)
    info = await storage.assessment(db, conversation_id)
    # Which assessment this report belongs to, for the heading on the page.
    result["assessment"] = {"format": info["format"], "finished_at": info["finished_at"]}
    if view:
        await storage.set_report_viewed(db, conversation_id)
    return result


app.include_router(api)

# Cloudflare calls this entrypoint for every request and passes it to FastAPI.
Default = asgi.entrypoint(app)
