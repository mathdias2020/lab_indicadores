-- Hermes proposal contract for the indicator laboratory.
-- Proposals are hypotheses under review, never executable trading instructions.

alter table lab_indicadores.commands
  drop constraint commands_command_type_check;

alter table lab_indicadores.commands
  add constraint commands_command_type_check
  check (command_type in ('start_run', 'start_research', 'cancel_run'));

alter table lab_indicadores.commands
  drop constraint start_run_requires_run;

alter table lab_indicadores.commands
  add constraint start_run_requires_run
  check (command_type not in ('start_run', 'start_research') or run_id is not null);

create table lab_indicadores.proposals (
  id uuid primary key default gen_random_uuid(),
  proposal_key text not null unique,
  run_id uuid not null references lab_indicadores.runs(id) on delete cascade,
  agent_id text not null references lab_indicadores.agents(agent_id) on delete restrict,
  owner_id uuid references auth.users(id) on delete set null,
  status text not null default 'in_review'
    check (status in ('draft', 'in_review', 'accepted', 'rejected', 'superseded', 'error')),
  evidence_level text not null default 'not_tested'
    check (evidence_level in ('not_tested', 'descriptive', 'explanatory', 'predictive', 'operational', 'rejected')),
  asset text not null
    check (asset in ('WDO', 'WIN')),
  track text not null
    check (track in ('flow', 'price', 'flow_price')),
  horizon text not null
    check (horizon in ('scalping', 'tactical_intraday', 'broad_intraday')),
  title text not null,
  question text not null,
  mechanism text not null,
  hypothesis text not null,
  validation_plan jsonb not null default '{}'::jsonb,
  limitations jsonb not null default '[]'::jsonb,
  source_context_uri text not null,
  source_context_sha256 text not null check (source_context_sha256 ~ '^[0-9a-f]{64}$'),
  proposal_sha256 text not null check (proposal_sha256 ~ '^[0-9a-f]{64}$'),
  artifact_uri text not null,
  holdout_accessed boolean not null default false
    check (holdout_accessed = false),
  reviewer_note text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index proposals_owner_created_idx
  on lab_indicadores.proposals (owner_id, created_at desc);

create index proposals_status_created_idx
  on lab_indicadores.proposals (status, created_at desc);

create trigger proposals_touch_updated_at
before update on lab_indicadores.proposals
for each row execute function lab_indicadores.touch_updated_at();

alter table lab_indicadores.proposals enable row level security;

create policy proposals_owner_select
  on lab_indicadores.proposals for select to authenticated
  using ((select auth.uid()) = owner_id);

create policy proposals_orchestrator_manage
  on lab_indicadores.proposals
  for all
  to lab_indicadores_orchestrator
  using (true)
  with check (true);

grant select on lab_indicadores.proposals to authenticated;
grant select, insert, update on lab_indicadores.proposals to lab_indicadores_orchestrator;

create or replace function lab_indicadores.enqueue_research(
  p_idempotency_key text,
  p_run_key text default null,
  p_requested_by text default null,
  p_dataset_manifest text default 'hermes-context-absorption-v1',
  p_config jsonb default '{}'::jsonb
)
returns table (run_id uuid, command_id uuid, existing boolean)
language plpgsql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
declare
  v_owner_id uuid := auth.uid();
  v_run_id uuid;
  v_command_id uuid;
begin
  if nullif(trim(p_idempotency_key), '') is null then
    raise exception 'idempotency key is required';
  end if;

  if p_dataset_manifest <> 'hermes-context-absorption-v1' then
    raise exception 'research manifest not allowed: %', p_dataset_manifest;
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
    'research',
    p_dataset_manifest,
    jsonb_build_object(
      'research_id', 'absorption-baseline-v1',
      'execution_profile', 'fixture_proposal',
      'holdout_accessed', false
    ) || (coalesce(p_config, '{}'::jsonb) - 'research_id' - 'execution_profile' - 'holdout_accessed'),
    v_owner_id,
    p_requested_by
  )
  returning id into v_run_id;

  insert into lab_indicadores.commands (
    run_id, command_type, idempotency_key, payload, owner_id, requested_by
  ) values (
    v_run_id,
    'start_research',
    p_idempotency_key,
    jsonb_build_object(
      'run_type', 'research',
      'dataset_manifest', p_dataset_manifest,
      'research_id', 'absorption-baseline-v1',
      'execution_profile', 'fixture_proposal'
    ) || (coalesce(p_config, '{}'::jsonb) - 'research_id' - 'execution_profile' - 'holdout_accessed'),
    v_owner_id,
    p_requested_by
  )
  returning id into v_command_id;

  insert into lab_indicadores.events (run_id, event_type, message, payload)
  values (
    v_run_id,
    'research_queued',
    'Hermes research proposal queued',
    jsonb_build_object('command_id', v_command_id, 'idempotency_key', p_idempotency_key)
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
      and c.command_type in ('start_run', 'start_research')
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

grant execute on function lab_indicadores.enqueue_research(text, text, text, text, jsonb)
  to authenticated, lab_indicadores_orchestrator;
grant execute on function lab_indicadores.claim_next_command(text)
  to lab_indicadores_orchestrator;

create or replace function public.dashboard_list_proposals(p_limit integer default 30)
returns table (
  proposal_id uuid,
  proposal_key text,
  run_id uuid,
  agent_id text,
  status text,
  evidence_level text,
  asset text,
  track text,
  horizon text,
  title text,
  question text,
  mechanism text,
  hypothesis text,
  validation_plan jsonb,
  limitations jsonb,
  source_context_uri text,
  source_context_sha256 text,
  proposal_sha256 text,
  artifact_uri text,
  holdout_accessed boolean,
  reviewer_note text,
  reviewed_at timestamptz,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select
    p.id,
    p.proposal_key,
    p.run_id,
    p.agent_id,
    p.status,
    p.evidence_level,
    p.asset,
    p.track,
    p.horizon,
    p.title,
    p.question,
    p.mechanism,
    p.hypothesis,
    p.validation_plan,
    p.limitations,
    p.source_context_uri,
    p.source_context_sha256,
    p.proposal_sha256,
    p.artifact_uri,
    p.holdout_accessed,
    p.reviewer_note,
    p.reviewed_at,
    p.created_at,
    p.updated_at
  from lab_indicadores.proposals p
  order by p.created_at desc
  limit least(greatest(coalesce(p_limit, 30), 1), 100);
$$;

create or replace function public.dashboard_enqueue_research(
  p_idempotency_key text,
  p_run_key text default null,
  p_requested_by text default 'dashboard',
  p_config jsonb default '{}'::jsonb
)
returns table (run_id uuid, command_id uuid, existing boolean)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select *
  from lab_indicadores.enqueue_research(
    p_idempotency_key,
    p_run_key,
    p_requested_by,
    'hermes-context-absorption-v1',
    coalesce(p_config, '{}'::jsonb)
  );
$$;

revoke execute on function public.dashboard_list_proposals(integer) from public, anon;
revoke execute on function public.dashboard_enqueue_research(text, text, text, jsonb) from public, anon;
grant execute on function public.dashboard_list_proposals(integer) to authenticated;
grant execute on function public.dashboard_enqueue_research(text, text, text, jsonb) to authenticated;
