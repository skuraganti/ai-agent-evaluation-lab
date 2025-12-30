"""Bounded reproducibility study of the unchanged deterministic baseline.

Offline only: pure baseline() and evaluate() calls. No run journal, bridge IPC,
model reservation, network access or inference. See evals/BASELINE_REPEAT_PROTOCOL.md.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import catalog as catalog_module  # noqa: E402
from app.evaluator import baseline, digest, evaluate  # noqa: E402

PROTOCOL_PATH = Path("evals/BASELINE_REPEAT_PROTOCOL.json")
OUTPUT_DIR = Path("data/verification/baseline-repeat")
OUTCOME_KEYS = (
    "task_success",
    "schema_valid",
    "safe",
    "refusal",
    "failure_class",
    "blocked_calls",
    "error",
)
COMPARED = ("proposal_sha256", "outcome_sha256", "receipt_sha256", "semantic_hash")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_protocol(root):
    protocol = json.loads((root / PROTOCOL_PATH).read_text())
    expected = {
        "protocol_id": "tracelab-baseline-repeat-v1",
        "policy": "baseline",
        "fault": "none",
        "seed": 17,
        "repetitions": {"default": 3, "min": 1, "max": 5},
        "model_calls": 0,
        "frozen_before_first_measurement": True,
    }
    for key, value in expected.items():
        if protocol.get(key) != value:
            raise ValueError(f"Protocol field changed: {key}")
    fault = protocol["fault_experiment"]
    if (
        fault["faults"] != ["none", "timeout_once", "always_timeout"]
        or fault["seed"] != 17
        or fault["max_attempts_per_call"] != 2
    ):
        raise ValueError("Protocol fault experiment changed")
    per_rep = protocol["evaluations_per_repetition"]
    if per_rep["total"] != per_rep["originals"] + per_rep["probe"] + per_rep["fault"] or (
        per_rep["originals"] != len(protocol["original_tasks"]) or per_rep["probe"] != 1
    ):
        raise ValueError("Protocol evaluation counts are inconsistent")
    if protocol["max_total_evaluations"] != per_rep["total"] * protocol["repetitions"]["max"]:
        raise ValueError("Protocol evaluation cap changed")
    return protocol


def verify_pins(protocol, root):
    """Reject source/code drift before any evaluation happens."""
    for relative, expected in protocol["code_sha256"].items():
        if sha256_file(root / relative) != expected:
            raise ValueError(f"Pinned code changed: {relative}")
    source = protocol["source"]
    if sha256_file(root / "SOURCE_MANIFEST.json") != source["manifest_sha256"]:
        raise ValueError("Pinned source manifest changed")
    if (
        catalog_module.TASK_SHA != source["tasks_sha256"]
        or catalog_module.ORACLE_SHA != source["reference_sha256"]
        or catalog_module.PROTOCOL != protocol["evaluation_protocol"]
    ):
        raise ValueError("Catalog pins differ from frozen protocol")


def verify_tasks(protocol, catalog):
    actual = [
        {"id": t["id"], "split": t["split"], "category": t["category"]}
        for t in catalog.tasks.values()
        if not t["synthetic_probe"]
    ]
    if actual != protocol["original_tasks"]:
        raise ValueError("Original task IDs/splits differ from frozen protocol")
    probe = catalog.tasks.get(protocol["probe"]["id"])
    if not probe or not probe["synthetic_probe"]:
        raise ValueError("Synthetic probe missing or not labelled")
    if protocol["fault_experiment"]["task_id"] not in catalog.tasks:
        raise ValueError("Fault experiment task missing")


def output_path(root, value):
    directory = (root / OUTPUT_DIR).resolve()
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if path.parent != directory or path.suffix != ".json":
        raise ValueError(f"Output must be a .json file directly under {OUTPUT_DIR}")
    if path.exists() or path.is_symlink():
        raise ValueError("Output exists; studies are never overwritten")
    return path


def write_exclusive(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".partial-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Fails if the output appeared meanwhile.
    finally:
        temporary.unlink(missing_ok=True)


def one_evaluation(catalog, task_id, fault, seed, baseline_fn, evaluate_fn):
    """A genuinely separate baseline call followed by a separate local judge call."""
    row = {"task_id": task_id, "fault": fault, "seed": seed}
    try:
        task = catalog.task(task_id)
        proposal = baseline_fn(task)
        result = evaluate_fn(task, proposal, catalog.answer(task_id), fault=fault, seed=seed)
    except Exception as exc:  # Preserve failed evaluations instead of dropping them.
        failure = {"exception": type(exc).__name__, "message": str(exc)[:300]}
        row.update(failure, outcome_sha256=digest(failure))
        row.update({key: None for key in COMPARED if key != "outcome_sha256"})
        return row
    outcome = {key: result[key] for key in OUTCOME_KEYS}
    attempts = {}
    for event in result["events"]:
        if event["type"] == "tool_attempt":
            call = event["payload"]["call"]
            attempts[call] = attempts.get(call, 0) + 1
    row.update(
        outcome,
        proposal=proposal,
        proposal_sha256=digest(proposal),
        outcome_sha256=digest(outcome),
        receipt_sha256=digest(result["calls"]),
        semantic_hash=result["semantic_hash"],
        trace_hash=result["trace_hash"],
        receipts=len(result["calls"]),
        max_attempts_per_call=max(attempts.values(), default=0),
        synthetic_faults=sum(e["type"] == "synthetic_fault" for e in result["events"]),
        tool_ms=result["tool_ms"],
    )
    return row


def stability(rows, key):
    groups = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    stable, unstable = [], []
    for identity, items in groups.items():
        same = all(len({row[h] for row in items}) == 1 for h in COMPARED)
        same = same and not any("exception" in row for row in items)
        (stable if same else unstable).append(identity)
    return stable, unstable


def summarize(rows):
    ok = [row for row in rows if "exception" not in row]
    classes = {}
    for row in ok:
        classes[row["failure_class"]] = classes.get(row["failure_class"], 0) + 1
    timings = [row["tool_ms"] for row in ok]
    return {
        "n": len(rows),
        "task_success": sum(row["task_success"] for row in ok),
        "schema_valid": sum(row["schema_valid"] for row in ok),
        "safe": sum(row["safe"] for row in ok),
        "refusal": sum(row["refusal"] for row in ok),
        "blocked_call_entries": sum(len(row["blocked_calls"]) for row in ok),
        "exceptions": len(rows) - len(ok),
        "failure_class": dict(sorted(classes.items())),
        "tool_ms_local": {
            "min": min(timings),
            "median": statistics.median(timings),
            "max": max(timings),
        }
        if timings
        else None,
    }


def run_study(protocol, catalog, repetitions, *, baseline_fn=baseline, evaluate_fn=evaluate):
    seed, fault_cfg = protocol["seed"], protocol["fault_experiment"]
    splits = {t["id"]: t["split"] for t in protocol["original_tasks"]}
    originals, probes, faults = [], [], []
    for repetition in range(1, repetitions + 1):
        for task_id, split in splits.items():
            row = one_evaluation(catalog, task_id, "none", seed, baseline_fn, evaluate_fn)
            originals.append({"repetition": repetition, "split": split, **row})
        row = one_evaluation(
            catalog, protocol["probe"]["id"], "none", seed, baseline_fn, evaluate_fn
        )
        probes.append({"repetition": repetition, **row})
        for fault in fault_cfg["faults"]:
            row = one_evaluation(
                catalog, fault_cfg["task_id"], fault, fault_cfg["seed"], baseline_fn, evaluate_fn
            )
            faults.append({"repetition": repetition, **row})

    stable, unstable = stability(originals, "task_id")
    probe_stable, probe_unstable = stability(probes, "task_id")
    fault_stable, fault_unstable = stability(faults, "fault")
    attempt_violations = [
        row
        for row in faults
        if row.get("max_attempts_per_call", 0) > fault_cfg["max_attempts_per_call"]
    ]
    per_repetition = []
    for repetition in range(1, repetitions + 1):
        rows = [row for row in originals if row["repetition"] == repetition]
        per_repetition.append(
            {
                "repetition": repetition,
                "all": summarize(rows),
                "development": summarize([r for r in rows if r["split"] == "development"]),
                "held_out": summarize([r for r in rows if r["split"] == "held_out"]),
            }
        )
    passed = not (unstable or probe_unstable or fault_unstable or attempt_violations)
    return {
        "status": "stable" if passed else "unstable",
        "design": {
            "cases": len(splits),
            "development": sum(s == "development" for s in splits.values()),
            "held_out": sum(s == "held_out" for s in splits.values()),
            "repetitions": repetitions,
            "original_evaluations": len(originals),
            "note": f"{len(splits)} fixed, previously observed cases x {repetitions} "
            "deterministic repetitions; not independent tasks. n stays "
            f"{len(splits)}.",
        },
        "originals": {
            "stable_cases": stable,
            "unstable_cases": unstable,
            "per_repetition": per_repetition,
            "rows": originals,
        },
        "probe": {
            "separate_from_originals": True,
            "stable": probe_stable,
            "unstable": probe_unstable,
            "per_repetition": [summarize([row]) for row in probes],
            "rows": probes,
        },
        "fault_experiment": {
            "task_id": fault_cfg["task_id"],
            "synchronous_simulation": True,
            "stable_faults": fault_stable,
            "unstable_faults": fault_unstable,
            "attempt_bound_violations": len(attempt_violations),
            "rows": faults,
        },
        "evaluations_performed": len(originals) + len(probes) + len(faults),
        "model_calls": 0,
        "cost_usd": None,
        "tool_ms_basis": "local simulated tool validation/judging; not API latency",
        "not_compared": protocol["not_compared"],
    }


def runtime(root):
    packages = sorted(
        f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
    )
    lock = root / "uv.lock"
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "uv_lock_sha256": sha256_file(lock) if lock.is_file() else None,
        "packages_sha256": hashlib.sha256("\n".join(packages).encode()).hexdigest(),
        "jsonschema": importlib.metadata.version("jsonschema"),
    }


def main(
    argv=None,
    *,
    root=ROOT,
    catalog_factory=catalog_module.Catalog,
    baseline_fn=baseline,
    evaluate_fn=evaluate,
):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help=f"New .json file under {OUTPUT_DIR}")
    parser.add_argument("--repetitions", type=int, default=None)
    args = parser.parse_args(argv)
    root = Path(root)
    protocol = load_protocol(root)
    bounds = protocol["repetitions"]
    repetitions = bounds["default"] if args.repetitions is None else args.repetitions
    if not bounds["min"] <= repetitions <= bounds["max"]:
        raise ValueError(f"Repetitions must be {bounds['min']}..{bounds['max']}")
    planned = repetitions * protocol["evaluations_per_repetition"]["total"]
    if planned > protocol["max_total_evaluations"]:
        raise ValueError("Planned evaluations exceed the frozen cap")
    verify_pins(protocol, root)
    path = output_path(root, args.output)
    catalog = catalog_factory()
    verify_tasks(protocol, catalog)

    started = datetime.now(UTC).isoformat()
    study = run_study(
        protocol, catalog, repetitions, baseline_fn=baseline_fn, evaluate_fn=evaluate_fn
    )
    if study["evaluations_performed"] != planned:
        raise RuntimeError("Evaluation count differs from plan")
    report = {
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha256_file(root / PROTOCOL_PATH),
        "runner_sha256": sha256_file(Path(__file__)),
        "source": protocol["source"],
        "code_sha256": protocol["code_sha256"],
        "seed": protocol["seed"],
        "policy": protocol["policy"],
        "planned_evaluations": planned,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "runtime": runtime(root),
        **study,
    }
    write_exclusive(path, report)
    originals = study["originals"]
    print(
        json.dumps(
            {
                "status": study["status"],
                "output": str(path),
                "cases": study["design"]["cases"],
                "repetitions": repetitions,
                "unstable_cases": originals["unstable_cases"],
                "per_repetition_success": [
                    r["all"]["task_success"] for r in originals["per_repetition"]
                ],
                "probe_unstable": study["probe"]["unstable"],
                "fault_unstable": study["fault_experiment"]["unstable_faults"],
            }
        )
    )
    return 0 if study["status"] == "stable" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as exc:
        raise SystemExit(f"Refused before or during study: {exc}") from None
