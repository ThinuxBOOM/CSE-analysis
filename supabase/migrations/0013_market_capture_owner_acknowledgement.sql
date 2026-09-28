-- =============================================================================
-- Migration 0013: G-1 block acknowledgement is an OWNER action (P2 review fix)
-- =============================================================================
-- 0012 granted INSERT on market_capture_block_acknowledgements to cse_worker, so
-- the capture worker could acknowledge its own CSE block - contradicting G-1
-- (automated capture stops pending OWNER review). 0012 stays exactly as
-- committed (the migration runner pins every applied file's hash); this
-- migration corrects it additively:
--
--   1. cse_worker loses INSERT on market_capture_block_acknowledgements. It
--      keeps SELECT: the capture gate and the run-state trigger (blocked ->
--      running only after an acknowledgement) read the table as the worker.
--   2. The acknowledgement guard additionally refuses any session that LOGGED
--      IN as a service or read role (cse_worker, cse_backup, cse_reader),
--      whatever privileges it holds - defence in depth should INSERT ever be
--      granted again by mistake. session_user is the authenticated login role
--      and cannot be changed by SET ROLE.
--
-- The owner path needs no new role, grant or SECURITY DEFINER function: the
-- table's owner, cse_owner, inserts. Operationally (ops/bin/cse-capture
-- acknowledge-block, root via sudo only): OS user cse-migrator ->
-- peer-authenticated cse_migrator -> SET LOCAL ROLE cse_owner for exactly one
-- INSERT in one transaction - P1's owner-delegation path, which only root can
-- reach and no service role can use (neither cse_worker nor cse_backup is a
-- member of cse_owner). Service roles never become owner or superuser.
--
-- Additive: one REVOKE and a replaced trigger-function body (same name,
-- signature and trigger). Run AFTER 0012.
-- =============================================================================

revoke insert on market_capture_block_acknowledgements from cse_worker;

create or replace function market_capture_block_ack_guard() returns trigger
language plpgsql as $$
begin
  if session_user in ('cse_worker', 'cse_backup', 'cse_reader') then
    raise exception 'G-1: acknowledging a blocked capture run is an owner action; % may not record it', session_user
      using errcode = 'insufficient_privilege';
  end if;
  if coalesce((select e.state from market_capture_run_events e where e.run_id = new.run_id
                order by e.seq desc limit 1), '') <> 'blocked' then
    raise exception 'run % is not blocked; nothing to acknowledge', new.run_id using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

revoke all on function market_capture_block_ack_guard() from public;

comment on table market_capture_block_acknowledgements is
  'G-1: after CSE blocks or rate-limits a run, no further capture starts until the OWNER records a review here. '
  'Inserted only through the owner path (cse_migrator -> SET LOCAL ROLE cse_owner, root via sudo); the capture '
  'worker can read but never insert (0013), and service-role sessions are refused by the guard trigger.';
