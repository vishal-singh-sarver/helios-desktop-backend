"""Spectral global data:
  * material_apply.remove_spectral_labels — the senior's remove method, and
  * a writeXML/loadXML PERSISTENCE round-trip ("saved in context").
Driven against a real PyHelios context; skips where the native lib is absent.
"""
from pathlib import Path

import pytest

from app.helios import context as helios_ctx
from app.services import material_apply

pytestmark = pytest.mark.skipif(
    not helios_ctx.PYHELIOS_AVAILABLE,
    reason="PyHelios native library not available in this environment",
)


class _Sctx:
    def __init__(self, ctx):
        self.context = ctx


def _spectral_xml(labels) -> str:
    blocks = [
        f'    <globaldata_vec2 label="{lab}">\n'
        f"        400.0\t0.10\n        401.0\t0.11\n        402.0\t0.12\n"
        f"    </globaldata_vec2>"
        for lab in labels
    ]
    return "<helios>\n" + "\n".join(blocks) + "\n</helios>\n"


def _ctx_with_spectra(tmp_path: Path, labels):
    src = tmp_path / "spectra.xml"
    src.write_text(_spectral_xml(labels))
    ctx = helios_ctx.Context()
    ctx.loadXML(str(src))
    return ctx


def test_remove_spectral_labels(tmp_path):
    ctx = _ctx_with_spectra(tmp_path, ["leaf_refl_0", "leaf_trans_0"])
    sctx = _Sctx(ctx)

    assert material_apply.remove_spectral_labels(sctx, ["leaf_refl_0"]) == 1
    assert not ctx.doesGlobalDataExist("leaf_refl_0")
    assert ctx.doesGlobalDataExist("leaf_trans_0")           # the other stays
    assert material_apply.remove_spectral_labels(sctx, ["nope"]) == 0   # absent -> no-op


def test_spectral_global_data_persists_through_writexml(tmp_path):
    """'saved in context': spectra loaded into a context survive writeXML and
    come back on loadXML into a fresh context."""
    labels = ["leaf_refl_0", "leaf_trans_0"]
    ctx1 = _ctx_with_spectra(tmp_path, labels)
    assert all(ctx1.doesGlobalDataExist(l) for l in labels)

    snap = tmp_path / "context.xml"
    ctx1.writeXML(str(snap))

    ctx2 = helios_ctx.Context()
    ctx2.loadXML(str(snap))
    for l in labels:
        assert ctx2.doesGlobalDataExist(l), f"{l} did not persist through writeXML/loadXML"
