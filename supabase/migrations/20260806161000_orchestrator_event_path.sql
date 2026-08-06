-- The orchestrator inserts events directly under its RLS policy. The generic
-- invoker RPC is retained for service_role callers, but is not part of the
-- dedicated login role contract.
revoke execute on function lab_indicadores.record_event(uuid, text, text, jsonb)
from lab_indicadores_orchestrator;
