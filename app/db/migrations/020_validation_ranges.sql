-- Migration 020 — validation range corrections (Ground size).
--
-- Story "create a ground geometry": Ground size (length, breadth) must be
-- >= 1 (inclusive) with a maximum of 1,000,000 m. The original 017 seed used
-- min 0 with no max (and the service treated length/breadth as an exclusive
-- > 0 bound). This sets the catalog bounds; the inclusive >= 1 is completed in
-- code by removing length/breadth from eav_validation._EXCLUSIVE_MIN.
--
-- Idempotent UPDATE; safe to re-run. No schema change.

UPDATE property_type SET min = 1, max = 1000000 WHERE property IN ('length', 'breadth');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (20);
