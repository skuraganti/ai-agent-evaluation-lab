# TraceLab browser demonstration

Serve static `docs` files or use GitHub Pages. The full Python/Docker application and local startup instructions are in the [source repository](https://github.com/skuraganti/ai-agent-evaluation-lab).

The fixture includes 25 original BFCL development tasks, an explicitly synthetic untrusted probe, and eight actual recorded model planning runs. Recorded direct/guarded selections display their saved proposals, per-run n=1 metrics and explicitly simulated tool receipts; no new inference or external tool execution occurs. The reference adapter is a benchmark reference, not an evaluated model. Missing task/policy pairs remain unavailable. Actual costs are unavailable.

Integrity interaction transparently compares the copied event payload with the loaded fixture, checks sequence and saved previous-hash links, and checks the final saved trace hash. Tampering is a local copy only. It does not independently recalculate the original cryptographic hashes or prove authenticity. Source: [BFCL pinned development data](https://github.com/ShishirPatil/gorilla/tree/6ea57973c7a6097fd7c5915698c54c17c5b1b6c8/berkeley-function-call-leaderboard).
