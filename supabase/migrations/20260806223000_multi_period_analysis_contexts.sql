-- Allow the dashboard to select only one of the versioned, asset-specific
-- deterministic analysis contexts. The worker remains the final authority.

create or replace function lab_indicadores.enqueue_analysis_context(
  p_idempotency_key text,
  p_proposal_key text,
  p_analysis_context_id text,
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
  v_proposal_asset text;
  v_expected_asset text;
  v_manifest text;
  v_run_id uuid;
  v_command_id uuid;
begin
  if nullif(trim(p_idempotency_key), '') is null then
    raise exception 'idempotency key is required';
  end if;
  if nullif(trim(p_proposal_key), '') is null then
    raise exception 'proposal key is required';
  end if;

  if p_analysis_context_id = 'absorption-descriptive-baseline-v1' then
    v_manifest := 'hermes-analysis-absorption-v1';
    v_expected_asset := 'WDO';
  elsif p_analysis_context_id = 'absorption-descriptive-multi-period-wdo-v1' then
    v_manifest := 'hermes-analysis-absorption-multi-period-wdo-v1';
    v_expected_asset := 'WDO';
  elsif p_analysis_context_id = 'absorption-descriptive-multi-period-win-v1' then
    v_manifest := 'hermes-analysis-absorption-multi-period-win-v1';
    v_expected_asset := 'WIN';
  else
    raise exception 'analysis context is not allowlisted: %', p_analysis_context_id;
  end if;

  select p.owner_id, p.status, p.asset
    into v_proposal_owner, v_proposal_status, v_proposal_asset
  from lab_indicadores.proposals p
  where p.proposal_key = p_proposal_key;

  if not found or v_proposal_owner is distinct from v_owner_id then
    raise exception 'proposal is not owned by the current user';
  end if;
  if v_proposal_status not in ('in_review', 'accepted') then
    raise exception 'proposal is not eligible for deterministic analysis: %', v_proposal_status;
  end if;
  if v_proposal_asset is distinct from v_expected_asset then
    raise exception 'analysis context asset does not match proposal asset';
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
    v_manifest,
    jsonb_build_object(
      'analysis_context_id', p_analysis_context_id,
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
      'dataset_manifest', v_manifest,
      'analysis_context_id', p_analysis_context_id,
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
    'Versioned deterministic analysis queued',
    jsonb_build_object(
      'command_id', v_command_id,
      'proposal_key', p_proposal_key,
      'analysis_context_id', p_analysis_context_id,
      'dataset_manifest', v_manifest
    )
  );

  return query select v_run_id, v_command_id, false;
end;
$$;

create or replace function public.dashboard_enqueue_analysis_context(
  p_idempotency_key text,
  p_proposal_key text,
  p_analysis_context_id text,
  p_run_key text default null,
  p_requested_by text default 'dashboard'
)
returns table (run_id uuid, command_id uuid, existing boolean)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select *
  from lab_indicadores.enqueue_analysis_context(
    p_idempotency_key,
    p_proposal_key,
    p_analysis_context_id,
    p_run_key,
    p_requested_by
  );
$$;

revoke execute on function public.dashboard_enqueue_analysis_context(text, text, text, text, text) from public, anon;
grant execute on function public.dashboard_enqueue_analysis_context(text, text, text, text, text) to authenticated;
grant execute on function lab_indicadores.enqueue_analysis_context(text, text, text, text, text) to authenticated, lab_indicadores_orchestrator;
