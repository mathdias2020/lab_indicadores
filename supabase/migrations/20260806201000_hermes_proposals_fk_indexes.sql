create index if not exists proposals_run_id_idx
  on lab_indicadores.proposals (run_id);

create index if not exists proposals_agent_id_idx
  on lab_indicadores.proposals (agent_id);
