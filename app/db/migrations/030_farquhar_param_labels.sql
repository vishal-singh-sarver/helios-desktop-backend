-- Migration 030 — FARQUHAR PARAM LABELS: use Helios' own parameter names.
--
-- Story: the material form showed spec-sheet labels ("V cmax25", "dHa, V cmax")
-- that do not match the names Helios uses for the same coefficients. The
-- authoritative names come from the FarquharModelCoefficients setter API, which
-- is what our schema models — each property is one setter argument:
--
--   setVcmax(Vcmax_25, dHa_Vcmax, Topt_Vcmax [, dHd_Vcmax])
--   setJmax (Jmax_25,  dHa_Jmax,  Topt_Jmax,   dHd_Jmax)
--   setTPU  (TPU_25,   dHa_TPU,   Topt_TPU,    dHd_TPU)
--   setRd(Rd25) / setQuantumEfficiency_alpha(alpha)
--   setLightResponseCurvature_theta(theta)
--
-- DISPLAY ONLY: `label` is the rendered name; the property NAME (vcmax25,
-- dha_vcmax, ...) is the storage/API key and is deliberately unchanged, so no
-- stored value moves and no request payload breaks.
--
-- Append-only; idempotent (deterministic UPDATE, CASE falls through to the
-- existing label). Rd25 is listed for completeness — it was already correct.

UPDATE material_property_type
SET label = CASE (SELECT property FROM property_type WHERE id = material_property_type.property_type_id)
    WHEN 'vcmax25'    THEN 'Vcmax_25'
    WHEN 'dha_vcmax'  THEN 'dHa_Vcmax'
    WHEN 'topt_vcmax' THEN 'Topt_Vcmax'
    WHEN 'jmax25'     THEN 'Jmax_25'
    WHEN 'dha_jmax'   THEN 'dHa_Jmax'
    WHEN 'topt_jmax'  THEN 'Topt_Jmax'
    WHEN 'dhd_jmax'   THEN 'dHd_Jmax'
    WHEN 'tpu25'      THEN 'TPU_25'
    WHEN 'dha_tpu'    THEN 'dHa_TPU'
    WHEN 'topt_tpu'   THEN 'Topt_TPU'
    WHEN 'dhd_tpu'    THEN 'dHd_TPU'
    WHEN 'rd25'       THEN 'Rd25'
    WHEN 'alpha'      THEN 'alpha'
    WHEN 'theta'      THEN 'theta'
    ELSE label
END
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (30);
