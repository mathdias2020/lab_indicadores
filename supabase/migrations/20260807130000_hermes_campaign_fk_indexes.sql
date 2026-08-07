create index if not exists data_profiles_run_id_idx
  on lab_indicadores.data_profiles (run_id);

create index if not exists data_profiles_step_id_idx
  on lab_indicadores.data_profiles (step_id);

create index if not exists proposals_data_profile_id_idx
  on lab_indicadores.proposals (data_profile_id);

create index if not exists proposals_feedback_run_id_idx
  on lab_indicadores.proposals (feedback_run_id);

create index if not exists proposals_parent_proposal_key_idx
  on lab_indicadores.proposals (parent_proposal_key);

create index if not exists research_campaign_steps_parent_step_id_idx
  on lab_indicadores.research_campaign_steps (parent_step_id);

create index if not exists research_campaign_steps_run_id_idx
  on lab_indicadores.research_campaign_steps (run_id);
