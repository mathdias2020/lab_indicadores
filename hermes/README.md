# Hermes / indicador laboratory

This directory is the private runtime boundary for `hermes-indicadores`.

The first activation stage is observation only:

- reads the canonical development sample mounted on the VPS;
- writes an atomic heartbeat under this laboratory's `hermes/outbox`;
- has no Supabase credential, service-role key, Docker socket, holdout access,
  network access, or execution capability;
- is registered by this laboratory's existing control-plane orchestrator;
- never writes to `/srv/labs/projects/lab_automatizado` or its schema.

The proposal stage is separate from the observation runtime. When OpenAI is
selected, Hermes may request up to three semantic exploration questions from a
fixed catalog. The worker executes those questions with DuckDB against the
development Parquets in read-only mode; the model never submits SQL, file
paths, or arbitrary columns and receives bounded aggregates rather than raw
trades. Exploration reports are hashable artifacts under this laboratory's
outbox and remain subject to the human review gate.
