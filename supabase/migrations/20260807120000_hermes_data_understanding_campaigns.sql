-- Hermes data-understanding campaign contract for lab_indicadores.
-- The campaign is an auditable sequence: profile development Parquet ->
-- proposal -> human gate -> deterministic analysis -> optional error review.
-- Holdout access is structurally forbidden in every new record.

create table lab_indicadores.research_campaigns (
  id uuid primary key default gen_random_uuid(),
  campaign_key text not null unique,
  owner_id uuid references auth.users(id) on delete set null,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'awaiting_review', 'failed', 'completed', 'cancelled')),
  stage text not null default 'data_profile'
    check (stage in ('data_profile', 'hypothesis', 'analysis', 'error_review', 'gate', 'completed')),
  asset text not null check (asset in ('WDO', 'WIN')),
  track text not null check (track in ('flow', 'price', 'flow_price')),
  horizon text not null check (horizon in ('scalping', 'tactical_intraday', 'broad_intraday')),
  objective text not null,
  config jsonb not null default '{}'::jsonb,
  iteration integer not null default 0 check (iteration >= 0),
  max_iterations integer not null default 3 check (max_iterations between 1 and 10),
  holdout_accessed boolean not null default false check (holdout_accessed = false),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table lab_indicadores.research_campaign_steps (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references lab_indicadores.research_campaigns(id) on delete cascade,
  step_key text not null,
  stage text not null
    check (stage in ('data_profile', 'hypothesis', 'analysis', 'error_review', 'gate')),
  sequence_no integer not null check (sequence_no >= 1),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed', 'awaiting_review', 'skipped')),
  run_id uuid references lab_indicadores.runs(id) on delete set null,
  parent_step_id uuid references lab_indicadores.research_campaign_steps(id) on delete set null,
  input jsonb not null default '{}'::jsonb,
  output jsonb not null default '{}'::jsonb,
  error_payload jsonb not null default '{}'::jsonb,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (campaign_id, step_key)
);

create table lab_indicadores.data_profiles (
  id uuid primary key default gen_random_uuid(),
  profile_key text not null unique,
  campaign_id uuid not null references lab_indicadores.research_campaigns(id) on delete cascade,
  run_id uuid not null references lab_indicadores.runs(id) on delete cascade,
  step_id uuid references lab_indicadores.research_campaign_steps(id) on delete set null,
  profile_version text not null,
  profile_context_id text not null,
  asset text not null check (asset in ('WDO', 'WIN')),
  dataset_manifest text not null,
  profile jsonb not null,
  profile_sha256 text not null check (profile_sha256 ~ '^[0-9a-f]{64}$'),
  artifact_uri text not null,
  holdout_accessed boolean not null default false check (holdout_accessed = false),
  created_at timestamptz not null default now()
);

alter table lab_indicadores.runs
  add column if not exists campaign_id uuid references lab_indicadores.research_campaigns(id) on delete set null;

alter table lab_indicadores.runs
  drop constraint if exists runs_run_type_check;
alter table lab_indicadores.runs
  add constraint runs_run_type_check
  check (run_type in ('preflight', 'research', 'analysis', 'data_profile'));

alter table lab_indicadores.commands
  drop constraint if exists commands_command_type_check;
alter table lab_indicadores.commands
  add constraint commands_command_type_check
  check (command_type in ('start_run', 'start_research', 'start_analysis', 'start_data_profile', 'cancel_run'));

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

alter table lab_indicadores.proposals
  add column if not exists campaign_id uuid references lab_indicadores.research_campaigns(id) on delete set null,
  add column if not exists parent_proposal_key text references lab_indicadores.proposals(proposal_key) on delete set null,
  add column if not exists revision_no integer not null default 1,
  add column if not exists change_kind text not null default 'initial',
  add column if not exists change_reason text,
  add column if not exists feedback_run_id uuid references lab_indicadores.runs(id) on delete set null,
  add column if not exists data_profile_id uuid references lab_indicadores.data_profiles(id) on delete set null,
  add column if not exists data_profile_sha256 text;

alter table lab_indicadores.proposals
  drop constraint if exists proposals_revision_no_check;
alter table lab_indicadores.proposals
  add constraint proposals_revision_no_check check (revision_no >= 1);
alter table lab_indicadores.proposals
  drop constraint if exists proposals_change_kind_check;
alter table lab_indicadores.proposals
  add constraint proposals_change_kind_check
  check (change_kind in ('initial', 'error_review', 'human_revision'));
alter table lab_indicadores.proposals
  drop constraint if exists proposals_data_profile_sha256_check;
alter table lab_indicadores.proposals
  add constraint proposals_data_profile_sha256_check
  check (data_profile_sha256 is null or data_profile_sha256 ~ '^[0-9a-f]{64}$');

create index if not exists research_campaigns_owner_created_idx
  on lab_indicadores.research_campaigns (owner_id, created_at desc);
create index if not exists research_campaigns_status_stage_idx
  on lab_indicadores.research_campaigns (status, stage, updated_at desc);
create index if not exists research_campaign_steps_campaign_sequence_idx
  on lab_indicadores.research_campaign_steps (campaign_id, sequence_no);
create index if not exists data_profiles_campaign_created_idx
  on lab_indicadores.data_profiles (campaign_id, created_at desc);
create index if not exists runs_campaign_created_idx
  on lab_indicadores.runs (campaign_id, created_at desc);
create index if not exists proposals_campaign_created_idx
  on lab_indicadores.proposals (campaign_id, created_at desc);

create trigger research_campaigns_touch_updated_at
before update on lab_indicadores.research_campaigns
for each row execute function lab_indicadores.touch_updated_at();

create trigger research_campaign_steps_touch_updated_at
before update on lab_indicadores.research_campaign_steps
for each row execute function lab_indicadores.touch_updated_at();

alter table lab_indicadores.research_campaigns enable row level security;
alter table lab_indicadores.research_campaign_steps enable row level security;
alter table lab_indicadores.data_profiles enable row level security;

create policy research_campaigns_owner_select
  on lab_indicadores.research_campaigns for select to authenticated
  using ((select auth.uid()) = owner_id);
create policy research_campaigns_owner_insert
  on lab_indicadores.research_campaigns for insert to authenticated
  with check ((select auth.uid()) = owner_id and holdout_accessed = false);
create policy research_campaigns_orchestrator_manage
  on lab_indicadores.research_campaigns for all to lab_indicadores_orchestrator
  using (true) with check (true);

create policy research_campaign_steps_owner_select
  on lab_indicadores.research_campaign_steps for select to authenticated
  using (exists (
    select 1 from lab_indicadores.research_campaigns c
    where c.id = campaign_id and c.owner_id = (select auth.uid())
  ));
create policy research_campaign_steps_owner_insert
  on lab_indicadores.research_campaign_steps for insert to authenticated
  with check (exists (
    select 1 from lab_indicadores.research_campaigns c
    where c.id = campaign_id and c.owner_id = (select auth.uid())
  ));
create policy research_campaign_steps_orchestrator_manage
  on lab_indicadores.research_campaign_steps for all to lab_indicadores_orchestrator
  using (true) with check (true);

create policy data_profiles_owner_select
  on lab_indicadores.data_profiles for select to authenticated
  using (exists (
    select 1 from lab_indicadores.research_campaigns c
    where c.id = campaign_id and c.owner_id = (select auth.uid())
  ));
create policy data_profiles_orchestrator_manage
  on lab_indicadores.data_profiles for all to lab_indicadores_orchestrator
  using (true) with check (true);

grant select, insert on lab_indicadores.research_campaigns to authenticated;
grant select, insert on lab_indicadores.research_campaign_steps to authenticated;
grant select on lab_indicadores.data_profiles to authenticated;
grant select, insert, update on
  lab_indicadores.research_campaigns,
  lab_indicadores.research_campaign_steps,
  lab_indicadores.data_profiles
to lab_indicadores_orchestrator;

create or replace function public.dashboard_enqueue_campaign(
  p_idempotency_key text,
  p_campaign_key text,
  p_asset text,
  p_track text default 'flow_price',
  p_horizon text default 'tactical_intraday',
  p_objective text default 'Entender os dados brutos antes de propor e testar um indicador.',
  p_config jsonb default '{}'::jsonb
)
returns table (campaign_id uuid, run_id uuid, command_id uuid, existing boolean)
language plpgsql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
declare
  v_owner_id uuid := auth.uid();
  v_campaign_id uuid;
  v_run_id uuid;
  v_command_id uuid;
  v_asset text := upper(trim(p_asset));
  v_track text := lower(trim(p_track));
  v_horizon text := lower(trim(p_horizon));
  v_context_id text;
  v_manifest text;
  v_config jsonb := coalesce(p_config, '{}'::jsonb);
begin
  if nullif(trim(p_idempotency_key), '') is null then raise exception 'idempotency key is required'; end if;
  if nullif(trim(p_campaign_key), '') is null then raise exception 'campaign key is required'; end if;
  if v_asset not in ('WDO', 'WIN') then raise exception 'campaign asset is not allowed'; end if;
  if v_track not in ('flow', 'price', 'flow_price') then raise exception 'campaign track is not allowed'; end if;
  if v_horizon not in ('scalping', 'tactical_intraday', 'broad_intraday') then raise exception 'campaign horizon is not allowed'; end if;

  v_context_id := case when v_asset = 'WIN'
    then 'absorption-descriptive-multi-period-win-v1'
    else 'absorption-descriptive-multi-period-wdo-v1' end;
  v_manifest := case when v_asset = 'WIN'
    then 'hermes-analysis-absorption-multi-period-win-v1'
    else 'hermes-analysis-absorption-multi-period-wdo-v1' end;

  select c.run_id, c.id, r.campaign_id
    into v_run_id, v_command_id, v_campaign_id
  from lab_indicadores.commands c
  left join lab_indicadores.runs r on r.id = c.run_id
  where c.idempotency_key = p_idempotency_key;
  if found then return query select v_campaign_id, v_run_id, v_command_id, true; return; end if;

  insert into lab_indicadores.research_campaigns (
    campaign_key, owner_id, status, stage, asset, track, horizon, objective,
    config, max_iterations, holdout_accessed
  ) values (
    p_campaign_key, v_owner_id, 'queued', 'data_profile', v_asset, v_track, v_horizon,
    left(trim(p_objective), 2000), v_config || jsonb_build_object(
      'profile_context_id', v_context_id,
      'dataset_manifest', v_manifest,
      'holdout_accessed', false
    ),
    least(greatest(coalesce((v_config->>'max_iterations')::integer, 3), 1), 10), false
  ) returning id into v_campaign_id;

  insert into lab_indicadores.runs (
    run_key, run_type, status, dataset_manifest, config, owner_id, requested_by, campaign_id
  ) values (
    p_campaign_key || ':data-profile', 'data_profile', 'queued', v_manifest,
    jsonb_build_object(
      'campaign_id', v_campaign_id,
      'stage', 'data_profile',
      'profile_context_id', v_context_id,
      'asset', v_asset,
      'track', v_track,
      'horizon', v_horizon,
      'holdout_accessed', false
    ), v_owner_id, 'dashboard', v_campaign_id
  ) returning id into v_run_id;

  insert into lab_indicadores.commands (
    run_id, command_type, idempotency_key, payload, owner_id, requested_by
  ) values (
    v_run_id, 'start_data_profile', p_idempotency_key,
    jsonb_build_object(
      'campaign_id', v_campaign_id,
      'profile_context_id', v_context_id,
      'asset', v_asset,
      'track', v_track,
      'horizon', v_horizon,
      'dataset_manifest', v_manifest,
      'holdout_accessed', false
    ), v_owner_id, 'dashboard'
  ) returning id into v_command_id;

  insert into lab_indicadores.research_campaign_steps (
    campaign_id, step_key, stage, sequence_no, status, run_id,
    input
  ) values (
    v_campaign_id, 'data-profile-1', 'data_profile', 1, 'queued', v_run_id,
    jsonb_build_object('profile_context_id', v_context_id, 'dataset_manifest', v_manifest, 'holdout_accessed', false)
  );

  insert into lab_indicadores.events (run_id, event_type, message, payload)
  values (
    v_run_id, 'campaign_queued', 'Campaign queued for Hermes data understanding',
    jsonb_build_object('campaign_id', v_campaign_id, 'command_id', v_command_id, 'stage', 'data_profile')
  );

  return query select v_campaign_id, v_run_id, v_command_id, false;
end;
$$;

create or replace function public.dashboard_list_campaigns(p_limit integer default 30)
returns table (
  campaign_id uuid,
  campaign_key text,
  status text,
  stage text,
  asset text,
  track text,
  horizon text,
  objective text,
  iteration integer,
  max_iterations integer,
  holdout_accessed boolean,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select c.id, c.campaign_key, c.status, c.stage, c.asset, c.track, c.horizon,
         c.objective, c.iteration, c.max_iterations, c.holdout_accessed,
         c.created_at, c.updated_at
  from lab_indicadores.research_campaigns c
  order by c.created_at desc
  limit least(greatest(coalesce(p_limit, 30), 1), 100);
$$;

create or replace function public.dashboard_list_data_profiles(p_campaign_id uuid)
returns table (
  profile_id uuid,
  profile_key text,
  campaign_id uuid,
  run_id uuid,
  profile_context_id text,
  asset text,
  dataset_manifest text,
  profile jsonb,
  profile_sha256 text,
  artifact_uri text,
  holdout_accessed boolean,
  created_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select p.id, p.profile_key, p.campaign_id, p.run_id, p.profile_context_id,
         p.asset, p.dataset_manifest, p.profile, p.profile_sha256,
         p.artifact_uri, p.holdout_accessed, p.created_at
  from lab_indicadores.data_profiles p
  where p.campaign_id = p_campaign_id
  order by p.created_at desc
  limit 20;
$$;

revoke execute on function public.dashboard_enqueue_campaign(text, text, text, text, text, text, jsonb) from public, anon;
grant execute on function public.dashboard_enqueue_campaign(text, text, text, text, text, text, jsonb) to authenticated;
revoke execute on function public.dashboard_list_campaigns(integer) from public, anon;
grant execute on function public.dashboard_list_campaigns(integer) to authenticated;
revoke execute on function public.dashboard_list_data_profiles(uuid) from public, anon;
grant execute on function public.dashboard_list_data_profiles(uuid) to authenticated;

comment on table lab_indicadores.research_campaigns is
  'Auditable Hermes campaign: development data profile before hypothesis, with bounded error-review iterations.';
comment on table lab_indicadores.data_profiles is
  'Deterministic, hashable observations of declared development Parquets; no raw trades are copied to Supabase.';
