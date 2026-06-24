-- Migration 020 — validation range corrections (Ground size + position).
--
-- Story "create a ground geometry":
--   * Ground size (length, breadth): > 0 (exclusive), max 1,000,000 m. The 017
--     seed used min 0 / no max. This sets min 0, max 1,000,000; the EXCLUSIVE
--     > 0 lower bound is completed in code by listing length/breadth in
--     eav_validation._EXCLUSIVE_MIN (so 0 is rejected but 0.5 is accepted).
--   * Position (position_x/y/z): inclusive [-1,000,000, +1,000,000]. The 017
--     seed left them unbounded (NULL). These are NOT in _EXCLUSIVE_MIN, so the
--     catalog min/max alone enforce the inclusive range.
--
-- Idempotent UPDATEs; safe to re-run. No schema change.

UPDATE property_type SET min = 0, max = 1000000 WHERE property IN ('length', 'breadth');
UPDATE property_type SET min = -1000000, max = 1000000
    WHERE property IN ('position_x', 'position_y', 'position_z');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (20);
