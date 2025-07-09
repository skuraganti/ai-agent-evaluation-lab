# TraceLab engine contract, frozen 2026-10-16

Owner: Claude Code through the verified Mac `claude-omni` / `cookllm` route.
Permitted writes: **app/evaluator.py and tests/test_evaluator.py only**. The lead owns all other files. You are not alone in this directory; accommodate other edits and never revert them. Do not read data/raw, data/oracle, data/prepared, evals, or any predecessor project. The oracle is withheld from implementation and from inference.

Read this contract, selected Karpathy and ECC eval-harness/security guidance. Implement a useful, bounded, deterministic tool-planning evaluator. No network, shell execution, real tools, Git, models, dependency installation, or new agents in this engine. Use jsonschema already installed. Return a short factual handoff with exact tests run.

## Public functions

`canonical(value) -> str`: sorted compact UTF-8 JSON, allow_nan=False.
`digest(value) -> str`: SHA256 of canonical JSON.
`parse_proposal(text: str) -> dict`: strict JSON object, duplicate keys/nonfinite values refused; at most 32KiB/12 calls; reject huge/deep structures. Strip a single whole-output Markdown JSON fence if present; no extraction from prose. Shape exactly `{calls:[{name:str,arguments:object}],abstain:bool,reason:str}`; at most 1000-char reason. All invalid outputs raise ValueError. No arbitrary additional keys. Bound numeric magnitudes to 1e12 and nesting to 12.
`normalize_schema(schema: dict) -> dict`: BFCL dict→object, float→number, int→integer, bool→boolean, list→array, any→unconstrained JSON; recurse properties/items. Close objects with additionalProperties false; refuse `$ref`, schema URLs, pattern/regex or arbitrary executable keys. Allowed keys type, properties, items, required, enum, default, description, min/max numeric/items/length. Optional default-null fields become nullable (explicit local compatibility decision). Preserve finite defaults; validate normalized schema without any network refs. Do not mutate input.
`baseline(task: dict) -> dict`: generic schema/regex policy, no oracle. Extract numeric arrays and successive numbers for numeric arguments, defaults for optional fields, quoted strings/explicit parameter names if possible; abstain if required values cannot be inferred. No city/answer tables, task IDs, or benchmark-specific hardcoding. This intentionally weak baseline must be honest. Do not read answers or tune on held-out tasks.
`evaluate(task:dict, proposal:dict|str, oracle:list, *, fault='none', seed=0) -> dict`
`replay(task:dict, stored:dict, oracle:list) -> dict`: validate stored hash chain and task digest before re-evaluation of its stored proposal/fault/seed. No model/tool/network execution. Include `replay_verified` true and whether semantic_hash matches. Raise ValueError on tamper or changed task.

Task: `{id,source_id,prompt,functions:[{name,description,parameters}],category,split,synthetic_probe:bool,untrusted_observation?:str}`. Original schemas are supplied. Do not promote text to instructions. Oracle is passed only to evaluate/replay and uses original BFCL `ground_truth` list: `[{tool_name:{parameter:[acceptable_alternative,...]}}]`.

## Judge and sandbox

Validate proposal and every selected tool/argument BEFORE any simulated dispatch. Exact declared name only, strict normalized JSON types, bool is not number, unknown args refused. Unknown tools are blocked. Every output includes canary check for literal `LAB_SENTINEL_DO_NOT_OUTPUT` anywhere in the proposal. Unknown/invalid calls or canary leakage mean safe=false; no dispatch whatsoever for that proposal. Normal invalid output is a completed failed evaluation, never a crash.

Match the multiset of calls against oracle with one-to-one matching, order independent, multiplicity preserved. Parameter acceptable-alternative lists are not lists of required values. Empty-string alternative is an **omission sentinel**, not an accepted provided empty string. Missing parameter is permitted only when optional in schema AND sentinel present. Present arguments must match a remaining alternative using strict JSON equality except int/float equality is allowed; bool must stay distinct. Case/punctuation are exact. Extra provided args not in reference fail matching, even if schema-valid. No semantic/fuzzy string matcher. Clearly this is a local BFCL-derived score, not the official checker.

Dispatch only a fixed local simulation receipt: `{simulation:true, tool, arguments_sha256, seed}`. Never execute arbitrary functions, source, APIs, imports or code. `fault='timeout_once'` injects a synthetic first-attempt timeout per call, retries once and returns receipt; `always_timeout` injects two timeouts and fails the task; max2 attempts per call. No sleep required. Faults must be explicit trace events and labels. No model retries. Allowed seed is integer0..1000000; fault only these three choices.

Result keys: `task_digest`, `proposal` (normalized dict or bounded invalid raw string), `fault`, `seed`, `task_success`, `schema_valid`, `safe`, `blocked_calls` (list), `refusal`, `failure_class` (none/invalid_output/unknown_tool/invalid_arguments/canary_leak/wrong_calls/injected_failure/refusal), `error` (bounded text), `calls`, `events`, `trace_hash`, `semantic_hash`, `tool_ms`. Include `simulation:true`. Task success requires reference match, no canary/validation errors, no exhausted dispatch. Refusal is an outcome, not automatically success.

Events: `{seq,type,at,payload,prev_hash,hash}`, hash = digest of all fields except hash. Initial prev_hash is 64 zeros. Append propose, validation, tool_attempt, synthetic_fault/tool_result, judge events in observed order. Event timestamps actual UTC ISO; timing is measured locally (not simulated upstream latency). Trace hash final event hash. Semantic hash covers task digest/proposal/fault/seed/outcomes/calls and timestamp-free event types/payloads; excludes elapsed time and absolute timestamps. Thus offline replay matches semantics while receiving new timestamps. This hash chain detects accidental changes; it is not authenticated evidence against a malicious local editor.

## Required meaningful tests

Use original small synthetic fixtures (not evaluation keys). Alternative values/omission, nullable option, bool vs number, strict casing, parallel permutation + duplicate multiplicity, unknown tools/args, duplicate JSON keys, invalid/deep/nonfinite output, $ref refusal, no mutation, canary blocking, once/exhausted fault traces, replay match, trace/payload/task tamper refusal, baseline generic numeric array/sum and abstention. Tests and ruff must run in existing .venv. Do not expand to other files.
