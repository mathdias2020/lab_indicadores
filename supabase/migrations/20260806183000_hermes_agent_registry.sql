-- Hermes registry for the indicator laboratory.
-- This is intentionally separate from lab_automatizado and does not grant
-- the agent browser or service-role access.

create table lab_indicadores.agents (
  agent_id text primary key,
  agent_type text not null
    check (agent_type in ('hermes', 'worker', 'orchestrator')),
  status text not null default 'offline'
    check (status in ('prepared', 'offline', 'online', 'observing', 'proposing', 'busy', 'degraded', 'error')),
  mode text not null default 'observation'
    check (mode in ('observation', 'proposal', 'research', 'review')),
  profile_id text not null,
  version text,
  capabilities jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  last_heartbeat_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index agents_status_heartbeat_idx
  on lab_indicadores.agents (status, last_heartbeat_at desc);

create trigger agents_touch_updated_at
before update on lab_indicadores.agents
for each row execute function lab_indicadores.touch_updated_at();

alter table lab_indicadores.agents enable row level security;

create policy agents_authenticated_select
  on lab_indicadores.agents for select to authenticated
  using (true);

grant select on lab_indicadores.agents to authenticated;
grant select, insert, update on lab_indicadores.agents to lab_indicadores_orchestrator;

create policy agents_orchestrator_manage
  on lab_indicadores.agents
  for all
  to lab_indicadores_orchestrator
  using (true)
  with check (true);

insert into lab_indicadores.agents (
  agent_id,
  agent_type,
  status,
  mode,
  profile_id,
  version,
  capabilities,
  metadata
)
values (
  'hermes-indicadores',
  'hermes',
  'prepared',
  'observation',
  'lab-indicadores',
  '0.1.0-bootstrap',
  '["read_development_data", "heartbeat_only"]'::jsonb,
  '{
    "execution_enabled": false,
    "holdout_access": false,
    "service_role_access": false,
    "docker_socket_access": false,
    "network_access": false,
    "scope": "canonical/development-only"
  }'::jsonb
)
on conflict (agent_id) do nothing;

comment on table lab_indicadores.agents is
  'Isolated agent registry for the indicator laboratory control room.';

create or replace function public.dashboard_list_agents()
returns table (
  agent_id text,
  agent_type text,
  status text,
  mode text,
  profile_id text,
  version text,
  capabilities jsonb,
  metadata jsonb,
  last_heartbeat_at timestamptz,
  created_at timestamptz,
  updated_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select a.agent_id, a.agent_type, a.status, a.mode, a.profile_id,
         a.version, a.capabilities, a.metadata, a.last_heartbeat_at,
         a.created_at, a.updated_at
  from lab_indicadores.agents a
  order by a.agent_type, a.agent_id;
$$;

revoke execute on function public.dashboard_list_agents() from public, anon;
grant execute on function public.dashboard_list_agents() to authenticated;
