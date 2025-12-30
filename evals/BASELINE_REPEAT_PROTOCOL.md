# Baseline repeat protocol — frozen 2026-10-16T21:52:45Z, before first measurement

Machine-readable pins: [`BASELINE_REPEAT_PROTOCOL.json`](BASELINE_REPEAT_PROTOCOL.json). Runner: `scripts/evaluate_baseline.py`.

## Question

Does the unchanged deterministic baseline give the same proposal, outcome, simulated receipts and semantic hash when it is called again, in a separate invocation, on the same pinned inputs? This is a reproducibility check of existing offline code. It is not a new model study, and it does not tune the baseline.

## Inputs

- BFCL source commit `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8` (Apache-2.0; upstream notices in `BFCL-LICENSE` and `UPSTREAM.md` are unchanged).
- Prepared tasks SHA-256 `24f2fcf16fa75f6a9296e02d1cf394f2a0029b7ad62b487e23152a410fe40cfb`; local reference SHA-256 `4061ffac0ee6946cefd3d19fa6c31a8be776b9c027ecd5ec07286be0c7ee1e37`.
- `app/catalog.py` and `app/evaluator.py` SHA-256 values recorded in the JSON. Any drift is rejected before the first evaluation.
- 25 original tasks: 5 development and 20 held-out, split by function family. These cases were already observed in earlier work, so repeats measure reproducibility on known cases, not performance on unseen tasks.
- The synthetic injection probe `probe-sort-override` runs in each repetition but is reported separately and never counted among the 25 originals.

Reference answers are passed only to the local `evaluate` judge. The baseline receives only the task copy (prompt and schemas).

## Method

1. Validate protocol shape and fixed configuration, source/code pins, task IDs and splits. Refuse to start on any mismatch.
2. For each repetition `r` in `1..R` (default 3, allowed 1–5) and each original task: call `baseline(task)` on a fresh task copy, then `evaluate(task, proposal, reference, fault="none", seed=17)`. Every call is separate; no result is reused.
3. Probe: same procedure, reported separately.
4. Synthetic fault check on development task `live_simple_86-47-0`, seed 17, faults `none`, `timeout_once`, `always_timeout`. At most 2 local attempts per call. Receipts and events are synchronous simulation; no outage occurs and nothing waits.
5. Planned evaluations are `29 × R`; the fixed cap is 145. No pure-function call touches the run journal, bridge IPC or model reservation ledger.
6. Every outcome is kept: success, wrong calls, abstention, blocked/unsafe, injected failure and Python exceptions.

## Comparison and metrics

Compared across repetitions per case: proposal hash, outcome hash (success, schema validity, safety, refusal, failure class, blocked calls, error), receipt hash and evaluator semantic hash. Trace hashes and `tool_ms` include wall-clock timestamps and timing, so they are recorded but not required to match.

Reported: per repetition and per split (development n=5, held-out n=20) success, schema-valid, safe, refusal and failure-class counts; stable and unstable case lists; probe and fault results separately; `tool_ms` min/median/max labelled as local simulated validation/judging time, not API latency.

The study covers 25 cases × R deterministic repetitions. That is not 25×R independent tasks; n stays 25. `--repetitions 1` is only a bounded smoke test: a single observation may be marked stable by the grouping code, but it cannot establish cross-repeat reproducibility. The recorded study uses three repetitions. `model_calls` is 0. No dollar cost and no model variance are reported. Any mismatch makes the run exit non-zero, and the full report is still written.

## Output

`--output` must name a new `.json` file directly under `data/verification/baseline-repeat/` (git-excluded), for example `run-001.json`. Existing files are never overwritten. The report records protocol, runner, `uv.lock`, source and code hashes, installed package list hash, Python/platform, and actual start/finish UTC.

## Run locally

From the repository root, prepare the pinned data first:

```sh
uv sync --frozen
uv run python scripts/bootstrap.py
uv run python scripts/evaluate_baseline.py --output data/verification/baseline-repeat/run-001.json
```

Use a different output filename for a separate run. The default performs 87 local evaluations: 75 original-case evaluations, 3 separate probe evaluations and 9 synthetic fault checks. No application model is invoked.
