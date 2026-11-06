"""Platform-neutral feature-check contracts and evidence storage; adapters supply probes and runners."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal


def strict_matching() -> bool:
    value = os.environ.get("STUDIO_QUALIFICATION_STRICT", "").strip().lower()
    if value not in ("", "0", "false", "no", "1", "true", "yes"):
        raise ValueError("STUDIO_QUALIFICATION_STRICT must be a boolean")
    return value in ("1", "true", "yes")


def matches_qualification(recorded: object, current: object, recorded_contract: dict, current_contract: dict) -> bool:
    """Provenance stays exact; only explicit compatibility boundaries invalidate balanced evidence."""
    return recorded_contract == current_contract and (not strict_matching() or recorded == current)


@dataclass(frozen=True)
class MemoryRequirement:
    pool: Literal["host", "device", "unified", "commit"]
    required_mb: int | None
    basis: Literal["measured", "estimated", "policy", "unknown"]
    reserve_mb: int = 0

    def __post_init__(self):
        if self.pool not in ("host", "device", "unified", "commit") or self.basis not in ("measured", "estimated", "policy", "unknown"):
            raise ValueError("invalid memory requirement")
        if self.required_mb is not None and (type(self.required_mb) is not int or self.required_mb < 0):
            raise ValueError("required memory must be a nonnegative MiB count or unknown")
        if type(self.reserve_mb) is not int or self.reserve_mb < 0:
            raise ValueError("the memory reserve must be a nonnegative MiB count")


@dataclass(frozen=True)
class FeatureCheck:
    id: str
    title: str
    scope: str
    requirements: tuple[MemoryRequirement, ...]
    group: str = "other"

    def public(self) -> dict:
        return asdict(self)


class EvidenceStore:
    """The owner chooses a platform/profile-specific root. Passes and attempts remain separate."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, feature: str, record: Literal["pass", "attempt"]) -> Path:
        if not isinstance(feature, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", feature) or record not in ("pass", "attempt"):
            raise ValueError("invalid feature evidence identifier")
        return self.root / f"{feature}{'-attempt' if record == 'attempt' else ''}.json"

    def read(self, feature: str, record: Literal["pass", "attempt"] = "pass") -> dict:
        try:
            value = json.loads(self._path(feature, record).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"the saved {feature} evidence is not an object")
        return value

    def write(self, feature: str, value: dict, record: Literal["pass", "attempt"] = "pass") -> None:
        target = self._path(feature, record)
        if not isinstance(value, dict) or record == "pass" and value.get("ok") is not True:
            raise ValueError("only a successful check can replace saved passing evidence")
        self.root.mkdir(parents=True, exist_ok=True)
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, suffix=".tmp", delete=False) as pending:
                path = Path(pending.name)
                json.dump(value, pending)
            path.replace(target)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
