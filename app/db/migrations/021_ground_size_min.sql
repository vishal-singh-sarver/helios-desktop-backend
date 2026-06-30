-- Migration 021 — Ground size minimum raised to 0.01 m (inclusive).
--
-- Story: ground size (length, breadth) must be >= 0.01 m. Migration 020 set the
-- catalog min to 0, and eav_validation._EXCLUSIVE_MIN made the bound exclusive
-- (> 0). This raises the catalog floor to 0.01; dropping length/breadth from
-- _EXCLUSIVE_MIN (in code) makes the bound inclusive, so 0.01 is accepted
-- (>= 0.01) and 0.009 is rejected. Only ground size changes — resolution and
-- position ranges are untouched.
--
-- Idempotent UPDATE; safe to re-run. No schema change.

UPDATE property_type SET min = 0.01 WHERE property IN ('length', 'breadth');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (21);
