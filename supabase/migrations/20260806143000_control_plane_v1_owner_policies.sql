-- Complements control_plane_v1 with owner-scoped inserts used by the
-- authenticated enqueue RPC. Worker claims remain service-role only.

create policy runs_owner_insert
  on lab_indicadores.runs
  for insert
  to authenticated
  with check ((select auth.uid()) = owner_id);

create policy events_owner_insert
  on lab_indicadores.events
  for insert
  to authenticated
  with check (
    exists (
      select 1
      from lab_indicadores.runs r
      where r.id = run_id
        and r.owner_id = (select auth.uid())
    )
  );

grant insert on lab_indicadores.runs to authenticated;
grant insert on lab_indicadores.events to authenticated;
