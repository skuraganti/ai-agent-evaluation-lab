"""TraceLab: frozen public benchmark experiments, simulated tools and offline replay."""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.catalog import PROTOCOL, Catalog
from app.evaluator import replay
from app.runner import Runner, heartbeat
from app.security import protect
from app.store import MAX_MODEL_CALLS, Store

ROOT = Path(__file__).resolve().parent


def create_app(db=None, bridge=None, *, background=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.catalog = Catalog()
        app.state.store = Store(db or os.getenv("TRACELAB_DB", "data/state/tracelab.sqlite"))
        app.state.runner = Runner(
            app.state.store,
            app.state.catalog,
            bridge or os.getenv("TRACELAB_BRIDGE", "data/bridge"),
        )
        stop = threading.Event()
        thread = threading.Thread(target=app.state.runner.serve, args=(stop,), daemon=True)
        if background:
            thread.start()
        app.state.thread = thread
        yield
        stop.set()
        if thread.is_alive():
            thread.join(timeout=3)
        app.state.store.close()

    app = FastAPI(title="TraceLab", lifespan=lifespan, docs_url=None, redoc_url=None)
    protect(app)

    @app.exception_handler(ValueError)
    @app.exception_handler(TypeError)
    @app.exception_handler(RecursionError)
    async def bad_input(request, exc):
        return JSONResponse({"error": str(exc)[:300]}, status_code=400)

    @app.get("/health")
    def health():
        app.state.store.counts()
        if background and not app.state.thread.is_alive():
            return JSONResponse({"status": "error", "runner_alive": False}, status_code=503)
        return {
            "status": "ok",
            "protocol": PROTOCOL,
            "engine_sha": app.state.store.engine_sha,
            "runner_alive": app.state.thread.is_alive() if background else None,
        }

    @app.get("/api/state")
    def state():
        return {
            "tasks": app.state.catalog.summary(),
            "runs": app.state.store.all(),
            "counts": app.state.store.counts(),
            "model_cap": MAX_MODEL_CALLS,
            "bridge": heartbeat(app.state.runner.bridge),
            "protocol": PROTOCOL,
            "cost": {"actual_usd": None, "basis": "Alias billing unavailable; never $0 assumed"},
        }

    @app.get("/api/tasks/{identifier}")
    def task(identifier: str):
        return app.state.catalog.task(identifier)

    @app.post("/api/runs", status_code=202)
    async def submit(request: Request):
        if background and not app.state.thread.is_alive():
            return JSONResponse({"error": "Local runner stopped; inspect logs"}, status_code=503)
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Request must be a JSON object")
        if (
            value.get("policy") != "baseline"
            and not heartbeat(app.state.runner.bridge)["available"]
        ):
            return JSONResponse(
                {"error": "Approved cookllm bridge unavailable; no fallback"}, status_code=503
            )
        return app.state.store.submit(app.state.catalog, value)

    @app.get("/api/runs/{identifier}")
    def run(identifier: str):
        row = app.state.store.get(identifier)
        # Only after evaluation; this endpoint is never consumed by the model bridge.
        if row["result"]:
            row["reference_calls"] = app.state.catalog.answer(row["task_id"])
        return row

    @app.post("/api/runs/{identifier}/replay", status_code=201)
    def replay_run(identifier: str):
        row = app.state.store.get(identifier)
        if row["state"] != "complete" or not row["result"]:
            raise ValueError("Only a completed evaluation has a replayable proposal")
        result = replay(
            app.state.catalog.task(row["task_id"]),
            row["result"],
            app.state.catalog.answer(row["task_id"]),
        )
        new_id = app.state.store.save_replay(row, result)
        return {"id": new_id, "replay": result}

    @app.get("/api/export")
    def export():
        return JSONResponse(
            {
                "protocol": PROTOCOL,
                "runs": app.state.store.all(),
                "cost_usd": None,
                "simulation": True,
            },
            headers={"Content-Disposition": 'attachment; filename="tracelab-runs.json"'},
        )

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static/index.html")

    return app


app = create_app()
