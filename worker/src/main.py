"""Minimal FastAPI app running on Cloudflare Workers (Python Workers).

Run locally (from the worker/ folder):
    uv run pywrangler dev
    curl http://localhost:8787/

Deploy:
    uv run pywrangler deploy
"""

from fastapi import FastAPI
from pydantic import BaseModel
from workers import asgi

app = FastAPI()


class EchoRequest(BaseModel):
    message: str


@app.get("/")
def home():
    return {"message": "Hello from Cloudflare Workers"}


@app.post("/echo")
def echo(request: EchoRequest):
    return {"you_said": request.message}


# Cloudflare calls this entrypoint for every request and passes it to FastAPI.
Default = asgi.entrypoint(app)
