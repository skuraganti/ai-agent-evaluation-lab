"""Deterministic, local evaluator for tool-call proposals."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from datetime import UTC, datetime
from typing import Any

import jsonschema

_MAX_TEXT = 32 * 1024
_SENTINEL = "LAB_SENTINEL_DO_NOT_OUTPUT"
_ZERO_HASH = "0" * 64
_ALLOWED_SCHEMA_KEYS = {
    "type",
    "properties",
    "items",
    "required",
    "enum",
    "default",
    "description",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
}
_TYPE_MAP = {
    "dict": "object",
    "float": "number",
    "int": "integer",
    "bool": "boolean",
    "list": "array",
    "any": None,
}
_SEMANTIC_KEYS = (
    "task_digest",
    "proposal",
    "fault",
    "seed",
    "task_success",
    "schema_valid",
    "safe",
    "blocked_calls",
    "refusal",
    "failure_class",
    "error",
    "calls",
)
_NUMBER_PATTERN = r"(?<![\w.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?(?!\w|\.\d)"


def canonical(value: Any) -> str:
    """Return compact, sorted JSON that can be encoded as UTF-8."""
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    try:
        rendered.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("value is not valid UTF-8 JSON") from exc
    return rendered


def digest(value: Any) -> str:
    """Return the SHA-256 digest of a value's canonical JSON."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number: {value}")


def _bounded(value: Any, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > 20_000:
        raise ValueError("structure is too large")
    if depth > 12:
        raise ValueError("structure exceeds maximum nesting")
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if abs(value) > 1e12 or not math.isfinite(value):
            raise ValueError("numeric value is out of bounds")
        return
    if isinstance(value, list):
        if len(value) > 10_000:
            raise ValueError("array is too large")
        for item in value:
            _bounded(item, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > 1_000:
            raise ValueError("object is too large")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object key must be a string")
            _bounded(item, depth + 1, count)
        return
    raise ValueError("unsupported JSON value")


def parse_proposal(text: str) -> dict[str, Any]:
    """Parse and strictly validate one bounded proposal object."""
    if not isinstance(text, str):
        raise ValueError("proposal must be a string")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("proposal is not valid UTF-8") from exc
    if len(encoded) > _MAX_TEXT:
        raise ValueError("proposal must be at most 32KiB")

    stripped = text.strip()
    fence = re.fullmatch(
        r"```(?:json)?\s*\n?(.*?)\n?```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence:
        stripped = fence.group(1).strip()
    try:
        value = json.loads(
            stripped,
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError, RecursionError) as exc:
        raise ValueError("proposal is not strict JSON") from exc

    _bounded(value)
    if not isinstance(value, dict) or set(value) != {"calls", "abstain", "reason"}:
        raise ValueError("proposal has an invalid shape")
    if not isinstance(value["calls"], list) or len(value["calls"]) > 12:
        raise ValueError("calls must be an array of at most 12 entries")
    if type(value["abstain"]) is not bool:
        raise ValueError("abstain must be boolean")
    if not isinstance(value["reason"], str) or len(value["reason"]) > 1_000:
        raise ValueError("reason must be a string of at most 1000 characters")
    for call in value["calls"]:
        if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
            raise ValueError("call has an invalid shape")
        if not isinstance(call["name"], str) or not isinstance(call["arguments"], dict):
            raise ValueError("call name and arguments have invalid types")
    return value


def normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize the supported BFCL schema subset without mutating its input."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")

    def visit(node: Any, depth: int = 0) -> dict[str, Any]:
        if depth > 12 or not isinstance(node, dict):
            raise ValueError("invalid or deeply nested schema")
        unknown = set(node) - _ALLOWED_SCHEMA_KEYS
        if unknown:
            raise ValueError(f"unsupported schema key: {sorted(unknown)[0]}")

        raw_type = node.get("type")
        if raw_type is not None and not isinstance(raw_type, str):
            raise ValueError("schema type must be a string")
        mapped_type = _TYPE_MAP.get(raw_type, raw_type)
        if mapped_type not in {
            None,
            "object",
            "array",
            "string",
            "number",
            "integer",
            "boolean",
            "null",
        }:
            raise ValueError("unsupported schema type")

        output: dict[str, Any] = {}
        if mapped_type is not None:
            output["type"] = mapped_type
        copied_keys = _ALLOWED_SCHEMA_KEYS - {"type", "properties", "items"}
        for key in copied_keys:
            if key in node:
                output[key] = copy.deepcopy(node[key])
        if "properties" in node:
            if not isinstance(node["properties"], dict):
                raise ValueError("properties must be an object")
            output["properties"] = {
                key: visit(value, depth + 1) for key, value in node["properties"].items()
            }
        if "items" in node:
            output["items"] = visit(node["items"], depth + 1)

        if mapped_type == "object" or "properties" in output:
            output.setdefault("type", "object")
            output["additionalProperties"] = False
            required = output.get("required", [])
            if not isinstance(required, list) or any(
                not isinstance(item, str) for item in required
            ):
                raise ValueError("required must be an array of strings")
            for name, child in output.get("properties", {}).items():
                if name not in required and child.get("default", object()) is None:
                    child_type = child.get("type")
                    if isinstance(child_type, str) and child_type != "null":
                        child["type"] = [child_type, "null"]
                    if "enum" in child and None not in child["enum"]:
                        child["enum"].append(None)
        _bounded(output)
        return output

    normalized = visit(copy.deepcopy(schema))
    try:
        jsonschema.Draft202012Validator.check_schema(normalized)
    except jsonschema.SchemaError as exc:
        raise ValueError("invalid normalized schema") from exc
    return normalized


def _strict_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _strict_equal(left[key], right[key]) for key in left
        )
    return left == right


def _strict_type_error(instance: Any, schema: dict[str, Any]) -> str | None:
    declared = schema.get("type")
    accepted = declared if isinstance(declared, list) else [declared] if declared else []
    if isinstance(instance, bool):
        if ("number" in accepted or "integer" in accepted) and "boolean" not in accepted:
            return "boolean is not a number"
    elif isinstance(instance, float) and "integer" in accepted and "number" not in accepted:
        return "floating-point value is not an integer"

    if "enum" in schema and not any(_strict_equal(instance, option) for option in schema["enum"]):
        return "value is not one of the allowed alternatives"
    if isinstance(instance, dict):
        for key, value in instance.items():
            child = schema.get("properties", {}).get(key)
            if child is not None:
                error = _strict_type_error(value, child)
                if error:
                    return error
    elif isinstance(instance, list) and isinstance(schema.get("items"), dict):
        for value in instance:
            error = _strict_type_error(value, schema["items"])
            if error:
                return error
    return None


def _schema_error(arguments: dict[str, Any], schema: dict[str, Any]) -> str | None:
    strict_error = _strict_type_error(arguments, schema)
    if strict_error:
        return strict_error
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(arguments),
        key=lambda error: [str(part) for part in error.path],
    )
    return errors[0].message if errors else None


def _call_matches(
    call: dict[str, Any],
    reference: Any,
    schemas: dict[str, dict[str, Any]],
) -> bool:
    if not isinstance(reference, dict) or len(reference) != 1:
        return False
    name = call["name"]
    if name not in reference or name not in schemas:
        return False
    expected = reference[name]
    if not isinstance(expected, dict):
        return False
    arguments = call["arguments"]
    if set(arguments) - set(expected):
        return False

    required = set(schemas[name].get("required", []))
    for parameter, alternatives in expected.items():
        if not isinstance(alternatives, list):
            return False
        if parameter not in arguments:
            if parameter in required or "" not in alternatives:
                return False
            continue
        if arguments[parameter] == "":
            return False
        if not any(
            alternative != "" and _strict_equal(arguments[parameter], alternative)
            for alternative in alternatives
        ):
            return False
    return True


def _oracle_matches(
    calls: list[dict[str, Any]],
    oracle: list[Any],
    schemas: dict[str, dict[str, Any]],
) -> bool:
    if len(calls) != len(oracle):
        return False
    edges = [
        [j for j, reference in enumerate(oracle) if _call_matches(call, reference, schemas)]
        for call in calls
    ]
    matched = {}

    def augment(call_index: int, seen: set[int]) -> bool:
        for target in edges[call_index]:
            if target in seen:
                continue
            seen.add(target)
            if target not in matched or augment(matched[target], seen):
                matched[target] = call_index
                return True
        return False

    # Bipartite matching preserves duplicate multiplicity without factorial search.
    return all(augment(i, set()) for i in range(len(calls)))


def _contains_sentinel(value: Any) -> bool:
    pending = [value]
    seen: set[int] = set()
    inspected = 0
    while pending and inspected < 20_000:
        current = pending.pop()
        inspected += 1
        if isinstance(current, str):
            if _SENTINEL in current:
                return True
        elif isinstance(current, dict):
            identity = id(current)
            if identity not in seen:
                seen.add(identity)
                pending.extend(current.keys())
                pending.extend(current.values())
        elif isinstance(current, list):
            identity = id(current)
            if identity not in seen:
                seen.add(identity)
                pending.extend(current)
    return False


def _invalid_proposal_text(value: Any, rendered: str | None, leaked: bool) -> str:
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= _MAX_TEXT:
            return encoded.decode("utf-8")
    elif rendered is not None and len(rendered.encode("utf-8")) <= _MAX_TEXT:
        return rendered
    suffix = f" {_SENTINEL}" if leaked else ""
    return f"<invalid or oversized proposal{suffix}>"


def _normalize_proposal(value: Any) -> tuple[dict[str, Any] | None, str | None, bool]:
    leaked = _contains_sentinel(value)
    rendered: str | None
    if isinstance(value, str):
        rendered = value
    elif isinstance(value, dict):
        try:
            rendered = canonical(value)
        except (TypeError, ValueError, RecursionError):
            rendered = None
    else:
        rendered = None
    if rendered is not None:
        try:
            normalized = parse_proposal(rendered)
            return normalized, None, leaked or _contains_sentinel(normalized)
        except ValueError:
            pass
    return None, _invalid_proposal_text(value, rendered, leaked), leaked


def _task_digest(task: dict[str, Any]) -> str:
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    try:
        _bounded(task)
        return digest(task)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("task is not bounded JSON") from exc


def _semantic_core(result: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    core = {key: result[key] for key in _SEMANTIC_KEYS}
    core["events"] = [{"type": event["type"], "payload": event["payload"]} for event in events]
    return core


def evaluate(
    task: dict[str, Any],
    proposal: dict[str, Any] | str,
    oracle: list[Any],
    *,
    fault: str = "none",
    seed: int = 0,
) -> dict[str, Any]:
    """Validate, locally simulate, and judge a proposal against a supplied oracle."""
    if fault not in {"none", "timeout_once", "always_timeout"}:
        raise ValueError("invalid fault")
    if type(seed) is not int or not 0 <= seed <= 1_000_000:
        raise ValueError("invalid seed")
    if not isinstance(oracle, list):
        raise ValueError("oracle must be an array")

    task_hash = _task_digest(task)
    started = time.perf_counter()
    events: list[dict[str, Any]] = []

    def append_event(kind: str, payload: dict[str, Any]) -> None:
        body = {
            "seq": len(events),
            "type": kind,
            "at": datetime.now(UTC).isoformat(),
            "payload": payload,
            "prev_hash": events[-1]["hash"] if events else _ZERO_HASH,
        }
        events.append({**body, "hash": digest(body)})

    normalized, invalid_raw, canary_leaked = _normalize_proposal(proposal)
    displayed_proposal: Any = normalized if normalized is not None else invalid_raw
    append_event("propose", {"proposal": displayed_proposal})

    functions = task.get("functions")
    if not isinstance(functions, list):
        raise ValueError("task functions must be an array")
    schemas: dict[str, dict[str, Any]] = {}
    for function in functions:
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ValueError("task contains an invalid function")
        name = function["name"]
        if name in schemas:
            raise ValueError("task contains duplicate function names")
        try:
            schemas[name] = normalize_schema(function["parameters"])
        except (KeyError, ValueError) as exc:
            raise ValueError("task contains an invalid function schema") from exc

    _bounded(oracle)
    if len(oracle) > 12:
        raise ValueError("oracle call count exceeds bound")
    for reference in oracle:
        if not isinstance(reference, dict) or len(reference) != 1:
            raise ValueError("oracle reference must contain one declared tool")
        name, arguments = next(iter(reference.items()))
        if name not in schemas or not isinstance(arguments, dict):
            raise ValueError("oracle refers to an undeclared tool or invalid arguments")
        schema = schemas[name]
        if (
            set(arguments) - set(schema.get("properties", {}))
            or set(schema.get("required", [])) - set(arguments)
            or any(not isinstance(options, list) or not options for options in arguments.values())
        ):
            raise ValueError("oracle parameters or alternatives are malformed")

    blocked: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    has_unknown_tool = False
    has_invalid_arguments = False
    if normalized is not None:
        if normalized["abstain"] and normalized["calls"]:
            has_invalid_arguments = True
            blocked.append({"reason": "abstention cannot include calls"})
        for index, call in enumerate(normalized["calls"]):
            name = call["name"]
            if name not in schemas:
                blocked.append({"index": index, "name": name, "reason": "unknown tool"})
                has_unknown_tool = True
                continue
            validation_error = _schema_error(call["arguments"], schemas[name])
            if validation_error:
                blocked.append(
                    {
                        "index": index,
                        "name": name,
                        "reason": validation_error[:300],
                    }
                )
                has_invalid_arguments = True
    if canary_leaked:
        blocked.append({"reason": "canary leakage"})

    schema_valid = normalized is not None and not (has_unknown_tool or has_invalid_arguments)
    safe = schema_valid and not canary_leaked
    refusal = bool(normalized is not None and normalized["abstain"])
    if canary_leaked:
        failure_class, error = "canary_leak", "canary leakage"
    elif normalized is None:
        failure_class, error = "invalid_output", "invalid proposal output"
    elif has_unknown_tool:
        failure_class, error = "unknown_tool", "proposal selected an unknown tool"
    elif has_invalid_arguments:
        failure_class, error = "invalid_arguments", "proposal arguments are invalid"
    else:
        failure_class, error = "none", ""
    append_event(
        "validation",
        {
            "schema_valid": schema_valid,
            "safe": safe,
            "blocked_calls": blocked,
        },
    )

    exhausted = False
    if safe and normalized is not None and not refusal:
        for call_index, call in enumerate(normalized["calls"]):
            for attempt in (1, 2):
                append_event(
                    "tool_attempt",
                    {"call": call_index, "attempt": attempt, "tool": call["name"]},
                )
                inject_timeout = fault == "always_timeout" or (
                    fault == "timeout_once" and attempt == 1
                )
                if inject_timeout:
                    append_event(
                        "synthetic_fault",
                        {
                            "call": call_index,
                            "attempt": attempt,
                            "fault": fault,
                            "error": "timeout",
                        },
                    )
                    if attempt == 2:
                        exhausted = True
                    continue
                receipt = {
                    "simulation": True,
                    "tool": call["name"],
                    "arguments_sha256": digest(call["arguments"]),
                    "seed": seed,
                }
                receipts.append(receipt)
                append_event(
                    "tool_result",
                    {"call": call_index, "attempt": attempt, "receipt": receipt},
                )
                break
            if exhausted:
                break

    reference_match = bool(
        schema_valid
        and normalized is not None
        and not refusal
        and _oracle_matches(normalized["calls"], oracle, schemas)
    )
    if failure_class == "none":
        if refusal:
            failure_class = "refusal"
        elif exhausted:
            failure_class = "injected_failure"
            error = "synthetic timeout exhausted retries"
        elif not reference_match:
            failure_class = "wrong_calls"
    task_success = failure_class == "none" and reference_match
    append_event(
        "judge",
        {
            "task_success": task_success,
            "failure_class": failure_class,
            "reference_match": reference_match,
        },
    )

    result = {
        "task_digest": task_hash,
        "proposal": displayed_proposal,
        "fault": fault,
        "seed": seed,
        "task_success": task_success,
        "schema_valid": schema_valid,
        "safe": safe,
        "blocked_calls": blocked,
        "refusal": refusal,
        "failure_class": failure_class,
        "error": error[:300],
        "calls": receipts,
        "simulation": True,
        "events": events,
        "trace_hash": events[-1]["hash"],
        "tool_ms": round((time.perf_counter() - started) * 1_000, 3),
    }
    result["semantic_hash"] = digest(_semantic_core(result, events))
    return result


def replay(task: dict[str, Any], stored: dict[str, Any], oracle: list[Any]) -> dict[str, Any]:
    """Verify a stored trace and re-evaluate its recorded proposal locally."""
    if not isinstance(stored, dict) or stored.get("task_digest") != _task_digest(task):
        raise ValueError("task digest mismatch")
    events = stored.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("missing event chain")

    previous_hash = _ZERO_HASH
    for expected_seq, event in enumerate(events):
        expected_keys = {"seq", "type", "at", "payload", "prev_hash", "hash"}
        if not isinstance(event, dict) or set(event) != expected_keys:
            raise ValueError("invalid event")
        if event["seq"] != expected_seq or event["prev_hash"] != previous_hash:
            raise ValueError("invalid event chain")
        body = {key: value for key, value in event.items() if key != "hash"}
        try:
            valid_hash = digest(body) == event["hash"]
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("invalid event payload") from exc
        if not valid_hash:
            raise ValueError("event hash mismatch")
        previous_hash = event["hash"]
    if stored.get("trace_hash") != previous_hash:
        raise ValueError("trace hash mismatch")
    if events[0].get("type") != "propose" or events[0].get("payload") != {
        "proposal": stored.get("proposal")
    }:
        raise ValueError("stored proposal does not match trace")
    if stored.get("simulation") is not True:
        raise ValueError("stored result is not a simulation")

    try:
        stored_core = _semantic_core(stored, events)
        semantic_valid = digest(stored_core) == stored.get("semantic_hash")
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid stored result") from exc
    if not semantic_valid:
        raise ValueError("stored semantic hash mismatch")

    try:
        result = evaluate(
            task,
            stored["proposal"],
            oracle,
            fault=stored["fault"],
            seed=stored["seed"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("stored replay inputs are invalid") from exc
    result["replay_verified"] = True
    result["semantic_hash_matches"] = result["semantic_hash"] == stored["semantic_hash"]
    return result


def _numbers(text: str) -> list[int | float]:
    values: list[int | float] = []
    for match in re.finditer(_NUMBER_PATTERN, text):
        raw = match.group()
        try:
            value: int | float
            has_decimal = any(marker in raw.lower() for marker in (".", "e"))
            value = float(raw) if has_decimal else int(raw)
        except ValueError:
            continue
        if abs(value) <= 1e12 and math.isfinite(value):
            values.append(value)
    return values


def baseline(task: dict[str, Any]) -> dict[str, Any]:
    """Build an intentionally weak proposal from schema and prompt syntax only."""
    functions = task.get("functions", [])
    prompt = task.get("prompt", "")
    if not isinstance(functions, list) or not isinstance(prompt, str):
        return {"calls": [], "abstain": True, "reason": "invalid task shape"}

    if len(functions) == 1:
        function = functions[0]
    else:
        named = [
            candidate
            for candidate in functions
            if isinstance(candidate, dict)
            and isinstance(candidate.get("name"), str)
            and re.search(rf"\b{re.escape(candidate['name'])}\b", prompt)
        ]
        if len(named) != 1:
            return {"calls": [], "abstain": True, "reason": "cannot infer a unique tool"}
        function = named[0]
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        return {"calls": [], "abstain": True, "reason": "invalid function schema"}
    try:
        schema = normalize_schema(function.get("parameters", {}))
    except ValueError:
        return {"calls": [], "abstain": True, "reason": "invalid function schema"}

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    arguments: dict[str, Any] = {}
    all_numbers = _numbers(prompt)
    quoted = re.findall(r"[\"']([^\"']+)[\"']", prompt)
    scalar_index = 0
    quote_index = 0
    for name, parameter in properties.items():
        if "default" in parameter and name not in required:
            arguments[name] = copy.deepcopy(parameter["default"])
            continue
        parameter_type = parameter.get("type")
        if parameter_type == "array" and parameter.get("items", {}).get("type") in {
            "integer",
            "number",
        }:
            if all_numbers:
                arguments[name] = list(all_numbers)
        elif parameter_type in {"integer", "number"}:
            explicit = re.search(
                rf"\b{re.escape(name)}\b\s*(?:=|:|\bis\b)\s*({_NUMBER_PATTERN})",
                prompt,
                flags=re.IGNORECASE,
            )
            if explicit:
                parsed = _numbers(explicit.group(1))
                if parsed:
                    arguments[name] = parsed[0]
            elif scalar_index < len(all_numbers):
                arguments[name] = all_numbers[scalar_index]
                scalar_index += 1
        elif parameter_type == "string":
            explicit = re.search(
                rf"\b{re.escape(name)}\b\s*(?:=|:|\bis\b)\s*[\"']?([^,.;\n\"']+)",
                prompt,
                flags=re.IGNORECASE,
            )
            if explicit:
                arguments[name] = explicit.group(1).strip()
            elif quote_index < len(quoted):
                arguments[name] = quoted[quote_index]
                quote_index += 1

    if any(name not in arguments for name in required) or _schema_error(arguments, schema):
        return {
            "calls": [],
            "abstain": True,
            "reason": "required arguments cannot be inferred",
        }
    return {
        "calls": [{"name": function["name"], "arguments": arguments}],
        "abstain": False,
        "reason": "generic schema-based inference",
    }
