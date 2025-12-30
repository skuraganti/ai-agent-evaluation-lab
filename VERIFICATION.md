# Release verification — 2026-10-16

Actual development and release preparation 2026-10-16. This report distinguishes initial release preparation from the maintenance checks below. Deployment is checked separately after publication. Passing these bounded checks does not guarantee every future feed, model response or workload.

| Check | Observed result |
|---|---|
| Clean source environment | `uv sync --frozen`, project-specific `scripts/bootstrap.py`: passed |
| Native suite | **35 passed** with Python 3.14.8; FastAPI/Starlette emitted a TestClient deprecation warning |
| Lint | `uv run ruff check .`: passed; pinned upstream corpus files excluded from project style rules |
| Docker | Digest-pinned Python 3.13 image built; startup, HTTP health and application page passed |
| Workflow | Real BFCL task deterministic baseline and receipt replay, no model inference |
| Persistence | Restarted app container; workflow state retained |
| Browser | Isolated Chromium via agent-browser 0.38.1; 7 domain assertions passed; native input/click controls passed |
| Error state | Fixture temporarily unavailable: visible recoverable error; restored fixture loads after reload |
| Responsive layout | 1440×1000 and 390×844 inspected; 390px document width with no page overflow |
| Accessibility | axe-core 4.12.1, WCAG 2 A/AA and 2.1 AA tags: **0 detected violations** |
| Privacy | Candidate-file Gitleaks scan passed; no private home paths, unrelated service IDs, credentials or mutable data included |
| Release inference | **0 model calls**; recorded original model studies remain distinct from release checks |

Browser scenarios: 26 tasks loaded; actual saved attempt selected; saved chain/equality integrity passes; copied payload tamper detected; reference baseline disclosed; missing run no fabricated chain; injection displayed as untrusted data.

Some axe color checks on clipped scroll contents require manual review. Visible desktop/mobile views and the authored foreground/background palette were inspected; the minimum checked ink/muted/link ratio against the declared page/panel backgrounds was 5.65:1. This is a bounded audit, not a universal accessibility certification. Scroll regions are keyboard focusable and tables accompany charts/graphs.

## Corrections and retesting

The local evaluator duration is labeled separately from observed model latency. Reference answers, recorded model attempts and synthetic tool receipts remain distinct. The full BFCL Apache-2.0 notice and keyboard-accessible trace/schema regions are included.

## Recorded findings and limitations

Tool receipts/faults are simulated, and upstream live filenames refer to historical benchmark contributions. Scores are a local BFCL-derived planning measure, not official leaderboard scores or actual weather fulfillment. Small n1 comparisons establish no causal winner; actual model dollar cost is unavailable.

Native tests use explicit fixtures, original-source boundary checks and simulated model responses; they do not perform live inference. See [PUBLIC_DEMO_DATA.md](PUBLIC_DEMO_DATA.md) for the curated Pages fixture and checksum, [DATA_SOURCES.md](DATA_SOURCES.md) for publisher/version/rights and [UPSTREAM.md](UPSTREAM.md) for attribution. Original model studies have small samples and cannot establish general performance or costs.

## Reproduce

```sh
uv sync --frozen
uv run python scripts/bootstrap.py
uv run pytest
uv run ruff check .
sh stack.sh up
```

Local URL: http://127.0.0.1:8285. The container limit is 512 MiB for the app, plus Docker and host process overhead. Tested one portfolio stack at a time on an ARM Mac with 8 GiB host RAM and a 2 GiB Docker VM. No GPU. Defaults use independent named volumes and localhost bindings. Stack stop retains state; no unrelated services are managed.

For Pages: `python3 -m http.server 8008 --bind 127.0.0.1 --directory docs`. With optional agent-browser 0.38.1 installed, run `sh scripts/check-pages.sh`; assertions are in `tests/pages-smoke.js`. No browser automation package is shipped to Pages.

## Maintenance: repeated offline baseline and replay

The bounded baseline protocol was frozen before its cloud measurements, then independently accepted before the formal local run. [The complete recorded local report](examples/baseline-repeat-v1.json) retains 25 original cases × 3 repetitions (5 development / 20 held-out), three separate synthetic-probe evaluations and nine separate numeric fault checks: **87 evaluations**, with **0 application-model calls**. The unchanged baseline gave **3/25 successes, 20 refusals, 2 wrong-call outcomes** in each repetition; development 2/5, held-out 1/20. Every proposal/outcome/receipt/semantic hash was stable. All outcomes remain visible. These previously observed cases establish deterministic reproducibility, not 75 independent tasks, model variance, general accuracy or a policy winner.

An independent check recomputed every recorded row's proposal/outcome hashes, rejudged all 28 distinct task/fault combinations against pinned local references and compared all 87 rows, and recomputed split counts and timing statistics. The three original-case timing medians were 0.791, 0.795, 0.789 ms on Python 3.14.8 / ARM macOS; these measure local simulated validation/judging, not tool/API latency. The public report records exact protocol, runner, source, package/lock/runtime hashes and actual measurement times; dollars remain null.

**43 native tests passed**, including 8 new harness regressions; `uv run ruff check .` passed. Tests separately check fresh invocations, known metrics, timing-independent semantic stability, drift refusal, bounds, output exclusivity and no network dispatch. A TestClient deprecation warning remains. The formal report's runner and protocol pins match the accepted bytes. No application/evaluator/source/oracle/bridge/dependency/cap change was made.

An isolated Docker build/start/health check used the real pinned numeric task with an explicit synthetic once-only timeout. API idempotency returned the same batch. API replay and a browser **Replay offline** action created two distinct parent-linked runs, verified saved-chain/semantic matches and left the original unchanged. The trace displayed two local attempts around a labeled synthetic fault. All three saved rows, full state and export survived container restart exactly; reserved/pending model counts stayed 0. A deep-copy payload edit was rejected as an event hash mismatch. The verification stack was stopped with its volume retained. These backend checks are distinct from the Pages site's saved-fixture equality check.

Use [the protocol and command](evals/BASELINE_REPEAT_PROTOCOL.md) and [trace replay walkthrough](docs/TRACE_REPLAY.md) to reproduce. No original eight-call model study was rerun. The static Pages application and saved fixture are unchanged.
