-- The login role password is provisioned out-of-band and never stored here.
-- This migration grants the orchestrator access only inside lab_indicadores.

do $$
begin
  if not exists (
    select 1
    from pg_roles
    where rolname = 'lab_indicadores_orchestrator'
  ) then
    raise exception 'lab_indicadores_orchestrator must be provisioned before this migration';
  end if;
end
$$;

grant usage on schema lab_indicadores to lab_indicadores_orchestrator;
grant select, insert, update on
  lab_indicadores.runs,
  lab_indicadores.commands,
  lab_indicadores.events,
  lab_indicadores.artifacts,
  lab_indicadores.workers
to lab_indicadores_orchestrator;
grant usage, select on sequence lab_indicadores.events_id_seq
to lab_indicadores_orchestrator;

grant execute on function lab_indicadores.claim_next_command(text)
to lab_indicadores_orchestrator;
grant execute on function lab_indicadores.record_event(uuid, text, text, jsonb)
to lab_indicadores_orchestrator;

create policy runs_orchestrator_manage
  on lab_indicadores.runs
  for all
  to lab_indicadores_orchestrator
  using (true)
  with check (true);

create policy commands_orchestrator_manage
  on lab_indicadores.commands
  for all
  to lab_indicadores_orchestrator
  using (true)
  with check (true);

create policy events_orchestrator_insert
  on lab_indicadores.events
  for insert
  to lab_indicadores_orchestrator
  with check (true);

create policy artifacts_orchestrator_insert
  on lab_indicadores.artifacts
  for insert
  to lab_indicadores_orchestrator
  with check (true);

create policy workers_orchestrator_manage
  on lab_indicadores.workers
  for all
  to lab_indicadores_orchestrator
  using (true)
  with check (true);
