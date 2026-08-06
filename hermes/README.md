# Hermes / indicador laboratory

This directory is the private runtime boundary for `hermes-indicadores`.

The first activation stage is observation only:

- reads the canonical development sample mounted on the VPS;
- writes an atomic heartbeat under this laboratory's `hermes/outbox`;
- has no Supabase credential, service-role key, Docker socket, holdout access,
  network access, or execution capability;
- is registered by this laboratory's existing control-plane orchestrator;
- never writes to `/srv/labs/projects/lab_automatizado` or its schema.

The hypothesis engine is intentionally not enabled by this bootstrap. It will
be added as a separate proposal stage with its own model credential, context
contract, proposal artifacts, review gate, and dashboard evidence trail.
