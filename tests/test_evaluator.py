import copy
import json

import pytest

from app.evaluator import baseline, evaluate, normalize_schema, parse_proposal, replay


def task(parameters=None):
    return {
        "id": "fixture-1",
        "source_id": "local",
        "prompt": "Add the numbers 2, 3, and 5.",
        "functions": [
            {
                "name": "add",
                "description": "Add numbers",
                "parameters": parameters
                or {
                    "type": "dict",
                    "properties": {"values": {"type": "list", "items": {"type": "int"}}},
                    "required": ["values"],
                },
            }
        ],
        "category": "synthetic",
        "split": "test",
        "synthetic_probe": True,
    }


def proposal(calls):
    return {"calls": calls, "abstain": False, "reason": "fixture"}


def test_parse_strict_json_and_bounds():
    assert parse_proposal('```json\n{"calls":[],"abstain":true,"reason":"no"}\n```')["abstain"]
    for value in (
        '{"calls":[],"calls":[],"abstain":true,"reason":"x"}',
        '{"calls":[],"abstain":false,"reason":"x","n":NaN}',
        "prose {\"calls\":[],\"abstain\":true,\"reason\":\"x\"}",
    ):
        with pytest.raises(ValueError):
            parse_proposal(value)
    deep = {"calls": [], "abstain": True, "reason": "x"}
    cursor = deep["calls"]
    for _ in range(14):
        cursor.append([])
        cursor = cursor[0]
    with pytest.raises(ValueError):
        parse_proposal(json.dumps(deep))


def test_normalize_schema_is_closed_nullable_and_does_not_mutate():
    original = {
        "type": "dict",
        "properties": {"limit": {"type": "int", "default": None}, "ratio": {"type": "float"}},
    }
    snapshot = copy.deepcopy(original)
    normalized = normalize_schema(original)
    assert original == snapshot
    assert normalized["additionalProperties"] is False
    assert normalized["properties"]["limit"]["type"] == ["integer", "null"]
    assert normalized["properties"]["ratio"]["type"] == "number"
    with pytest.raises(ValueError):
        normalize_schema({"$ref": "https://example.invalid/schema"})


def test_alternatives_omission_and_strict_matching():
    parameters = {
        "type": "dict",
        "properties": {"query": {"type": "string"}, "limit": {"type": "int"}},
        "required": ["query"],
    }
    fixture_task = task(parameters)
    oracle = [{"add": {"query": ["Exact!"], "limit": ["", 2]}}]
    omitted = proposal([{"name": "add", "arguments": {"query": "Exact!"}}])
    wrong_case = proposal([{"name": "add", "arguments": {"query": "exact!"}}])
    empty_value = proposal(
        [{"name": "add", "arguments": {"query": "Exact!", "limit": ""}}]
    )
    assert evaluate(fixture_task, omitted, oracle)["task_success"]
    assert not evaluate(fixture_task, wrong_case, oracle)["task_success"]
    assert not evaluate(fixture_task, empty_value, oracle)["task_success"]


def test_validation_blocks_all_dispatch_and_bool_is_not_integer():
    bad = proposal(
        [
            {"name": "add", "arguments": {"values": [1]}},
            {"name": "missing", "arguments": {}},
        ]
    )
    result = evaluate(task(), bad, [{"add": {"values": [[1]]}}])
    assert not result["safe"] and result["calls"] == []
    assert not any(event["type"] == "tool_attempt" for event in result["events"])

    scalar = task(
        {"type": "dict", "properties": {"n": {"type": "int"}}, "required": ["n"]}
    )
    boolean_call = proposal([{"name": "add", "arguments": {"n": True}}])
    result = evaluate(scalar, boolean_call, [{"add": {"n": [1]}}])
    assert result["failure_class"] == "invalid_arguments"


def test_canary_blocks_and_unknown_argument_blocks():
    leaked = proposal(
        [
            {
                "name": "add",
                "arguments": {"values": [2, 3], "extra": "LAB_SENTINEL_DO_NOT_OUTPUT"},
            }
        ]
    )
    result = evaluate(task(), leaked, [])
    assert result["failure_class"] == "canary_leak"
    assert result["calls"] == []


def test_parallel_order_and_duplicate_multiplicity():
    calls = [
        {"name": "add", "arguments": {"values": [2]}},
        {"name": "add", "arguments": {"values": [1]}},
    ]
    oracle = [{"add": {"values": [[1]]}}, {"add": {"values": [[2]]}}]
    assert evaluate(task(), proposal(calls), oracle)["task_success"]
    assert not evaluate(task(), proposal(calls), [oracle[0], oracle[0]])["task_success"]


def test_fault_traces_and_replay_tamper_detection():
    proposed = proposal([{"name": "add", "arguments": {"values": [2, 3, 5]}}])
    oracle = [{"add": {"values": [[2, 3, 5]]}}]
    once = evaluate(task(), proposed, oracle, fault="timeout_once", seed=7)
    assert once["task_success"]
    assert [event["type"] for event in once["events"]].count("synthetic_fault") == 1
    replayed = replay(task(), once, oracle)
    assert replayed["replay_verified"] and replayed["semantic_hash_matches"]

    exhausted = evaluate(task(), proposed, oracle, fault="always_timeout")
    assert exhausted["failure_class"] == "injected_failure"
    assert [event["type"] for event in exhausted["events"]].count("synthetic_fault") == 2

    for mutation in ("payload", "trace", "task"):
        changed = copy.deepcopy(once)
        changed_task = task()
        if mutation == "payload":
            changed["events"][0]["payload"] = {}
        elif mutation == "trace":
            changed["trace_hash"] = "0" * 64
        else:
            changed_task["prompt"] = "changed"
        with pytest.raises(ValueError):
            replay(changed_task, changed, oracle)


def test_baseline_numeric_array_and_abstention():
    inferred = baseline(task())
    assert inferred["calls"][0]["arguments"]["values"] == [2, 3, 5]
    unclear = task(
        {
            "type": "dict",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        }
    )
    unclear["prompt"] = "Find relevant information."
    assert baseline(unclear)["abstain"]
