-- The host-side orchestrator reads the selected Hermes provider/model from the
-- control plane. The table contains no API secret; the secret remains on the
-- VPS. Keep the role read-only and scoped to this laboratory table.
create policy ai_provider_settings_orchestrator_select
  on lab_indicadores.ai_provider_settings for select
  to lab_indicadores_orchestrator
  using (true);
