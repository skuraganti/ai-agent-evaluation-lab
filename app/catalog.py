"""Immutable, original-publisher tasks. Reference answers never leave the grader."""

import copy
import hashlib
import json
import os
from pathlib import Path

from app.io import read_regular

ROOT = Path(__file__).resolve().parent.parent
TASK_SHA = "24f2fcf16fa75f6a9296e02d1cf394f2a0029b7ad62b487e23152a410fe40cfb"
ORACLE_SHA = "4061ffac0ee6946cefd3d19fa6c31a8be776b9c027ecd5ec07286be0c7ee1e37"
PROTOCOL = "tracelab-exact-v1"
POLICIES = {"baseline", "direct", "guarded"}
FAULTS = {"none", "timeout_once", "always_timeout"}


def pinned(path, expected):
    content = read_regular(path, 200_000)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Pinned benchmark bytes changed; run acquisition and inspect provenance")
    return json.loads(content)


def public_tasks():
    return pinned(Path(os.getenv("TRACELAB_TASKS", ROOT / "data/prepared/tasks.json")), TASK_SHA)


class Catalog:
    def __init__(self):
        self.tasks = {t["id"]: t for t in public_tasks()}
        self.oracles = pinned(
            Path(os.getenv("TRACELAB_ORACLE", ROOT / "data/oracle/answers.json")), ORACLE_SHA
        )
        if set(self.tasks) != set(self.oracles):
            raise ValueError("Task and oracle IDs do not match")

    def task(self, identifier):
        if identifier not in self.tasks:
            raise ValueError("Unknown pinned benchmark task")
        return copy.deepcopy(self.tasks[identifier])

    def answer(self, identifier):
        return copy.deepcopy(self.oracles[identifier])

    def summary(self):
        return [
            {
                "id": t["id"],
                "source_id": t["source_id"],
                "split": t["split"],
                "category": t["category"],
                "synthetic_probe": t["synthetic_probe"],
                "prompt": t["prompt"],
                "tools": [f["name"] for f in t["functions"]],
            }
            for t in self.tasks.values()
        ]
