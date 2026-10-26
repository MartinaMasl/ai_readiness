"""FastAPI app running on Cloudflare Workers (Python Workers).

Run locally (from the worker/ folder):
    uv run pywrangler d1 migrations apply ai-readiness --local   (once)
    uv run pywrangler dev

Deploy:
    uv run pywrangler deploy

Every answer is one request: load the state from D1, run one step of the
flow, save the state again. Nothing is kept in memory between requests.

Settings (wrangler.jsonc "vars", overridden locally by .dev.vars):
MODEL_ID, TEMPERATURE, TEST_MODE, DIMENSIONS. Secret: OPENAI_API_KEY.
"""

import uuid

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from workers import asgi

import demo
import flow
import llm
import storage
from report import build_report
from rubric import load_rubric, only_dimensions

# Read once when the Worker starts; the rubric is the same for every conversation.
RUBRIC = load_rubric()

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


def model(request: Request) -> llm.OpenAIModel:
    """The model with the settings of this Worker."""
    # Imported here, not at the top: the openai package uses randomness when
    # it loads, which Workers do not allow while the Worker is starting.
    from openai import AsyncOpenAI

    env = request.scope["env"]
    # One timeout of 120 s for everything: on Workers the package's default
    # 5 s connect timeout also covers waiting for the whole answer, which
    # takes longer than that.
    client = AsyncOpenAI(api_key=env.OPENAI_API_KEY, timeout=120.0)
    return llm.OpenAIModel(client, env.MODEL_ID, float(env.TEMPERATURE))


def dimensions_setting(request: Request) -> list[str]:
    """DIMENSIONS, e.g. "Data" or "Data,People": the dimensions a new
    conversation covers. Empty (the live site): all dimensions."""
    value = getattr(request.scope["env"], "DIMENSIONS", "") or ""
    names = [name.strip() for name in value.split(",") if name.strip()]
    return names or [d.name for d in RUBRIC.dimensions]


def conversation_rubric(state: dict):
    """The rubric with the dimensions this conversation covers (fixed at its start)."""
    return only_dimensions(RUBRIC, state["dimensions"])


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@api.get("")
def home():
    return {"message": "Hello from Cloudflare Workers"}


@api.post("/conversations")
async def start_conversation(request: Request):
    db = database(request)
    conversation_id = str(uuid.uuid4())
    rubric = only_dimensions(RUBRIC, dimensions_setting(request))
    state, log = flow.new_conversation(rubric, conversation_id, storage.now(), model(request).info())
    await storage.save_state(db, conversation_id, state)
    await storage.save_log(db, conversation_id, log)
    return {
        "id": conversation_id,
        "started_at": log["started_at"],
        "intro": flow.INTRO,
        "question": state["transcript"][-1]["text"],
        # For the progress sidebar: all dimensions and subdimensions, and
        # the position of the current subdimension in that list.
        "outline": flow.outline(rubric),
        "position": state["subdimension_index"],
    }


@api.post("/conversations/{conversation_id}/answer")
async def answer(conversation_id: str, body: AnswerRequest, request: Request):
    db = database(request)
    state = await storage.load_state(db, conversation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if state["done"]:
        raise HTTPException(status_code=409, detail="Conversation already finished")

    from openai import APIError  # see model() for why it is imported here

    log = await storage.load_log(db, conversation_id)
    rubric = conversation_rubric(state)
    try:
        message, done, log_changed = await flow.handle_answer(rubric, state, log, body.text.strip(), model(request))
    except (APIError, ValueError) as error:
        # The model call failed or its answer did not pass the checks in
        # llm.py. Nothing is saved, so the respondent can send the answer again.
        print(f"Model step failed for {conversation_id}: {type(error).__name__}: {error}")
        raise HTTPException(status_code=502, detail="The chatbot could not process the answer. Please try again.")

    await storage.save_state(db, conversation_id, state)
    if log_changed:
        await storage.save_log(db, conversation_id, log)
    return {
        "message": message,
        "done": done,
        "position": state["subdimension_index"],
        "check": flow.check_summary(rubric, state) if test_mode(request) else None,
    }


@api.get("/conversations/{conversation_id}/report")
async def report(conversation_id: str, request: Request):
    # Made-up scores for checking the report design (see demo.py).
    if conversation_id == "demo":
        return build_report(RUBRIC, demo.worked_example_scores(RUBRIC))
    if conversation_id == "demo-four":
        rubric = demo.four_dimension_placeholder(RUBRIC)
        return build_report(rubric, demo.worked_example_scores(rubric))

    state = await storage.load_state(database(request), conversation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not state["done"]:
        raise HTTPException(status_code=409, detail="Conversation not finished yet")
    return build_report(conversation_rubric(state), state["scores"])


app.include_router(api)

# Cloudflare calls this entrypoint for every request and passes it to FastAPI.
Default = asgi.entrypoint(app)
