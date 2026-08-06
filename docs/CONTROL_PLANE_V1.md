# Control plane v1

## Scope

This control plane belongs exclusively to `lab-indicadores`. It does not
reuse `lab_automatizado`, its workers or its tables.

The first vertical slice supports one deterministic command:

```text
enqueue_preflight -> claim_next_command -> run worker -> artifact -> events
```

The dashboard and chat will be clients of this same command contract. Neither
client receives unrestricted shell access.

The MVP dashboard reaches the control plane through the `public.dashboard_*`
RPC gateway. These wrappers are `SECURITY INVOKER`, are executable only by
authenticated users, and preserve the owner-scoped RLS boundary of the private
schema.

## State model

Run states:

```text
queued -> claimed -> running -> succeeded
                         \-> failed
queued/claimed/running -> cancel_requested -> cancelled
```

Command states are separate from run states and use their own terminal status.
The `idempotency_key` is unique and prevents duplicate runs when the same
request is repeated by the panel, chat or a retrying client.

## First command

- function: `lab_indicadores.enqueue_preflight`
- manifest: `indicator-lab-smoke-v1`
- worker: `lab-indicadores-worker`
- artifact root: `/srv/labs/projects/lab-b/runs`
- holdout: must remain `false`

The host-side orchestrator claims work through
`lab_indicadores.claim_next_command`. The worker itself remains unaware of
Supabase and writes only its report to the lab-owned VPS path. The orchestrator
then writes events and artifact metadata to the control plane. The report is
referenced by URI plus SHA-256.

## Access boundary

- panel users: authenticated, owner-scoped read access;
- orchestrator: dedicated Postgres login scoped to the `lab_indicadores` schema;
- worker: no Supabase credential unless a future job explicitly requires it;
- raw Parquets: mounted read-only;
- artifacts: only under `lab-b`;
- `service_role` never reaches browser code.

## Acceptance criteria

1. Repeating an idempotency key returns the same run and command.
2. Only one worker can claim a queued command.
3. Every state transition emits an event.
4. The preflight report contains an artifact hash and `holdout_accessed=false`.
5. A failed worker leaves an auditable error and no false success.
6. No command can target `lab-a`, `lab_automatizado` or the global dataset for writing.
