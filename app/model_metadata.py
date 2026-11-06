"""Explicit inventory-to-model mappings, independent of platform probes and admission."""
from __future__ import annotations

import model_catalog
import shared_measurements

# These are model needs, not similarly named codecs, adapters, or native binaries.
COMMON = {
    "qwen-image-2.1": "qwen", "qwen-full": "qwen-full", "flux3k-fast": "flux3k-fast",
    "flux3k-full": "flux3k-full", "moss-image": "moss-image",
}
MAC = {**COMMON, "drama3-mlx": "voice", "music3-weights": "music", "ref2va": "ref2va", "fasth3": "video"}
SPARK = {**COMMON, "drama3-checkpoint": "voice", "music3-weights": "music", "ref2va": "ref2va", "fasth3": "video"}


def inventory_info(need_id, platform):
    models = MAC if platform == "macos" else SPARK if platform == "linux" else {}
    model = models.get(need_id)
    if model is None:
        return {}
    if platform == "macos":
        backend, engine = ("torch-metal", "comfyui") if model == "moss-image" else ("mlx", "mlx")
        layout = "comfy" if model == "moss-image" else "mlx"
        if model == "voice":
            engine = "tensorfold"
    else:
        backend, engine = ("native-cuda", "tensorfold") if model == "voice" else ("torch-cuda", "comfyui")
        layout = "native" if model == "voice" else "comfy"
    if model in ("music", "video"):
        # These weights have multiple execution routes; files do not select an engine.
        backend, engine, layout = "unknown", "unknown", "shared-weights"
    spec = model_catalog.model(model)
    return {"model": model, "model_metadata": {"label": spec["label"], "platform": platform,
            "backend": backend, "engine": engine, "layout": layout},
            "shared_measurements": (shared_measurements.references(model, platform=platform, backend=backend, engine=engine)
                                    if backend != "unknown" and engine != "unknown" else {})}
