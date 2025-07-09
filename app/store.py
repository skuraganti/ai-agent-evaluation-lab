"""Small durable run journal. Published model requests are never recreated after a crash."""

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from app.catalog import FAULTS, POLICIES, PROTOCOL

MAX_MODEL_CALLS = 12


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL,
                request TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, task_id TEXT NOT NULL,
                policy TEXT NOT NULL, fault TEXT NOT NULL, seed INTEGER NOT NULL,
                created REAL NOT NULL, started REAL, finished REAL, state TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT,
                usage TEXT, model_ms REAL, parent_id TEXT, task_digest TEXT NOT NULL,
                protocol TEXT NOT NULL);
        """)
        self.db.commit()
        self.engine_sha = hashlib.sha256(
            (Path(__file__).parent / "evaluator.py").read_bytes()
        ).hexdigest()
        if "engine_sha" not in {r[1] for r in self.db.execute("PRAGMA table_info(runs)")}:
            self.db.execute(
                "ALTER TABLE runs ADD COLUMN engine_sha TEXT NOT NULL "
                "DEFAULT 'unrecorded-preacceptance'"
            )
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def all(self):
        with self.lock:
            return [
                self.decode(r)
                for r in self.db.execute(
                    "SELECT * FROM runs ORDER BY created DESC,id DESC LIMIT 500"
                ).fetchall()
            ]

    @staticmethod
    def decode(row):
        result = dict(row)
        for field in ["result", "usage"]:
            if result[field]:
                result[field] = json.loads(result[field])
        return result

    def get(self, identifier):
        with self.lock:
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("Unknown run")
            return self.decode(row)

    def submit(self, catalog, value):
        from app.evaluator import digest

        if not isinstance(value, dict) or set(value) != {
            "task_ids",
            "policy",
            "fault",
            "seed",
            "token",
        }:
            raise ValueError("Expected task_ids, policy, fault, seed and token")
        ids, policy, fault, seed, token = (
            value[k] for k in ["task_ids", "policy", "fault", "seed", "token"]
        )
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 26
            or any(not isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("Choose1..26 distinct pinned tasks")
        if policy not in POLICIES or fault not in FAULTS:
            raise ValueError("Unknown policy or synthetic fault")
        if type(seed) is not int or not 0 <= seed <= 1_000_000:
            raise ValueError("Seed must be integer0..1000000")
        try:
            if str(uuid.UUID(token)) != token:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("A canonical UUID idempotency token is required") from None
        tasks = [catalog.task(i) for i in ids]
        request = json.dumps(value, sort_keys=True)
        with self.lock:
            prior = self.db.execute("SELECT * FROM batches WHERE token=?", (token,)).fetchone()
            if prior:
                if prior["request"] != request:
                    raise ValueError("Idempotency token was used for a different request")
                return {"batch_id": prior["id"], "deduplicated": True}
            counts = self.counts()
            if counts["runs"] + len(ids) > 500:
                raise ValueError("Local journal cap500 reached; preserve/export before a new study")
            if policy != "baseline" and (
                len(ids) > 3
                or counts["model_attempts_reserved"] + len(ids) > MAX_MODEL_CALLS
                or counts["model_pending"] + len(ids) > 3
            ):
                raise ValueError("Model bound:3 pending and12 lifetime reserved attempts")
            batch, now = str(uuid.uuid4()), time.time()
            with self.db:
                self.db.execute("INSERT INTO batches VALUES(?,?,?,?)", (batch, token, request, now))
                for task in tasks:
                    self.db.execute(
                        """INSERT INTO runs
                        (id,batch_id,task_id,policy,fault,seed,created,state,task_digest,protocol,engine_sha)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            str(uuid.uuid4()),
                            batch,
                            task["id"],
                            policy,
                            fault,
                            seed,
                            now,
                            "queued",
                            digest(task),
                            PROTOCOL,
                            self.engine_sha,
                        ),
                    )
            return {"batch_id": batch, "deduplicated": False}

    def counts(self):
        with self.lock:
            return dict(
                self.db.execute("""SELECT count(*) runs,
                coalesce(sum(policy!='baseline' AND parent_id IS NULL),0) model_attempts_reserved,
                coalesce(sum(policy!='baseline' AND parent_id IS NULL
                    AND state IN ('queued','awaiting')),0) model_pending FROM runs""").fetchone()
            )

    def mark_published(self, identifier):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE runs SET state='awaiting',published=1,started=? WHERE id=?",
                (time.time(), identifier),
            )

    def finish(self, identifier, result=None, error=None, usage=None, model_ms=None):
        with self.lock, self.db:
            self.db.execute(
                """UPDATE runs SET state=?,finished=?,result=?,error=?,usage=?,model_ms=?
                WHERE id=? AND state IN ('queued','awaiting')""",
                (
                    "failed" if error is not None else "complete",
                    time.time(),
                    json.dumps(result, allow_nan=False) if result is not None else None,
                    (error or "Evaluation failed")[:300] if error is not None else None,
                    json.dumps(usage, allow_nan=False) if usage is not None else None,
                    model_ms,
                    identifier,
                ),
            )

    def save_replay(self, parent, result):
        with self.lock, self.db:
            if self.counts()["runs"] >= 500:
                raise ValueError("Local journal cap reached")
            identifier, now = str(uuid.uuid4()), time.time()
            self.db.execute(
                """INSERT INTO runs
                (id,batch_id,task_id,policy,fault,seed,created,started,finished,state,
                 result,parent_id,task_digest,protocol,engine_sha)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifier,
                    parent["batch_id"],
                    parent["task_id"],
                    parent["policy"],
                    parent["fault"],
                    parent["seed"],
                    now,
                    now,
                    now,
                    "complete",
                    json.dumps(result, allow_nan=False),
                    parent["id"],
                    parent["task_digest"],
                    PROTOCOL,
                    self.engine_sha,
                ),
            )
            return identifier
