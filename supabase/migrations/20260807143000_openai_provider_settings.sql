create table lab_indicadores.ai_provider_settings (
  setting_key text primary key check (setting_key = 'hermes-proposal'),
  provider text not null default 'fixture'
    check (provider in ('fixture', 'openai')),
  model text not null default 'gpt-5.6-sol'
    check (model in ('gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna')),
  reasoning_effort text not null default 'medium'
    check (reasoning_effort in ('none', 'low', 'medium', 'high', 'xhigh', 'max')),
  enabled boolean not null default true,
  api_key_source text not null default 'vps_environment'
    check (api_key_source = 'vps_environment'),
  updated_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into lab_indicadores.ai_provider_settings (setting_key)
values ('hermes-proposal')
on conflict (setting_key) do nothing;

create trigger ai_provider_settings_touch_updated_at
before update on lab_indicadores.ai_provider_settings
for each row execute function lab_indicadores.touch_updated_at();

alter table lab_indicadores.ai_provider_settings enable row level security;

create policy ai_provider_settings_authenticated_select
  on lab_indicadores.ai_provider_settings for select to authenticated
  using (true);

create policy ai_provider_settings_authenticated_update
  on lab_indicadores.ai_provider_settings for update to authenticated
  using (true)
  with check (true);

grant select on lab_indicadores.ai_provider_settings to authenticated, lab_indicadores_orchestrator;
grant update on lab_indicadores.ai_provider_settings to authenticated;

create or replace function public.dashboard_get_ai_settings()
returns table (
  setting_key text,
  provider text,
  model text,
  reasoning_effort text,
  enabled boolean,
  api_key_source text,
  updated_at timestamptz
)
language sql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
  select setting_key, provider, model, reasoning_effort, enabled, api_key_source, updated_at
  from lab_indicadores.ai_provider_settings
  where setting_key = 'hermes-proposal';
$$;

grant execute on function public.dashboard_get_ai_settings() to authenticated;

create or replace function public.dashboard_update_ai_settings(
  p_provider text,
  p_model text,
  p_reasoning_effort text
)
returns table (
  setting_key text,
  provider text,
  model text,
  reasoning_effort text,
  enabled boolean,
  api_key_source text,
  updated_at timestamptz
)
language plpgsql
security invoker
set search_path = lab_indicadores, pg_catalog
as $$
begin
  if auth.uid() is null then
    raise exception 'authentication is required';
  end if;
  if lower(trim(p_provider)) not in ('fixture', 'openai') then
    raise exception 'provider is not allowed';
  end if;
  if lower(trim(p_model)) not in ('gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna') then
    raise exception 'model is not allowed';
  end if;
  if lower(trim(p_reasoning_effort)) not in ('none', 'low', 'medium', 'high', 'xhigh', 'max') then
    raise exception 'reasoning effort is not allowed';
  end if;

  update lab_indicadores.ai_provider_settings
  set provider = lower(trim(p_provider)),
      model = lower(trim(p_model)),
      reasoning_effort = lower(trim(p_reasoning_effort)),
      updated_by = auth.uid(),
      updated_at = clock_timestamp()
  where setting_key = 'hermes-proposal';

  return query
  select s.setting_key, s.provider, s.model, s.reasoning_effort,
         s.enabled, s.api_key_source, s.updated_at
  from lab_indicadores.ai_provider_settings s
  where s.setting_key = 'hermes-proposal';
end;
$$;

grant execute on function public.dashboard_update_ai_settings(text, text, text) to authenticated;
