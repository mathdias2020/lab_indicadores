-- Browser gateway for the indicator-lab dashboard.
-- Functions stay SECURITY INVOKER so auth.uid() and lab_indicadores RLS remain
-- the authorization boundary. No service or database credential is exposed.

create or replace function public.dashboard_list_runs(p_limit integer default 50)
returns table (
  run_id uuid,
  run_key text,
  run_type text,
  status text,
  dataset_manifest text,
  requested_by text,
  worker_id text,
  error_message text,
  heartbeat_at timestamptz,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select
    r.id,
    r.run_key,
    r.run_type,
    r.status,
    r.dataset_manifest,
    r.requested_by,
    r.worker_id,
    r.error_message,
    r.heartbeat_at,
    r.started_at,
    r.finished_at,
    r.created_at,
    r.updated_at
  from lab_indicadores.runs r
  order by r.created_at desc
  limit least(greatest(coalesce(p_limit, 50), 1), 100);
$$;

create or replace function public.dashboard_list_run_events(p_run_id uuid)
returns table (
  event_id bigint,
  run_id uuid,
  event_type text,
  message text,
  payload jsonb,
  created_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select e.id, e.run_id, e.event_type, e.message, e.payload, e.created_at
  from lab_indicadores.events e
  where e.run_id = p_run_id
  order by e.created_at asc, e.id asc
  limit 500;
$$;

create or replace function public.dashboard_list_run_artifacts(p_run_id uuid)
returns table (
  artifact_id uuid,
  run_id uuid,
  artifact_type text,
  uri text,
  sha256 text,
  metadata jsonb,
  created_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select a.id, a.run_id, a.artifact_type, a.uri, a.sha256, a.metadata, a.created_at
  from lab_indicadores.artifacts a
  where a.run_id = p_run_id
  order by a.created_at asc, a.id asc
  limit 100;
$$;

create or replace function public.dashboard_list_workers()
returns table (
  worker_id text,
  status text,
  capabilities jsonb,
  version text,
  last_heartbeat_at timestamptz,
  metadata jsonb,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select w.worker_id, w.status, w.capabilities, w.version,
         w.last_heartbeat_at, w.metadata, w.created_at, w.updated_at
  from lab_indicadores.workers w
  order by w.worker_id;
$$;

create or replace function public.dashboard_enqueue_preflight(
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
  from lab_indicadores.enqueue_preflight(
    p_idempotency_key,
    p_run_key,
    p_requested_by,
    'indicator-lab-smoke-v1',
    coalesce(p_config, '{}'::jsonb)
  );
$$;

revoke execute on function public.dashboard_list_runs(integer) from public, anon;
revoke execute on function public.dashboard_list_run_events(uuid) from public, anon;
revoke execute on function public.dashboard_list_run_artifacts(uuid) from public, anon;
revoke execute on function public.dashboard_list_workers() from public, anon;
revoke execute on function public.dashboard_enqueue_preflight(text, text, text, jsonb) from public, anon;

grant execute on function public.dashboard_list_runs(integer) to authenticated;
grant execute on function public.dashboard_list_run_events(uuid) to authenticated;
grant execute on function public.dashboard_list_run_artifacts(uuid) to authenticated;
grant execute on function public.dashboard_list_workers() to authenticated;
grant execute on function public.dashboard_enqueue_preflight(text, text, text, jsonb) to authenticated;
