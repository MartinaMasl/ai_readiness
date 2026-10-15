"""FastAPI app running on Cloudflare Workers (Python Workers).

Run locally (from the worker/ folder):
    uv run pywrangler d1 migrations apply ai-readiness --local   (once)
    uv run pywrangler dev

Deploy:
    uv run pywrangler deploy

Every answer is one request: load the state from D1, run one step of the
flow, save the state again. Nothing is kept in memory between requests.
"""

import uuid

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from workers import asgi

import demo
import flow
import storage
from report import build_report
from rubric import load_rubric

# Read once when the Worker starts; the rubric is the same for every conversation.
RUBRIC = load_rubric()

app = FastAPI()

# All endpoints live under /api.
api = APIRouter(prefix="/api")


def database(request: Request):
    """The D1 binding "DB" from wrangler.jsonc."""
    return request.scope["env"].DB


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@api.get("")
def home():
    return {"message": "Hello from Cloudflare Workers"}


@api.post("/conversations")
async def start_conversation(request: Request):
    db = database(request)
    conversation_id = str(uuid.uuid4())
    state, log = flow.new_conversation(RUBRIC, conversation_id, storage.now())
    await storage.save_state(db, conversation_id, state)
    await storage.save_log(db, conversation_id, log)
    return {
        "id": conversation_id,
        "started_at": log["started_at"],
        "intro": flow.INTRO,
        "question": state["transcript"][-1]["text"],
        # For the progress sidebar: all dimensions and subdimensions, and
        # the position of the current subdimension in that list.
        "outline": flow.outline(RUBRIC),
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

    log = await storage.load_log(db, conversation_id)
    message, done, log_changed = flow.handle_answer(RUBRIC, state, log, body.text.strip())

    await storage.save_state(db, conversation_id, state)
    if log_changed:
        await storage.save_log(db, conversation_id, log)
    return {
        "message": message,
        "done": done,
        "position": state["subdimension_index"],
        "check": flow.check_summary(RUBRIC, state),
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
    return build_report(RUBRIC, state["scores"])


app.include_router(api)

# Cloudflare calls this entrypoint for every request and passes it to FastAPI.
Default = asgi.entrypoint(app)
