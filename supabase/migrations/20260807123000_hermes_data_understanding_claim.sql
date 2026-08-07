-- Extend the isolated claim queue with the Hermes data-profile stage.

create or replace function lab_indicadores.claim_next_command(p_worker_id text)
returns table (
  command_id uuid,
  run_id uuid,
  command_type text,
  payload jsonb,
  dataset_manifest text
)
language plpgsql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
begin
  return query
  with next_command as (
    select c.id
    from lab_indicadores.commands c
    where c.status = 'queued'
      and c.command_type in ('start_run', 'start_research', 'start_analysis', 'start_data_profile')
    order by c.requested_at, c.id
    for update skip locked
    limit 1
  ), claimed as (
    update lab_indicadores.commands c
    set status = 'claimed', claimed_by = p_worker_id, claimed_at = clock_timestamp()
    from next_command n
    where c.id = n.id
    returning c.id, c.run_id, c.command_type, c.payload
  )
  update lab_indicadores.runs r
  set status = 'claimed', worker_id = p_worker_id,
      heartbeat_at = clock_timestamp(), updated_at = clock_timestamp()
  from claimed c
  where r.id = c.run_id
  returning c.id, c.run_id, c.command_type, c.payload, r.dataset_manifest;
end;
$$;

grant execute on function lab_indicadores.claim_next_command(text)
  to lab_indicadores_orchestrator;
