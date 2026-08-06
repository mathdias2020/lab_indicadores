-- Cover foreign keys used by owner-scoped panel queries and run joins.
create index commands_owner_id_idx
  on lab_indicadores.commands (owner_id);

create index commands_run_id_idx
  on lab_indicadores.commands (run_id);

create index runs_owner_id_idx
  on lab_indicadores.runs (owner_id);
