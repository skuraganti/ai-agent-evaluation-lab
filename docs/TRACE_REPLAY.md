# Trace replay

TraceLab saves every evaluation as a hash-chained event trace. A replay checks a saved trace and runs the deterministic evaluator again on the **saved proposal**. It never calls the baseline again and never runs model inference. This page covers the full local backend, the local UI and the static Pages demonstration, which do different things.

## What a saved result contains

`app/evaluator.py:evaluate` writes events in a fixed order: `propose`, `validation`, then for each allowed call `tool_attempt` and either `synthetic_fault` or `tool_result`, and finally `judge`. Each event has `seq`, `type`, `at` (UTC timestamp), `payload`, `prev_hash` and `hash = sha256(canonical event body)`. The last event's hash is `trace_hash`.

`semantic_hash` covers only the outcome fields and the event types and payloads (`task_digest`, `proposal`, `fault`, `seed`, `task_success`, `schema_valid`, `safe`, `blocked_calls`, `refusal`, `failure_class`, `error`, `calls`, events without `at`/hashes). Timestamps and `tool_ms` are left out. So a correct replay keeps the same `semantic_hash` but gets a new `trace_hash`.

Tool receipts are simulated (`"simulation": true`). Synthetic faults (`timeout_once`, `always_timeout`) run synchronously with at most 2 local attempts per call; no external tool is contacted and nothing waits.

## Full local backend walkthrough

Start the stack as described in the README (`sh stack.sh up`, then `http://127.0.0.1:8285`). The examples use only the `baseline` policy, so the optional model bridge is not needed. Mutating requests must come from the same origin or carry no `Origin` header, the `Host` must be local, and bodies are capped at 64 KiB.

### 1. Submit a run

```sh
TOKEN=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl -s -X POST http://127.0.0.1:8285/api/runs \
  -H 'Content-Type: application/json' \
  -d "{\"task_ids\":[\"live_simple_86-47-0\",\"live_simple_4-3-0\"],\"policy\":\"baseline\",\"fault\":\"none\",\"seed\":17,\"token\":\"$TOKEN\"}"
# 202 {"batch_id":"…","deduplicated":false}
```

The body must have exactly the keys `task_ids` (1–26 distinct pinned IDs), `policy` (`baseline`, `direct` or `guarded`), `fault` (`none`, `timeout_once` or `always_timeout`), `seed` (integer 0–1,000,000) and `token` (a canonical lowercase UUID).

- Sending the same token with the same body again returns the same `batch_id` with `"deduplicated": true`. Nothing new is evaluated. **This is idempotency, not a repeat.** For a separate evaluation, use a new token.
- The same token with a different body is rejected (`Idempotency token was used for a different request`). A non-canonical token, such as an uppercase UUID, is rejected too.
- `direct`/`guarded` return 503 when the approved bridge heartbeat is missing. There is no fallback.

### 2. Inspect the result

```sh
curl -s http://127.0.0.1:8285/api/state      # runs[], counts, task summaries
curl -s http://127.0.0.1:8285/api/runs/RUN_ID # one run with events
```

Execution state (`queued`, `awaiting`, `complete`, `failed`) is separate from task outcome. A `complete` run may still be a task failure: `wrong_calls`, `refusal`, `invalid_arguments`, `unknown_tool`, `canary_leak` or `injected_failure`. With seed 17, for example, `live_simple_86-47-0` passes and the baseline abstains (`refusal`) on `live_simple_4-3-0`. `reference_calls` appears in `GET /api/runs/{id}` only once a result exists. It is added for display after judging and is never sent to the baseline or the bridge.

### 3. Replay

```sh
curl -s -X POST http://127.0.0.1:8285/api/runs/RUN_ID/replay
# 201 {"id":"NEW_RUN_ID","replay":{…,"replay_verified":true,"semantic_hash_matches":true}}
```

The server:

1. Requires `state == "complete"` with a saved `result`. A complete run that failed the task or abstained can be replayed. A journal `failed` run (for example an interrupted or invalid bridge response) has no result and returns 400 `Only a completed evaluation has a replayable proposal`.
2. Checks the saved task digest against the pinned task, then each event's sequence, `prev_hash` link and recomputed hash, the final `trace_hash`, that the first `propose` payload equals the saved `proposal`, `simulation == true`, and the saved `semantic_hash`.
3. Re-evaluates the saved proposal with the saved `fault` and `seed` against the local reference.
4. Saves the result as a **new run** with `parent_id` = the original ID. The original row is not changed.

Check both flags:

```sh
curl -s -X POST http://127.0.0.1:8285/api/runs/RUN_ID/replay \
  | python3 -c 'import json,sys; r=json.load(sys.stdin)["replay"]; assert r["replay_verified"] and r["semantic_hash_matches"], r; print("ok")'
```

`replay_verified` means the saved chain was internally consistent. `semantic_hash_matches` means the re-evaluation gave the same semantic outcome. A `false` value for the second one points to evaluator drift since the original run.

### Accounting

- Replays are excluded from policy comparison denominators. The UI metrics count only rows without `parent_id`, and the frozen study tables use original attempts only.
- Replays never reserve model attempts. `model_attempts_reserved` counts only non-baseline rows without `parent_id`, so replaying a recorded `direct` run uses no model budget and needs no bridge.
- Replays do count toward the 500-row local journal cap.

### Tampering

Test tampering only on a deep copy of a result, never on the journal:

```python
import copy
from app.catalog import Catalog
from app.evaluator import replay

catalog = Catalog()
row = ...  # complete JSON response from GET /api/runs/RUN_ID
task_id = row["task_id"]
saved = row["result"]
copied = copy.deepcopy(saved)
copied["events"][0]["payload"]["proposal"]["reason"] = "edited"
replay(catalog.task(task_id), copied, catalog.answer(task_id))  # ValueError: event hash mismatch
```

Editing an outcome field such as `failure_class` instead gives `stored semantic hash mismatch`.

**Limits.** The chain uses plain SHA-256 with no key or signature. It catches accidental or partial edits. Anyone who can write the journal can also recompute every hash, so a passing replay does not prove authenticity or who wrote the trace.

## Local UI

In the local app, **Inspect** shows events, the proposal, reference calls (after judging), `trace_hash`, `semantic_hash`, and for replays `replay_verified` and `semantic_match`. The **Replay offline** button is enabled only for `complete` runs and calls the endpoint above. Replays are marked `replay /` in the journal.

## Pages demonstration (static)

The Pages site (`docs/index.html`, `docs/app.js`, `docs/demo-data.json`) has no backend and runs no evaluator.

- `docs/demo-data.json` holds eight saved records: six actual model planning calls (direct and guarded on `live_simple_4-3-0`, `live_simple_85-46-0`, `live_parallel_1-0-1`) and two separate synthetic probe attempts (direct and guarded on `probe-sort-override`). Each case/policy pair has n=1. Actual USD cost and the backend model version are unknown.
- **Check saved chain** compares a copied event list with the loaded fixture and checks sequence numbers, saved `prev_hash` links and the final saved `trace_hash`. It does **not** recompute SHA-256 hashes or re-evaluate anything. Its tamper checkbox changes only the browser copy.
- The **Reference adapter** option shows the saved benchmark reference. It is not an evaluated model or a baseline run.

## Where saved study files are

- Original eight-call study, on the machine that ran it: `data/verification/live-results.json` (git-excluded). A fresh clone does not have it, and `evals/live-results.json` does not exist. The public copy of those eight records is `docs/demo-data.json`.
- Baseline reproducibility study: `scripts/evaluate_baseline.py --output data/verification/baseline-repeat/run-001.json` (git-excluded, never overwritten). See `evals/BASELINE_REPEAT_PROTOCOL.md`. It calls the pure baseline and evaluator directly and never touches the journal, bridge or reservation ledger.

Benchmark tasks come from BFCL/Gorilla (Apache-2.0) at commit `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8`; see `BFCL-LICENSE` and `UPSTREAM.md`. TraceLab's original code keeps the licensing choice in `LICENSING.md`.
