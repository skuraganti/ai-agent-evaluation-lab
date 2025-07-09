# Sources and local preparation

Actual acquisition/development: 2026-10-16. Complete URLs, immutable revision, bytes, SHA256 and captured retrieval timestamps are in [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json). Dataset content is untrusted input; no downloaded evaluator or repository script is executed.

## Candidate comparison and choice

| Candidate | Original publisher / license | Fit and tradeoff |
|---|---|---|
| [Berkeley Function Calling Leaderboard](https://github.com/ShishirPatil/gorilla/tree/6ea57973c7a6097fd7c5915698c54c17c5b1b6c8/berkeley-function-call-leaderboard) | Shishir G. Patil et al., Gorilla/Berkeley; Apache-2.0, explicitly including dataset in README | Chosen: small JSONL task/schema/reference pairs enable inspectable call planning, exact local grading and replay without installing upstream runtime or executing external tools. |
| [τ³-bench / tau2-bench](https://github.com/sierra-research/tau2-bench) | Sierra Research; MIT | Richer stateful policies and user simulator; more domain/runtime and model orchestration than needed for a bounded two-policy experiment on this Mac. Current project still uses tau2-bench repository name. Not acquired or executed. |
| [WebArena](https://github.com/web-arena-x/webarena) | WebArena authors; Apache-2.0 repository | Valuable browser-agent benchmark, but self-hosted websites/reset/browser state introduce substantially more environment state. This lab chooses function planning to fit the2GB Docker VM. No claim that a resource benchmark was measured for WebArena. Not installed. |

This selection is an engineering judgment about fit, not a measured ranking of benchmark quality. Research review independently checked original LICENSE/README, paired IDs and upstream marker/multiset semantics.

## Acquired BFCL snapshot

Repository commit `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8` (resolved from public main at acquisition). Files under `berkeley-function-call-leaderboard/bfcl_eval/data/`:

- `BFCL_v4_live_simple.json`:258 unique tasks,260140bytes; paired `possible_answer/` file258 exact matching IDs.
- `BFCL_v4_live_parallel.json`:16 unique tasks,15649bytes; paired answer file16 exact matching IDs,2–6 calls each.
- `BFCL_v4_live_relevance.json`:16 unused comparison rows; probed answer path returned404. Excluded from scoring; no labels invented.
- Original dataset README and repository Apache-2.0 LICENSE retained in `data/raw`. Full checksum manifest includes both reference files. Source bundle is approximately407KB, prepared tasks/oracle below200KB each.

“Live” is upstream naming for historical user-contributed queries and tool documentation. [Original collection methodology](https://gorilla.cs.berkeley.edu/blogs/12_bfcl_v2_live.html) reports February26–April1,2024 collection followed by cleaning, enhancement and sensitive placeholder substitution. It is not an active weather/API feed, and not necessarily untouched production text. Dataset revision date is distinct from original collection. Exact per-row collection/publication times are unavailable.

Preserve publisher copyright and license notices. Apache-2.0 permits reuse subject to its conditions; modifications here are local schema adaptation, purposive sampling and one synthetic augmentation. No claim to authorship of original tasks, upstream checker, or benchmark concept. Cite BFCL authors and the original project when sharing permitted derivatives; raw data caches and private runtime archives are excluded from the public release.

## Reproducible preparation

`uv sync --frozen` then `uv run python scripts/acquire.py`. Acquisition uses pinned raw URLs, no redirects/proxies/private cookies,20s request timeout and2MB per-file bound. Existing mismatches fail; files are never silently replaced. All unchanged cached bytes are reused. Original first-download timestamps were reconstructed immediately from file mtime after one404 interrupted the initial inline acquisition; this basis is explicit in the manifest rather than represented as server publication time.

Selection indices are explicit in `scripts/acquire.py` and prepared manifest:5 development tasks (sort, sum, tax family),20 held-out weather-planning tasks (15 simple,5 parallel). No function-name family occurs in both development and held-out splits. Held-out tasks still share families among themselves, and the sample deliberately overrepresents weather. Public benchmark training contamination cannot be ruled out. Source data has multilingual prompts despite an upstream English metadata label; selected Portuguese text is retained without translation.

Every selected task preserves original `id`, exact function descriptions/parameters, and all message content with visible original role labels inside untrusted prompt text. Embedded roles never become host/system instructions. Regex screen refuses email-shaped text and credential/card field names in selected records; human inspection also selected public city/coordinate/arithmetic tasks. This bounded screen and upstream redaction are not a global privacy guarantee for the unused raw corpus. No private candidate context is imported.

Prepared `tasks.json` SHA256 `24f2fcf16fa75f6a9296e02d1cf394f2a0029b7ad62b487e23152a410fe40cfb`; separate `answers.json` SHA256 `4061ffac0ee6946cefd3d19fa6c31a8be776b9c027ecd5ec07286be0c7ee1e37`. Runtime pins both. Model bridge reads only the first, verifies its fingerprint and reconstructs payload by ID. Implementation worker was forbidden raw/oracle/prepared/evaluation keys. Answers reach only the local judge and post-run inspector.

## Local scoring and synthetic material

BFCL schema dialect (`dict`, `float`, `any`) is normalized locally. Objects close additional properties; optional fields with null default accept null. Empty-string oracle entries permit optional omission, not arbitrary empty-string arguments. Calls match order-independent one-to-one, preserving duplicate multiplicity. String case/punctuation remain exact, unlike [upstream checker normalization](https://raw.githubusercontent.com/ShishirPatil/gorilla/6ea57973c7a6097fd7c5915698c54c17c5b1b6c8/berkeley-function-call-leaderboard/bfcl_eval/eval_checker/ast_eval/ast_checker.py). Bool never equals a number; int/float numeric equality is accepted. Results are **local BFCL-derived call-planning scores**, not official leaderboard scores or end-to-end task fulfillment.

All tool responses are deterministic local simulation receipts, not observed external domain data. Timeout-once/exhausted faults are injected, not actual service outages. `probe-sort-override` appends a synthetic malicious observation to the original development sort task; canary and invalid.example destination are invented test values. Actual model proposals, measured CLI latency and local dispatch traces are observed executions of this lab. Static dataset snapshots, offline replay, synthetic faults and live model inference have distinct labels in UI/reports.
