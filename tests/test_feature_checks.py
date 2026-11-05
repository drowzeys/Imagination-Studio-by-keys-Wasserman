"""Shared contracts/storage contain no platform probes, fixed hardware tier or default installation path."""
import pytest

from feature_checks import EvidenceStore, FeatureCheck, MemoryRequirement


def test_memory_contract_distinguishes_unified_and_discrete_budgets_without_a_fixed_tier():
    unified = FeatureCheck("video", "Video", "A measured recipe", (MemoryRequirement("unified", 16000, "measured", 2000),))
    discrete = FeatureCheck("video", "Video", "Another recipe", (MemoryRequirement("host", 8000, "estimated"),
                                                               MemoryRequirement("device", 12000, "measured", 2000)))
    assert unified.public()["requirements"][0]["pool"] == "unified"
    assert [r["pool"] for r in discrete.public()["requirements"]] == ["host", "device"]
    assert MemoryRequirement("unified", None, "unknown").required_mb is None
    with pytest.raises(ValueError):
        MemoryRequirement("device", True, "measured")


def test_passes_and_attempts_are_scoped_by_owner_and_failed_checks_cannot_replace_success(tmp_path):
    for profile in ("windows-cuda", "mac-metal", "spark-cuda"):
        store = EvidenceStore(tmp_path / profile)
        store.write("video", {"ok": True, "fingerprint": {"profile": profile}, "checked_at": "2026-11-04"})
        store.write("video", {"ok": False, "error": "temporarily busy"}, "attempt")
        with pytest.raises(ValueError, match="only a successful"):
            store.write("video", {"ok": False})
        assert store.read("video")["fingerprint"]["profile"] == profile
        assert store.read("video", "attempt")["error"] == "temporarily busy"
    assert EvidenceStore(tmp_path / "another-profile").read("video") == {}


def test_partial_evidence_write_preserves_previous_pass_and_cleans_temporary_file(tmp_path):
    store = EvidenceStore(tmp_path)
    original = {"ok": True, "results": ["measured"]}
    store.write("finish", original)
    with pytest.raises(TypeError):
        store.write("finish", {"ok": True, "not_serializable": object()})
    assert store.read("finish") == original and list(tmp_path.glob("*.tmp")) == []
    with pytest.raises(ValueError):
        store.write("../outside", original)
