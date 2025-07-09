import json
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import bridge as host
from app.catalog import Catalog
from app.evaluator import digest
from app.io import atomic_json, read_json, read_regular
from app.main import create_app
from app.runner import Runner, heartbeat
from app.store import Store

TASK = "live_simple_86-47-0"


def request(policy="baseline", ids=None, **extra):
    return {
        "task_ids": ids or [TASK],
        "policy": policy,
        "fault": "none",
        "seed": 17,
        "token": str(uuid.uuid4()),
        **extra,
    }


@pytest.fixture
def system(tmp_path):
    catalog, store = Catalog(), Store(tmp_path / "state.sqlite")
    runner = Runner(store, catalog, tmp_path / "bridge")
    yield catalog, store, runner
    store.close()


def ready(runner):
    atomic_json(
        runner.bridge / "heartbeat.json",
        {"model": "cookllm", "at": time.time(), "status": "ready", "calls": 0},
    )


def test_source_split_and_originals():
    c = Catalog()
    assert len(c.tasks) == 26
    assert len([t for t in c.tasks.values() if t["split"] == "held_out"]) == 20
    dev = {
        f["name"] for t in c.tasks.values() if t["split"] == "development" for f in t["functions"]
    }
    held = {f["name"] for t in c.tasks.values() if t["split"] == "held_out" for f in t["functions"]}
    assert not dev & held
    assert "ground_truth" not in json.dumps(c.summary())


def test_idempotent_batch_and_baseline(system):
    catalog, store, runner = system
    value = request()
    first = store.submit(catalog, value)
    assert store.submit(catalog, value)["deduplicated"]
    with pytest.raises(ValueError):
        store.submit(catalog, {**value, "seed": 18})
    runner.step()
    row = store.all()[0]
    assert row["batch_id"] == first["batch_id"]
    assert row["state"] == "complete" and row["result"]["task_success"]
    assert store.counts()["model_attempts_reserved"] == 0


def test_pending_same_identity_and_response_first_recovery(system):
    catalog, store, runner = system
    ready(runner)
    store.submit(catalog, request("direct"))
    runner.step()
    row = store.all()[0]
    original = runner.paths(row)[0].read_bytes()
    Runner(store, catalog, runner.bridge).step()
    assert store.get(row["id"])["state"] == "awaiting"
    assert runner.paths(row)[0].read_bytes() == original
    pending = read_json(runner.paths(row)[0], 4096)
    payload = host.approved_context(pending)
    assert set(payload) == {"task", "policy"}
    assert "ground_truth" not in json.dumps(payload)
    # Simulate a completed host response across an app restart (not live model evidence).
    atomic_json(
        runner.paths(row)[1],
        {
            "id": row["id"],
            "task_digest": row["task_digest"],
            "model": "cookllm",
            "state": "succeeded",
            "attempted": True,
            "output": {
                "calls": [
                    {"name": "sort_array", "arguments": {"array": [1, 54, 3, 1, 76, 2343, 21]}}
                ],
                "abstain": False,
                "reason": "synthetic integration fixture",
            },
            "latency_ms": 1,
            "usage": {"input_tokens": 2, "output_tokens": 3},
        },
    )
    runner.paths(row)[0].unlink()
    Runner(store, catalog, runner.bridge).step()
    assert store.get(row["id"])["result"]["task_success"]
    assert not any(path.exists() for path in runner.paths(row))
    assert store.counts()["model_attempts_reserved"] == 1


@pytest.mark.parametrize("kind", ["missing", "symlink", "wrong_id", "expired", "fifo"])
def test_broken_pending_never_republished(system, tmp_path, kind):
    catalog, store, runner = system
    ready(runner)
    store.submit(catalog, request("guarded"))
    runner.step()
    row = store.all()[0]
    path = runner.paths(row)[0]
    if kind == "expired":
        with store.lock, store.db:
            store.db.execute("UPDATE runs SET started=? WHERE id=?", (time.time() - 200, row["id"]))
    else:
        path.unlink()
        if kind == "symlink":
            target = tmp_path / "target"
            target.write_text("{}")
            path.symlink_to(target)
        elif kind == "wrong_id":
            atomic_json(path, {"id": str(uuid.uuid4())})
        elif kind == "fifo":
            os.mkfifo(path)
    runner.step()
    assert store.get(row["id"])["state"] == "failed"
    assert not path.exists()
    assert store.counts()["model_attempts_reserved"] == 1


def test_bounded_attempts_and_replay(system):
    catalog, store, runner = system
    ready(runner)
    ids = list(catalog.tasks)[:3]
    store.submit(catalog, request("direct", ids))
    with pytest.raises(ValueError, match="bound"):
        store.submit(catalog, request("guarded"))
    runner.step()
    for row in store.all():
        runner.paths(row)[0].unlink()
    runner.step()
    for _ in range(3):
        store.submit(catalog, request("direct", ids))
        runner.step()
        for row in store.all():
            if row["state"] == "awaiting":
                runner.paths(row)[0].unlink()
        runner.step()
    with pytest.raises(ValueError, match="bound"):
        store.submit(catalog, request("direct"))
    assert store.counts()["model_attempts_reserved"] == 12


def test_heartbeat_metadata_validation(system):
    _, _, runner = system
    ready(runner)
    assert heartbeat(runner.bridge)["available"]
    for value in [float("nan"), time.time() + 20, time.time() - 20, "now"]:
        (runner.bridge / "heartbeat.json").write_text(
            json.dumps({"model": "cookllm", "at": value, "status": "ready", "calls": 0})
        )
        assert not heartbeat(runner.bridge)["available"]


@pytest.mark.parametrize(
    "raw", ["[]", '{"at":' + "9" * 400 + "}", '{"latency_ms":NaN}', "[" * 2000 + "]" * 2000]
)
def test_malformed_ipc_fails_without_killing_step(system, raw):
    catalog, store, runner = system
    ready(runner)
    store.submit(catalog, request("direct"))
    runner.step()
    row = store.all()[0]
    runner.paths(row)[1].write_text(raw)
    runner.step()
    done = store.get(row["id"])
    assert done["state"] == "failed" and done["error"]
    assert done["result"] is None


def test_directory_ipc_cleanup_and_empty_error(system):
    catalog, store, runner = system
    ready(runner)
    store.submit(catalog, request("direct"))
    runner.step()
    row = store.all()[0]
    runner.paths(row)[1].mkdir()
    runner.step()
    assert store.get(row["id"])["state"] == "failed"
    assert runner.paths(row)[1].is_dir()
    store.submit(catalog, request())
    row = next(r for r in store.all() if r["state"] == "queued")
    store.finish(row["id"], error="")
    assert store.get(row["id"])["state"] == "failed"
    assert store.get(row["id"])["error"]


def test_finish_commit_cleanup_crash_reconciles(system):
    catalog, store, runner = system
    ready(runner)
    store.submit(catalog, request("direct"))
    runner.step()
    row = store.all()[0]
    atomic_json(runner.paths(row)[1], {"crash_fixture": True})
    store.finish(row["id"], error="simulated finish-before-cleanup crash")
    Runner(store, catalog, runner.bridge)
    assert not any(p.exists() for p in runner.paths(row))


@pytest.mark.parametrize("attempted,expected", [(True, 876.5), (False, None)])
def test_observed_failed_attempt_latency_retained(system, attempted, expected):
    catalog, store, runner = system
    ready(runner)
    store.submit(catalog, request("direct"))
    runner.step()
    row = store.all()[0]
    atomic_json(
        runner.paths(row)[1],
        {
            "id": row["id"],
            "task_digest": row["task_digest"],
            "model": "cookllm",
            "state": "failed",
            "attempted": attempted,
            "error": "synthetic unavailable",
            "latency_ms": 876.5,
            "usage": {"input_tokens": 23, "output_tokens": 7},
        },
    )
    runner.step()
    done = store.get(row["id"])
    assert done["state"] == "failed"
    assert done["model_ms"] == expected
    assert done["usage"] == ({"input_tokens": 23, "output_tokens": 7} if attempted else None)


def test_bridge_pin_policy_expiry_and_raw_io(tmp_path):
    task = Catalog().task(TASK)
    value = {
        "id": str(uuid.uuid4()),
        "task_id": TASK,
        "task_digest": digest(task),
        "policy": "direct",
        "protocol": "tracelab-exact-v1",
        "expires_at": time.time() + 30,
    }
    assert host.approved_context(value)["task"] == task
    for update in [
        {"task_digest": "a" * 64},
        {"policy": "new-provider"},
        {"expires_at": time.time() - 1},
        {"question": "arbitrary private text"},
    ]:
        with pytest.raises(ValueError):
            host.approved_context({**value, **update})
    f = tmp_path / "fifo"
    os.mkfifo(f)
    with pytest.raises(ValueError):
        read_regular(f, 100)
    f.unlink()
    f.write_text('{"x":1,"x":2}')
    with pytest.raises(ValueError):
        read_json(f, 100)


def test_http_workflow_and_boundaries(tmp_path):
    app = create_app(tmp_path / "app.sqlite", tmp_path / "bridge", background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/state").json()["cost"]["actual_usd"] is None
        value = request()
        assert client.post("/api/runs", json=value).status_code == 202
        app.state.runner.step()
        row = client.get("/api/state").json()["runs"][0]
        assert row["result"]["task_success"]
        result = client.post(f"/api/runs/{row['id']}/replay").json()
        assert result["replay"]["replay_verified"]
        assert client.get("/api/state").json()["counts"]["model_attempts_reserved"] == 0
        assert "reference_calls" in client.get(f"/api/runs/{row['id']}").json()
        assert client.post("/api/runs", json=request("direct")).status_code == 503
        assert client.post("/api/runs", json=[]).status_code == 400
        assert (
            client.post(
                "/api/runs", json=value, headers={"Origin": "https://invalid.example"}
            ).status_code
            == 403
        )
        assert client.get("/", headers={"Host": "evil.invalid"}).status_code == 400
        assert client.post("/api/runs", content=b"x" * 70_000).status_code == 413
        assert (
            client.post(
                "/api/runs",
                content="[" * 2000 + "]" * 2000,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 400
        )
        assert "default-src 'self'" in client.get("/").headers["content-security-policy"]
        assert client.get("/api/export").headers["content-disposition"].endswith('.json"')
