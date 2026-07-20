-- Migration 026 — topt_tpu minimum corrected to 273 K.
--
-- Story: the Photosynthesis catalog value for "T opt, TPU" is 273-373 K. The
-- 017 seed wrote min = 272 — a digit typo, out of step with its siblings
-- topt_vcmax / topt_jmax (both 273) and every other Kelvin bound in the
-- catalog. The Photosynthesis mapping passes NULL min/max overrides, so the
-- property_type row is the effective bound and the only thing to correct.
--
-- Numbered 026, not 024: migrations 024/025 (visualiser material type) are
-- in flight on feature/material-type-response and are not on M2 yet.
--
-- Idempotent UPDATE; safe to re-run. No schema change.

UPDATE property_type SET min = 273 WHERE property = 'topt_tpu';

INSERT OR IGNORE INTO schema_migrations(version) VALUES (26);
