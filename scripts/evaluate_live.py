"""Run the frozen eight-call study once. Existing output prevents implicit reruns."""

import argparse
import hashlib
import json
import platform
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data/verification/live-results.json"
CASES = ["live_simple_4-3-0", "live_simple_85-46-0", "live_parallel_1-0-1", "probe-sort-override"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Explicitly continue only missing case/policy identities",
    )
    resume = parser.parse_args().resume
    if OUTPUT.exists() and not resume:
        raise SystemExit("Study artifact exists; inspect it instead of silently issuing more calls")
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "protocol": "tracelab-exact-v1",
        "model": "cookllm",
        "hardware": platform.platform(),
        "python_host": platform.python_version(),
        "actual_cost_usd": None,
        "replicates_per_case_policy": 1,
        "rows": [],
        "code_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in ["app/evaluator.py", "bridge.py", "app/runner.py"]
        },
    }
    if resume:
        report = json.loads(OUTPUT.read_text())
    report.setdefault("submissions", {})
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    with httpx.Client(base_url="http://127.0.0.1:8285", timeout=10, trust_env=False) as client:
        # Serial cases keep pending calls below3 and expose every returned failure.
        for case in CASES:
            for policy in ["direct", "guarded"]:
                existing = [
                    r for r in report["rows"] if (r["task_id"], r["policy"]) == (case, policy)
                ]
                if existing:
                    if len(existing) != 1:
                        raise ValueError("Duplicate case/policy in frozen report")
                    actual = client.get("/api/runs/" + existing[0]["id"]).json()
                    if actual["state"] not in {"complete", "failed"}:
                        raise ValueError("Existing attempt is still pending; inspect instead")
                    continue
                key = case + "/" + policy
                token = report["submissions"].setdefault(key, str(uuid.uuid4()))
                OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
                value = {
                    "task_ids": [case],
                    "policy": policy,
                    "fault": "none",
                    "seed": 17,
                    "token": token,
                }
                response = client.post("/api/runs", json=value)
                response.raise_for_status()
                batch = response.json()["batch_id"]
                end = time.monotonic() + 145
                row = None
                while time.monotonic() < end:
                    row = next(
                        (
                            r
                            for r in client.get("/api/state").json()["runs"]
                            if r["batch_id"] == batch
                        ),
                        None,
                    )
                    if row and row["state"] in {"complete", "failed"}:
                        break
                    time.sleep(0.5)
                if not row or row["state"] not in {"complete", "failed"}:
                    raise TimeoutError(
                        "Study waits expired; inspect existing run; do not retry model"
                    )
                report["rows"].append(row)
                OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
                result = row.get("result") or {}
                print(
                    json.dumps(
                        {
                            "case": case,
                            "policy": policy,
                            "state": row["state"],
                            "pass": result.get("task_success"),
                            "safe": result.get("safe"),
                            "failure": result.get("failure_class"),
                            "model_ms": row.get("model_ms"),
                        }
                    ),
                    flush=True,
                )
    report["finished_at"] = datetime.now(UTC).isoformat()
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
