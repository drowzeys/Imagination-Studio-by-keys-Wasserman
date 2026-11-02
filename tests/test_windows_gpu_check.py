"""Reference validation without importing PyTorch or probing hardware."""
import importlib.util
import math
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_gpu_check", ROOT / "scripts" / "windows-gpu-check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_reference_accepts_expected_results():
    check.check_values("sum", [2.0, 2.0001], [2.0, 2.0])


@pytest.mark.parametrize("actual,expected", [
    ([2.0], [2.0, 2.0]),
    ([3.0], [2.0]),
    ([math.nan], [2.0]),
    ([math.inf], [2.0]),
])
def test_reference_rejects_wrong_or_nonfinite_results(actual, expected):
    with pytest.raises(RuntimeError):
        check.check_values("sum", actual, expected)


def test_probe_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["windows-gpu-check.py"])

    def missing(_index):
        raise RuntimeError("CUDA unavailable")

    monkeypatch.setattr(check, "probe", missing)
    assert check.main() == 1
    assert '"ok": false' in capsys.readouterr().err


def test_cpu_only_torch_does_not_pass(monkeypatch):
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    for name, module in (("torch", torch), ("torchvision", SimpleNamespace()), ("torchaudio", SimpleNamespace())):
        monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(RuntimeError, match="CPU-only"):
        check.probe(0)


def test_invalid_device_does_not_fall_back(monkeypatch):
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, device_count=lambda: 1))
    for name, module in (("torch", torch), ("torchvision", SimpleNamespace()), ("torchaudio", SimpleNamespace())):
        monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(ValueError, match="index"):
        check.probe(1)
