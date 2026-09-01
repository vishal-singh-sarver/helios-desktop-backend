-- Migration 028 — HEAT TRANSFER FLAG: boolean -> enum (One Sided / Two Sided).
--
-- Story: the "Two-Sided Heat Transfer Flag" renders as a two-option DROPDOWN
-- labelled "Heat Transfer Flag" (spec sheet + M2 mockup), not a raw checkbox.
-- Model it as an enum {One Sided, Two Sided}, shared across every material type
-- that carries it (Radiation, Energy Balance, Photosynthesis, Boundary Layer
-- Conductance) — one property_type row, so the datatype flips everywhere at once.
--
-- The Helios engine still keeps its numeric 0/1 twosided-flag convention, so
-- material_apply maps the enum token back to a UInt on write (One Sided -> 0,
-- Two Sided -> 1): the stored/UI value is the token, the engine value stays 0/1.
-- Client write contract changes accordingly — the flag is now sent as the enum
-- string, not a boolean.
--
-- Append-only; idempotent (deterministic UPDATEs; enum datatype is already seeded).

UPDATE property_type
SET datatype_id = (SELECT id FROM datatype WHERE name = 'enum'),
    enum_values = '["One Sided", "Two Sided"]'
WHERE property = 'two_sided_heat_transfer';

-- Label "Heat Transfer Flag" on every material type that carries the flag
-- (supersedes the Photosynthesis-only label set in migration 027).
UPDATE material_property_type
SET label = 'Heat Transfer Flag'
WHERE property_type_id = (SELECT id FROM property_type WHERE property = 'two_sided_heat_transfer');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (28);
