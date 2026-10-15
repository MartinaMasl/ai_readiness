"""Minimal FastAPI app running on Cloudflare Workers (Python Workers).

Run locally (from the worker/ folder):
    uv run pywrangler dev
    curl http://localhost:8787/api

Deploy:
    uv run pywrangler deploy
"""

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from workers import asgi

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


app.include_router(api)

# Cloudflare calls this entrypoint for every request and passes it to FastAPI.
Default = asgi.entrypoint(app)
