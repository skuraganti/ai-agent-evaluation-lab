# Release status

**Local release gates: verified on 2026-10-16.** Source, native tests, Docker workflow/persistence, Pages browser/mobile/error states and publication privacy review passed. Actual publication is recorded by GitHub commit/deployment metadata after this prepared initial commit. See [VERIFICATION.md](VERIFICATION.md) for exact checks and limits.

Local: `sh stack.sh up` → http://127.0.0.1:8285. [Pages demo](https://skuraganti.github.io/ai-agent-evaluation-lab/) · [Source](https://github.com/skuraganti/ai-agent-evaluation-lab). Pages uses static browser interactions; full Python services run locally. Runtime databases, caches, model-control files and credentials stay excluded. Tool receipts/faults are simulated, and upstream live filenames refer to historical benchmark contributions. Scores are a local BFCL-derived planning measure, not official leaderboard scores or actual weather fulfillment. Small n1 comparisons establish no causal winner; actual model dollar cost is unavailable.

Next maintenance action: use the documented setup, verify original-source checksums before updating fixtures, rerun affected tests and retain failed/uncertain evaluation outcomes. No hosted inference or unattended service is supplied by Pages.
