import copy

import pytest

from app.evaluator import baseline, evaluate, parse_proposal, replay


def task():
    return {
        "prompt": "Add 2, 3 and 5.",
        "functions": [
            {
                "name": "sum",
                "parameters": {
                    "type": "dict",
                    "properties": {"values": {"type": "list", "items": {"type": "int"}}},
                    "required": ["values"],
                },
            }
        ],
    }


def test_sentence_punctuation_is_not_a_decimal():
    assert baseline(task())["calls"][0]["arguments"]["values"] == [2, 3, 5]


@pytest.mark.parametrize(
    "raw",
    [
        "[" * 2000 + "]" * 2000,
        '{"calls":[{"name":"sum","arguments":{"values":['
        + "9" * 400
        + ']}}],"abstain":false,"reason":"x"}',
    ],
)
def test_pathological_output_returns_failed_evaluation(raw):
    with pytest.raises(ValueError):
        parse_proposal(raw)
    result = evaluate(task(), raw, [{"sum": {"values": [[2, 3, 5]]}}])
    assert result["failure_class"] == "invalid_output" and not result["task_success"]


def test_reference_shape_rejected_and_semantic_tamper():
    value = baseline(task())
    with pytest.raises(ValueError, match="oracle"):
        evaluate(task(), value, [{"sum": {"values": 5}}])
    result = evaluate(task(), value, [{"sum": {"values": [[2, 3, 5]]}}])
    altered = copy.deepcopy(result)
    altered["task_success"] = False
    with pytest.raises(ValueError, match="semantic"):
        replay(task(), altered, [{"sum": {"values": [[2, 3, 5]]}}])


def test_dense_twelve_call_mismatch_is_bounded():
    proposed = {
        "calls": [{"name": "sum", "arguments": {"values": [1]}}] * 12,
        "abstain": False,
        "reason": "dense matching fixture",
    }
    oracle = [{"sum": {"values": [[1]]}}] * 11 + [{"sum": {"values": [[2]]}}]
    assert not evaluate(task(), proposed, oracle)["task_success"]
    oracle[-1] = {"sum": {"values": [[1]]}}
    assert evaluate(task(), proposed, oracle)["task_success"]


def test_optional_null_default_applies_to_enum_too():
    t = {
        "functions": [
            {
                "name": "pick",
                "parameters": {
                    "type": "dict",
                    "properties": {"choice": {"type": "int", "enum": [1, 2], "default": None}},
                },
            }
        ]
    }
    p = {
        "calls": [{"name": "pick", "arguments": {"choice": None}}],
        "abstain": False,
        "reason": "nullable enum fixture",
    }
    assert evaluate(t, p, [{"pick": {"choice": [None]}}])["task_success"]


def test_fixed_heartbeat_frame_retains_strict_freshness(tmp_path):
    import time

    from app.io import atomic_json, read_json
    from app.runner import heartbeat

    path = tmp_path / "heartbeat.json"
    for count, status in [(7, "ready"), (10, "busy")]:
        frame = {"at": time.time(), "model": "cookllm", "status": status, "calls": count}
        atomic_json(path, frame, pad_to=256)
        assert path.stat().st_size == 256
        assert read_json(path, 4096) == frame
        assert heartbeat(tmp_path)["available"]
    before = path.read_bytes()
    with pytest.raises(ValueError):
        atomic_json(path, frame, pad_to=8)
    assert path.read_bytes() == before
    frame["status"] = "stopped"
    atomic_json(path, frame, pad_to=256)
    assert path.stat().st_size == 256
    assert not heartbeat(tmp_path)["available"]
    frame["status"] = "ready"
    frame["model"] = "unapproved-route"
    atomic_json(path, frame, pad_to=256)
    assert not heartbeat(tmp_path)["available"]
    frame["model"] = "cookllm"
    frame["at"] = time.time() - 60
    atomic_json(path, frame, pad_to=256)
    assert not heartbeat(tmp_path)["available"]
    path.write_bytes(b'{"at": 123' + b" " * 246)
    assert not heartbeat(tmp_path)["available"]
