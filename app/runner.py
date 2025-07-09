"""One serial local runner. Inference uses only pinned task identities, never answers."""

import math
import time
from pathlib import Path

from app.catalog import PROTOCOL
from app.evaluator import baseline, digest, evaluate
from app.io import atomic_json, read_json


def heartbeat(directory):
    try:
        value = read_json(Path(directory) / "heartbeat.json", 4096)
        at, count = value.get("at"), value.get("calls")
        if (
            type(at) not in {int, float}
            or not 0 <= at <= 10_000_000_000
            or not math.isfinite(at)
            or not 0 <= time.time() - at <= 5
            or value.get("model") != "cookllm"
            or value.get("status") not in {"ready", "busy"}
            or type(count) is not int
            or not 0 <= count <= 12
        ):
            raise ValueError()
        return {"available": True, "calls": count, "status": value["status"]}
    except (OSError, ValueError, TypeError):
        return {"available": False, "calls": None, "status": "unavailable"}


class Runner:
    def __init__(self, store, catalog, bridge):
        self.store, self.catalog, self.bridge = store, catalog, Path(bridge)
        self.bridge.mkdir(parents=True, exist_ok=True, mode=0o700)
        for row in self.store.all():
            if row["published"] and row["state"] in {"complete", "failed"}:
                self.cleanup(row)  # Finish-commit before cleanup may straddle a process crash.

    def paths(self, row):
        return [
            self.bridge / (row["id"] + suffix) for suffix in [".request.json", ".response.json"]
        ]

    def cleanup(self, row):
        for path in self.paths(row):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # Refuse to remove a substituted directory or any of its contents.

    def consume(self, row):
        request, response = self.paths(row)
        if response.exists() or response.is_symlink():
            observed_latency = None
            observed_usage = None
            try:
                value = read_json(response, 100_000)
                if (
                    value.get("id") != row["id"]
                    or value.get("task_digest") != row["task_digest"]
                    or value.get("model") != "cookllm"
                ):
                    raise ValueError("Bridge response identity mismatch")
                latency = value.get("latency_ms")
                usage = value.get("usage", {})
                if (
                    type(value.get("attempted")) is not bool
                    or type(latency) not in {int, float}
                    or not 0 <= latency <= 110_000
                    or not math.isfinite(latency)
                    or not isinstance(usage, dict)
                    or any(type(n) is not int or not 0 <= n <= 10_000_000 for n in usage.values())
                ):
                    raise ValueError("Invalid observed model metadata")
                if value["attempted"]:
                    observed_latency = latency
                    observed_usage = usage
                if value.get("state") != "succeeded":
                    raise ValueError(str(value.get("error", "Bridge attempt failed"))[:240])
                if not value["attempted"]:
                    raise ValueError("Successful response did not record an actual attempt")
                result = evaluate(
                    self.catalog.task(row["task_id"]),
                    value.get("output"),
                    self.catalog.answer(row["task_id"]),
                    fault=row["fault"],
                    seed=row["seed"],
                )
                self.store.finish(row["id"], result=result, usage=usage, model_ms=latency)
            except (ValueError, OSError, TypeError) as exc:
                self.store.finish(
                    row["id"],
                    error=str(exc) or "Invalid bounded response",
                    model_ms=observed_latency,
                    usage=observed_usage,
                )
            self.cleanup(row)
            return True
        if (
            not request.exists()
            or request.is_symlink()
            or time.time() - (row["started"] or row["created"]) > 125
        ):
            self.store.finish(row["id"], error="Interrupted, missing or expired request; no retry")
            self.cleanup(row)
            return True
        try:
            value = read_json(request, 4096)
            if (
                value.get("id") != row["id"]
                or value.get("task_id") != row["task_id"]
                or value.get("task_digest") != row["task_digest"]
                or value.get("policy") != row["policy"]
                or value.get("protocol") != PROTOCOL
            ):
                raise ValueError("Pending request identity changed")
        except (ValueError, OSError, TypeError) as exc:
            self.store.finish(row["id"], error=str(exc) or "Invalid pending request")
            self.cleanup(row)
            return True
        return False  # An intact pending identity survives restart without republication.

    def step(self):
        rows = list(reversed(self.store.all()))
        for row in rows:
            if row["state"] == "awaiting":
                self.consume(row)
        for row in rows:
            if row["state"] != "queued":
                continue
            try:
                task = self.catalog.task(row["task_id"])
                if row["protocol"] != PROTOCOL or row["task_digest"] != digest(task):
                    raise ValueError("Task/protocol changed; refusing stale run")
                if row["policy"] == "baseline":
                    result = evaluate(
                        task,
                        baseline(task),
                        self.catalog.answer(row["task_id"]),
                        fault=row["fault"],
                        seed=row["seed"],
                    )
                    self.store.finish(row["id"], result=result)
                else:
                    if not heartbeat(self.bridge)["available"]:
                        raise ValueError("Approved host bridge unavailable; no fallback or retry")
                    request, _ = self.paths(row)
                    # Reserve the published state first. A crash before file write fails closed.
                    self.store.mark_published(row["id"])
                    atomic_json(
                        request,
                        {
                            "id": row["id"],
                            "task_id": row["task_id"],
                            "task_digest": row["task_digest"],
                            "policy": row["policy"],
                            "protocol": PROTOCOL,
                            "expires_at": time.time() + 120,
                        },
                    )
            except Exception as exc:
                self.store.finish(row["id"], error=str(exc)[:300])

    def serve(self, stop):
        while not stop.wait(0.25):
            self.step()
