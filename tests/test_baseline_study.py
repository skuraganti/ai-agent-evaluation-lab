import ast
import copy
import json
import shutil
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.evaluator as evaluator_module
from scripts import evaluate_baseline as study

ROOT = Path(__file__).resolve().parent.parent
OUT = "data/verification/baseline-repeat/run-001.json"
PARAMS = {
    "type": "dict",
    "properties": {"values": {"type": "list", "items": {"type": "int"}}},
    "required": ["values"],
}


def fn(name):
    return {"name": name, "description": name, "parameters": copy.deepcopy(PARAMS)}


def fixture_task(identifier, split, functions, probe=False):
    return {
        "id": identifier,
        "source_id": identifier,
        "prompt": "Add the numbers 2, 3, and 5.",
        "functions": functions,
        "category": "simple",
        "split": split,
        "synthetic_probe": probe,
    }


class FakeCatalog:
    """dev-ok succeeds, dev-abstain abstains (two unnamed tools), held-wrong has wrong calls."""

    def __init__(self):
        rows = [
            fixture_task("dev-ok", "development", [fn("add")]),
            fixture_task("dev-abstain", "development", [fn("add"), fn("sum")]),
            fixture_task("held-wrong", "held_out", [fn("add")]),
            fixture_task("probe", "synthetic_probe", [fn("add")], probe=True),
        ]
        self.tasks = {t["id"]: t for t in rows}
        right = [{"add": {"values": [[2, 3, 5]]}}]
        self.oracles = {
            "dev-ok": right,
            "dev-abstain": right,
            "held-wrong": [{"add": {"values": [[1]]}}],
            "probe": right,
        }

    def task(self, identifier):
        return copy.deepcopy(self.tasks[identifier])

    def answer(self, identifier):
        return copy.deepcopy(self.oracles[identifier])


def fake_protocol(root=ROOT):
    protocol = json.loads((ROOT / study.PROTOCOL_PATH).read_text())
    protocol["original_tasks"] = [
        {"id": t["id"], "split": t["split"], "category": t["category"]}
        for t in FakeCatalog().tasks.values()
        if not t["synthetic_probe"]
    ]
    protocol["probe"]["id"] = "probe"
    protocol["fault_experiment"]["task_id"] = "dev-ok"
    protocol["evaluations_per_repetition"] = {"originals": 3, "probe": 1, "fault": 3, "total": 7}
    protocol["max_total_evaluations"] = 35
    protocol["code_sha256"] = {
        p: study.sha256_file(root / p) for p in ["app/catalog.py", "app/evaluator.py"]
    }
    return protocol


def make_root(tmp_path, protocol=None):
    for relative in ["app/catalog.py", "app/evaluator.py", "SOURCE_MANIFEST.json", "uv.lock"]:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    target = tmp_path / study.PROTOCOL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if protocol is None:
        shutil.copyfile(ROOT / study.PROTOCOL_PATH, target)
    else:
        target.write_text(json.dumps(protocol))
    return tmp_path


class Spy:
    def __init__(self, inner):
        self.inner, self.calls, self.returned = inner, [], []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))  # Keep objects alive so ids stay unique.
        self.returned.append(self.inner(*args, **kwargs))
        return self.returned[-1]


def test_protocol_frozen_pins_match_checkout():
    protocol = study.load_protocol(ROOT)
    study.verify_pins(protocol, ROOT)
    assert len(protocol["original_tasks"]) == 25
    splits = [t["split"] for t in protocol["original_tasks"]]
    assert splits.count("development") == 5 and splits.count("held_out") == 20
    assert protocol["probe"]["id"] not in {t["id"] for t in protocol["original_tasks"]}
    assert protocol["max_total_evaluations"] == 145


def test_hand_checked_metrics_and_separate_invocations():
    catalog = FakeCatalog()
    base, judge = Spy(evaluator_module.baseline), Spy(evaluator_module.evaluate)
    report = study.run_study(fake_protocol(), catalog, 2, baseline_fn=base, evaluate_fn=judge)

    # 2 repetitions x (3 originals + 1 probe + 3 faults): each a separate baseline call.
    assert len(base.calls) == len(judge.calls) == 14 == report["evaluations_performed"]
    tasks = [args[0] for args, _ in base.calls]
    proposals = [args[1] for args, _ in judge.calls]
    assert len({id(t) for t in tasks}) == 14 and len({id(p) for p in proposals}) == 14
    assert all(p is r for p, r in zip(proposals, base.returned, strict=True))
    for (args, _), task in zip(judge.calls, tasks, strict=True):
        assert args[0] is task  # evaluate saw the same fresh copy the baseline saw
    assert all(kw["seed"] == 17 for _, kw in judge.calls)

    first = report["originals"]["per_repetition"][0]
    assert first["all"]["n"] == 3 and first["all"]["task_success"] == 1
    assert first["all"]["refusal"] == 1 and first["all"]["schema_valid"] == 3
    assert first["all"]["failure_class"] == {"none": 1, "refusal": 1, "wrong_calls": 1}
    assert first["development"]["n"] == 2 and first["development"]["task_success"] == 1
    assert first["held_out"]["failure_class"] == {"wrong_calls": 1}
    assert report["originals"]["per_repetition"][1]["all"] == {
        **first["all"],
        "tool_ms_local": report["originals"]["per_repetition"][1]["all"]["tool_ms_local"],
    }
    assert report["design"]["cases"] == 3 and report["design"]["original_evaluations"] == 6
    assert report["status"] == "stable" and report["model_calls"] == 0
    assert report["cost_usd"] is None

    faults = {r["fault"]: r for r in report["fault_experiment"]["rows"] if r["repetition"] == 1}
    assert (faults["none"]["max_attempts_per_call"], faults["none"]["receipts"]) == (1, 1)
    assert (
        faults["timeout_once"]["synthetic_faults"] == 1 and faults["timeout_once"]["task_success"]
    )
    assert faults["always_timeout"]["max_attempts_per_call"] == 2
    assert faults["always_timeout"]["synthetic_faults"] == 2
    assert faults["always_timeout"]["failure_class"] == "injected_failure"
    assert faults["always_timeout"]["receipts"] == 0
    assert report["probe"]["separate_from_originals"] and len(report["probe"]["rows"]) == 2


def test_semantic_stability_despite_different_timestamps(monkeypatch):
    clock = {"t": datetime(2026, 1, 1, tzinfo=UTC)}

    class Clock:
        @staticmethod
        def now(tz=None):
            clock["t"] += timedelta(seconds=1)
            return clock["t"]

    monkeypatch.setattr(evaluator_module, "datetime", Clock)
    report = study.run_study(fake_protocol(), FakeCatalog(), 3)
    rows = [r for r in report["originals"]["rows"] if r["task_id"] == "dev-ok"]
    assert len({r["trace_hash"] for r in rows}) == 3  # chains differ by timestamp
    assert len({r["semantic_hash"] for r in rows}) == 1
    assert report["status"] == "stable" and not report["originals"]["unstable_cases"]


def test_mismatch_and_exceptions_fail_honestly():
    counter = {"n": 0}

    def drifting(task):
        counter["n"] += 1
        proposal = evaluator_module.baseline(task)
        proposal["reason"] += f" {counter['n']}"
        return proposal

    report = study.run_study(fake_protocol(), FakeCatalog(), 2, baseline_fn=drifting)
    assert report["status"] == "unstable"
    assert set(report["originals"]["unstable_cases"]) == {"dev-ok", "dev-abstain", "held-wrong"}

    def broken(task, proposal, oracle, **kwargs):
        if task["id"] == "held-wrong":
            raise ValueError("simulated judge failure")
        return evaluator_module.evaluate(task, proposal, oracle, **kwargs)

    report = study.run_study(fake_protocol(), FakeCatalog(), 2, evaluate_fn=broken)
    rows = [r for r in report["originals"]["rows"] if r["task_id"] == "held-wrong"]
    assert len(rows) == 2 and all(r["exception"] == "ValueError" for r in rows)
    assert report["originals"]["unstable_cases"] == ["held-wrong"]
    assert report["originals"]["per_repetition"][0]["all"]["exceptions"] == 1


def test_main_writes_exclusive_bounded_report(tmp_path, monkeypatch):
    root = make_root(tmp_path, fake_protocol())
    base = Spy(evaluator_module.baseline)

    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    run = dict(root=root, catalog_factory=FakeCatalog, baseline_fn=base)
    assert study.main(["--output", OUT], **run) == 0
    output = root / OUT
    saved = output.read_bytes()
    report = json.loads(saved)
    assert report["planned_evaluations"] == 21 == len(base.calls)
    assert report["runtime"]["uv_lock_sha256"] == study.sha256_file(ROOT / "uv.lock")
    assert report["started_at_utc"] <= report["finished_at_utc"]
    assert report["protocol_sha256"] == study.sha256_file(root / study.PROTOCOL_PATH)

    for argv in (
        ["--output", OUT],  # exists: never overwritten
        ["--output", "data/verification/run-002.json"],  # outside study directory
        ["--output", "data/verification/baseline-repeat/run-002.txt"],
        ["--output", "data/verification/baseline-repeat/run-002.json", "--repetitions", "0"],
        ["--output", "data/verification/baseline-repeat/run-002.json", "--repetitions", "6"],
    ):
        with pytest.raises(ValueError):
            study.main(argv, **run)
    assert len(base.calls) == 21 and output.read_bytes() == saved
    assert sorted(p.name for p in output.parent.iterdir()) == ["run-001.json"]

    with pytest.raises(FileExistsError):
        study.write_exclusive(output, {"other": True})
    assert output.read_bytes() == saved


def test_drift_rejected_before_any_evaluation(tmp_path):
    def refuse_catalog():
        raise AssertionError("catalog loaded despite drift")

    base = Spy(evaluator_module.baseline)
    args = ["--output", OUT]

    root = make_root(tmp_path / "code")
    with (root / "app/evaluator.py").open("a") as stream:
        stream.write("\n# drift\n")
    with pytest.raises(ValueError, match="Pinned code changed"):
        study.main(args, root=root, catalog_factory=refuse_catalog, baseline_fn=base)

    root = make_root(tmp_path / "source")
    (root / "SOURCE_MANIFEST.json").write_text("{}")
    with pytest.raises(ValueError, match="source manifest"):
        study.main(args, root=root, catalog_factory=refuse_catalog, baseline_fn=base)

    changed = json.loads((ROOT / study.PROTOCOL_PATH).read_text())
    changed["seed"] = 18
    root = make_root(tmp_path / "config", changed)
    with pytest.raises(ValueError, match="seed"):
        study.main(args, root=root, catalog_factory=refuse_catalog, baseline_fn=base)

    changed = json.loads((ROOT / study.PROTOCOL_PATH).read_text())
    changed["max_total_evaluations"] = 1000
    root = make_root(tmp_path / "cap", changed)
    with pytest.raises(ValueError, match="cap"):
        study.main(args, root=root, catalog_factory=refuse_catalog, baseline_fn=base)

    # Real protocol against a catalog whose task IDs differ.
    root = make_root(tmp_path / "tasks")
    with pytest.raises(ValueError, match="task IDs"):
        study.main(args, root=root, catalog_factory=FakeCatalog, baseline_fn=base)
    assert base.calls == [] and not (tmp_path / "tasks" / OUT).exists()


def test_runner_imports_no_bridge_store_runner_or_network():
    tree = ast.parse((ROOT / "scripts/evaluate_baseline.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert not imported & {"httpx", "socket", "urllib", "app.runner", "app.store", "bridge"}
    assert {m for m in imported if m.startswith("app")} == {"app", "app.evaluator"}


@pytest.mark.skipif(
    not (ROOT / "data/prepared/tasks.json").is_file(), reason="pinned data not acquired"
)
def test_real_pinned_catalog_single_repetition(tmp_path):
    root = make_root(tmp_path)
    public = {"id", "source_id", "prompt", "functions", "category", "split", "synthetic_probe"}
    seen = []

    def checked(task):
        assert set(task) <= public | {"untrusted_observation"}  # no reference fields
        seen.append(task["id"])
        return evaluator_module.baseline(task)

    run = dict(root=root, baseline_fn=checked)
    status = study.main(["--output", OUT, "--repetitions", "1"], **run)
    report = json.loads((root / OUT).read_text())
    assert status == 0 and report["status"] == "stable"
    assert report["design"] == {**report["design"], "cases": 25, "development": 5, "held_out": 20}
    assert len(seen) == 29 and report["model_calls"] == 0
