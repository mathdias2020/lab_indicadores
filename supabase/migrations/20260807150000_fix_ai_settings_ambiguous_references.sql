-- Fix ambiguous setting_key references in the Hermes settings RPCs.
-- The RETURNS TABLE output column named setting_key is also visible as a
-- PL/pgSQL/SQL identifier, so table columns must be explicitly qualified.

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
  select s.setting_key, s.provider, s.model, s.reasoning_effort,
         s.enabled, s.api_key_source, s.updated_at
  from lab_indicadores.ai_provider_settings as s
  where s.setting_key = 'hermes-proposal';
$$;

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

  update lab_indicadores.ai_provider_settings as s
  set provider = lower(trim(p_provider)),
      model = lower(trim(p_model)),
      reasoning_effort = lower(trim(p_reasoning_effort)),
      updated_by = auth.uid(),
      updated_at = clock_timestamp()
  where s.setting_key = 'hermes-proposal';

  return query
  select s.setting_key, s.provider, s.model, s.reasoning_effort,
         s.enabled, s.api_key_source, s.updated_at
  from lab_indicadores.ai_provider_settings as s
  where s.setting_key = 'hermes-proposal';
end;
$$;
