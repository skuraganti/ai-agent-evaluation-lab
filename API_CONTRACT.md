# Local API

All routes bind through127.0.0.1:8285. Mutations require local same-origin requests; max64KiB body, Host allowlist/CSP. There is no authentication claim for other processes/users with access to this Mac.

| Route | Behavior |
|---|---|
| `GET /health` | DB read + runner liveness;503 when background runner stops |
| `GET /api/state` | Public task summaries, capped journal, bridge heartbeat, reservation counts, cost unavailable |
| `GET /api/tasks/{id}` | Pinned prompt and original schemas, never reference answers |
| `POST /api/runs` | Exact JSON keys `task_ids`, `policy`, `fault`, `seed`, `token`;202 batch ID. Canonical UUID token deduplicates same request and rejects changed reuse.1..26 unique tasks; model max3/batch and pending3; lifetime12 reservations. Policy baseline/direct/guarded; fault none/timeout_once/always_timeout; integer seed0..1000000. Unavailable model bridge503, no fallback. |
| `GET /api/runs/{uuid}` | Run trace/proposal/metadata; reference alternatives only after completed evaluation |
| `POST /api/runs/{uuid}/replay` | Verify original chain/task, reevaluate stored proposal, create parent-linked record; no inference |
| `GET /api/export` | Local JSON attachment with complete journal and explicit cost-null/simulation flags |

Execution state `queued/awaiting/complete/failed` is distinct from task success. A completed evaluation may contain wrong calls, blocked/unsafe output, refusal or synthetic exhausted timeout. Execution error has no score and stays in the attempt denominator. Replay preserves the original outcome and is excluded from policy comparison. Historical and synthetic prompts remain labeled.
