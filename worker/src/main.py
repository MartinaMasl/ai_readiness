"""Minimal FastAPI app running on Cloudflare Workers (Python Workers).

Run locally (from the worker/ folder):
    uv run pywrangler dev
    curl http://localhost:8787/api

Deploy:
    uv run pywrangler deploy
"""

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from workers import asgi

import demo
from report import build_report
from rubric import load_rubric

# Read once when the Worker starts; the rubric is the same for every conversation.
RUBRIC = load_rubric()

app = FastAPI()

# All endpoints live under /api, e.g. GET /api and POST /api/echo.
api = APIRouter(prefix="/api")


class EchoRequest(BaseModel):
    message: str


@api.get("")
def home():
    return {"message": "Hello from Cloudflare Workers"}


@api.post("/echo")
def echo(request: EchoRequest):
    return {"you_said": request.message}


@api.get("/conversations/{conversation_id}/report")
def report(conversation_id: str):
    # Step 2: only made-up scores. Step 3 reads the real scores from D1.
    if conversation_id == "demo":
        return build_report(RUBRIC, demo.worked_example_scores(RUBRIC))
    if conversation_id == "demo-four":
        rubric = demo.four_dimension_placeholder(RUBRIC)
        return build_report(rubric, demo.worked_example_scores(rubric))
    raise HTTPException(status_code=404, detail="Conversation not found")


app.include_router(api)

# Cloudflare calls this entrypoint for every request and passes it to FastAPI.
Default = asgi.entrypoint(app)
