-- Deterministic descriptive analysis is a separate command from proposal generation.
-- It consumes only a reviewed proposal context and keeps the holdout closed.

alter table lab_indicadores.runs
  drop constraint runs_run_type_check;

alter table lab_indicadores.runs
  add constraint runs_run_type_check
  check (run_type in ('preflight', 'research', 'analysis'));

alter table lab_indicadores.commands
  drop constraint commands_command_type_check;

alter table lab_indicadores.commands
  add constraint commands_command_type_check
  check (command_type in ('start_run', 'start_research', 'start_analysis', 'cancel_run'));

alter table lab_indicadores.commands
  drop constraint start_run_requires_run;

alter table lab_indicadores.commands
  add constraint start_run_requires_run
  check (command_type not in ('start_run', 'start_research', 'start_analysis') or run_id is not null);

create or replace function lab_indicadores.enqueue_analysis(
  p_idempotency_key text,
  p_proposal_key text,
  p_run_key text default null,
  p_requested_by text default null
)
returns table (run_id uuid, command_id uuid, existing boolean)
language plpgsql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
declare
  v_owner_id uuid := auth.uid();
  v_proposal_owner uuid;
  v_proposal_status text;
  v_run_id uuid;
  v_command_id uuid;
begin
  if nullif(trim(p_idempotency_key), '') is null then
    raise exception 'idempotency key is required';
  end if;
  if nullif(trim(p_proposal_key), '') is null then
    raise exception 'proposal key is required';
  end if;

  select p.owner_id, p.status
    into v_proposal_owner, v_proposal_status
  from lab_indicadores.proposals p
  where p.proposal_key = p_proposal_key;

  if not found or v_proposal_owner is distinct from v_owner_id then
    raise exception 'proposal is not owned by the current user';
  end if;
  if v_proposal_status not in ('in_review', 'accepted') then
    raise exception 'proposal is not eligible for deterministic analysis: %', v_proposal_status;
  end if;

  select c.run_id, c.id
    into v_run_id, v_command_id
  from lab_indicadores.commands c
  where c.idempotency_key = p_idempotency_key;

  if found then
    return query select v_run_id, v_command_id, true;
    return;
  end if;

  insert into lab_indicadores.runs (
    run_key, run_type, dataset_manifest, config, owner_id, requested_by
  ) values (
    coalesce(nullif(trim(p_run_key), ''), p_idempotency_key),
    'analysis',
    'hermes-analysis-absorption-v1',
    jsonb_build_object(
      'analysis_id', 'absorption-descriptive-baseline-v1',
      'proposal_key', p_proposal_key,
      'execution_profile', 'duckdb_container',
      'holdout_accessed', false
    ),
    v_owner_id,
    p_requested_by
  )
  returning id into v_run_id;

  insert into lab_indicadores.commands (
    run_id, command_type, idempotency_key, payload, owner_id, requested_by
  ) values (
    v_run_id,
    'start_analysis',
    p_idempotency_key,
    jsonb_build_object(
      'run_type', 'analysis',
      'dataset_manifest', 'hermes-analysis-absorption-v1',
      'analysis_id', 'absorption-descriptive-baseline-v1',
      'proposal_key', p_proposal_key,
      'execution_profile', 'duckdb_container',
      'holdout_accessed', false
    ),
    v_owner_id,
    p_requested_by
  )
  returning id into v_command_id;

  insert into lab_indicadores.events (run_id, event_type, message, payload)
  values (
    v_run_id,
    'analysis_queued',
    'Deterministic descriptive analysis queued',
    jsonb_build_object('command_id', v_command_id, 'proposal_key', p_proposal_key)
  );

  return query select v_run_id, v_command_id, false;
end;
$$;

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
      and c.command_type in ('start_run', 'start_research', 'start_analysis')
    order by c.requested_at, c.id
    for update skip locked
    limit 1
  ), claimed as (
    update lab_indicadores.commands c
    set status = 'claimed',
        claimed_by = p_worker_id,
        claimed_at = now()
    from next_command n
    where c.id = n.id
    returning c.id, c.run_id, c.command_type, c.payload
  )
  update lab_indicadores.runs r
  set status = 'claimed',
      worker_id = p_worker_id,
      heartbeat_at = now(),
      updated_at = now()
  from claimed c
  where r.id = c.run_id
  returning c.id, c.run_id, c.command_type, c.payload, r.dataset_manifest;
end;
$$;

create or replace function public.dashboard_enqueue_analysis(
  p_idempotency_key text,
  p_proposal_key text,
  p_run_key text default null,
  p_requested_by text default 'dashboard'
)
returns table (run_id uuid, command_id uuid, existing boolean)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select *
  from lab_indicadores.enqueue_analysis(
    p_idempotency_key,
    p_proposal_key,
    p_run_key,
    p_requested_by
  );
$$;

grant execute on function lab_indicadores.enqueue_analysis(text, text, text, text)
  to authenticated, lab_indicadores_orchestrator;
grant execute on function lab_indicadores.claim_next_command(text)
  to lab_indicadores_orchestrator;

revoke execute on function public.dashboard_enqueue_analysis(text, text, text, text) from public, anon;
grant execute on function public.dashboard_enqueue_analysis(text, text, text, text) to authenticated;
