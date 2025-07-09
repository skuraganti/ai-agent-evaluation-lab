# Credits and authorship

Original benchmark tasks, function schemas and reference answers: BFCL / Gorilla, Shishir G. Patil et al., Apache-2.0. Original LICENSE and dataset README retained under data/raw; complete source links/hashes in DATA_SOURCES and SOURCE_MANIFEST. Upstream optional-argument and parallel matching semantics informed this independently written, deliberately stricter adapter. No upstream evaluator package is installed or executed.

Runtime libraries FastAPI/Starlette/Uvicorn/httpx/jsonschema and their transitive dependencies are pinned with hashes in requirements.lock/uv.lock; respective licenses remain with packages. Python Docker base uses its official pinned image. Browser uses standard DOM/CSS; no external UI/analytics/CDN assets.

Original local product work: experiment design, task split/probe, sandbox receipts, run journal/recovery protocol, evaluator integration, authored interface and verification artifacts. Claude Code through the approved cookllm route is the primary core-engine implementation worker; ChatGPT supplies architecture, integration, independent review, corrections and acceptance. Prior local portfolio security/IPC process patterns were copied as independent project files and adapted; no shared mutable source runtime. Exact worker evidence is in data/verification/worker-provenance.json and private parent orchestration logs when complete.

Online reuse is credited, licensed and adapted; provenance is retained. No fabricated authorship, development history, users, or published benchmark scores.
