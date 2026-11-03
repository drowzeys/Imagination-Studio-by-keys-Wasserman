#!/usr/bin/env python3
"""Explicit, bounded Stable Audio Open calibration. Does not enable Studio rendering."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import windows_sfx  # noqa: E402

SECONDS, SEED = 5, 7
PROMPT = "Soft steady rain falling on a metal roof."
EXPECTED_SAMPLES = round((SECONDS * 44100 / 2048) / 2) * 2 * 2048


def checker():
    spec = importlib.util.spec_from_file_location("sfx_comfy_check", ROOT / "scripts" / "windows-comfy-check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow() -> dict:
    sys.path.insert(0, str(ROOT / "spark"))
    import spark_generate

    result = spark_generate.sfx_workflow(PROMPT, "", SECONDS, SEED, prefix="windows_calibration/sfx")
    result["1"]["class_type"] = "WindowsCalibrationCheckpointLoader"
    result["19"] = {"class_type": "WindowsCalibrationReceipt", "inputs": {"audio": ["18", 0]}}
    return result


def prepare(directory: Path, models: Path):
    for relative, name in (
        ("models/checkpoints/stable_audio_open_1.0.safetensors", "model.safetensors"),
        ("models/text_encoders/stable_audio_open_t5_base.safetensors", "text_encoder/model.safetensors"),
    ):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(models / name, target)
    node = directory / "custom_nodes" / "windows_calibration" / "__init__.py"
    node.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "scripts" / "windows-calibration-node.py", node)


def post_prompt(base: str, graph: dict) -> dict:
    data = json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode()
    request = urllib.request.Request(base + "/prompt", data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read(1 << 20))


def validate_audio(path: Path) -> dict:
    import av
    import numpy as np

    samples, values, squares, peak = 0, 0, 0.0, 0.0
    with av.open(str(path)) as container:
        if len(container.streams.audio) != 1:
            raise RuntimeError("calibration must contain one audio stream")
        stream = container.streams.audio[0]
        if stream.codec_context.sample_rate != 44100 or len(stream.codec_context.layout.channels) != 2:
            raise RuntimeError("calibration audio must be 44.1 kHz stereo")
        for frame in container.decode(stream):
            array = frame.to_ndarray()
            if array.dtype.kind not in ("i", "f") or not array.size:
                raise RuntimeError("calibration audio has an invalid sample representation")
            scale = float(1 << (array.dtype.itemsize * 8 - 1)) if array.dtype.kind == "i" else 1.0
            audio = array.astype(np.float64) / scale
            if not np.isfinite(audio).all():
                raise RuntimeError("calibration audio contains nonfinite samples")
            samples += frame.samples
            values += audio.size
            squares += float(np.square(audio).sum())
            peak = max(peak, float(np.abs(audio).max()))
    if not values or abs(samples - EXPECTED_SAMPLES) > 2048:
        raise RuntimeError("calibration audio has the wrong length for the model's latent window")
    rms = math.sqrt(squares / values)
    if rms < 1e-6 or peak > 10 ** (-1 / 20):
        raise RuntimeError("calibration audio is silent or exceeds -1 dBFS")
    return {"samples_per_channel": samples, "seconds": samples / 44100, "sample_rate": 44100,
            "channels": 2, "peak_dbfs": 20 * math.log10(peak), "rms": rms}


def validate_receipt(receipt: dict, limit_bytes: int):
    allocated, reserved = receipt.get("peak_tensor_bytes"), receipt.get("peak_reserved_bytes")
    total, fraction = receipt.get("gpu_total_bytes"), receipt.get("gpu_fraction")
    if receipt.get("allocator") != "native" or type(allocated) is not int or type(reserved) is not int:
        raise RuntimeError("calibration did not return native-allocator peak counters")
    if not 0 < allocated <= reserved <= limit_bytes:
        raise RuntimeError("calibration allocator peaks are invalid or exceed the cap")
    if type(total) is not int or total <= 0 or type(fraction) not in (int, float) or not math.isfinite(fraction):
        raise RuntimeError("calibration did not report a valid allocation limit")
    if not math.isclose(total * fraction, limit_bytes, rel_tol=1e-9):
        raise RuntimeError("the calibration allocation cap was not applied")


def validate_queue(queue: dict, prompt_id: str) -> bool:
    if not isinstance(queue, dict):
        raise RuntimeError("invalid calibration queue")
    active = False
    for key in ("queue_running", "queue_pending"):
        if not isinstance(queue.get(key), list):
            raise RuntimeError("invalid calibration queue")
        for entry in queue[key]:
            if not isinstance(entry, list) or len(entry) < 2 or entry[1] != prompt_id:
                raise RuntimeError("another workflow appeared in the isolated calibration queue")
            active = True
    return active


def sampling_progress(message: dict, prompt_id: str) -> dict | None:
    if not isinstance(message, dict) or message.get("type") != "progress":
        return None
    data = message.get("data")
    if not isinstance(data, dict) or data.get("prompt_id") != prompt_id or data.get("node") != "12":
        return None
    value, maximum = data.get("value"), data.get("max")
    if type(value) not in (int, float) or type(maximum) not in (int, float):
        raise RuntimeError("invalid sampling progress")
    if not math.isfinite(value) or not math.isfinite(maximum) or not 0 <= value <= maximum or maximum <= 0:
        raise RuntimeError("invalid sampling progress")
    return data if value > 0 else None


def validate_cancelled(record: dict, prompt_id: str):
    if not isinstance(record, dict) or not isinstance(record.get("status"), dict):
        raise RuntimeError("invalid cancellation history")
    status = record["status"]
    if not isinstance(status.get("messages"), list):
        raise RuntimeError("invalid cancellation messages")
    interrupted = any(
        isinstance(item, list) and len(item) == 2 and item[0] == "execution_interrupted"
        and isinstance(item[1], dict) and item[1].get("prompt_id") == prompt_id
        and item[1].get("node_id") == "12" and item[1].get("node_type") == "KSampler"
        for item in status["messages"]
    )
    if status.get("status_str") == "success" or not interrupted:
        raise RuntimeError("the targeted workflow was not confirmed interrupted")
    outputs = record.get("outputs", {})
    if not isinstance(outputs, dict):
        raise RuntimeError("invalid cancellation outputs")
    saved = outputs.get("18", {})
    if not isinstance(saved, dict):
        raise RuntimeError("invalid cancellation save output")
    if any(isinstance(item, dict) and item.get("filename") for values in saved.values()
           for item in (values if isinstance(values, list) else [])):
        raise RuntimeError("audio was saved before the cancellation test completed")


async def cancel_sampling(check, base: str, process, monitor, graph: dict) -> dict:
    import aiohttp

    client_id = uuid.uuid4().hex
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3), trust_env=False) as session:
        async with session.ws_connect(base + "/ws", params={"clientId": client_id},
                                      timeout=aiohttp.ClientWSTimeout(ws_close=1), max_msg_size=1 << 20) as socket:
            async with session.post(base + "/prompt", json={"prompt": graph, "client_id": client_id}) as response:
                response.raise_for_status()
                reply = await response.json()
            if reply.get("error") or reply.get("node_errors"):
                raise RuntimeError("ComfyUI rejected the cancellation graph")
            prompt_id = str(uuid.UUID(reply["prompt_id"]))
            started = time.monotonic()
            deadline, next_poll, trigger = started + 300, started, None
            while True:
                monitor.check()
                if process.poll() is not None:
                    raise RuntimeError("the cancellation server exited")
                now = time.monotonic()
                if now >= deadline:
                    raise RuntimeError("the cancellation test exceeded its deadline")
                if now >= next_poll:
                    async with session.get(base + "/queue") as response:
                        response.raise_for_status()
                        active = validate_queue(await response.json(), prompt_id)
                    async with session.get(base + "/history/" + prompt_id) as response:
                        response.raise_for_status()
                        history = await response.json()
                    if prompt_id in history:
                        if trigger is None:
                            raise RuntimeError("the workflow finished before the sampling cancellation")
                        validate_cancelled(history[prompt_id], prompt_id)
                        if not active:
                            break
                    next_poll = time.monotonic() + 0.5
                if trigger is None:
                    try:
                        message = await socket.receive(timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise RuntimeError("sampling progress connection closed before cancellation")
                    if message.type == aiohttp.WSMsgType.TEXT:
                        progress = sampling_progress(json.loads(message.data), prompt_id)
                        if progress:
                            cancel_started = time.monotonic()
                            async with session.post(base + "/api/jobs/" + prompt_id + "/cancel") as response:
                                response.raise_for_status()
                                reply = await response.json()
                            if reply.get("cancelled") is not True:
                                raise RuntimeError("the backend did not accept targeted cancellation")
                            trigger = {"node": "12", "completed_steps": progress["value"], "max_steps": progress["max"]}
                            deadline = cancel_started + 15
                else:
                    await asyncio.sleep(0.1)
            async with session.get(base + "/windows_calibration/metrics") as response:
                response.raise_for_status()
                receipt = await response.json()
            validate_receipt(receipt, check.CalibrationCase.gpu_limit_mb << 20)
    return {"cancelled": True, "trigger": trigger, "cancel_to_terminal_seconds": time.monotonic() - cancel_started,
            "request_to_terminal_seconds": time.monotonic() - started, "allocator_receipt": receipt,
            "audio_saved": False, "qualified_route": False}


def exercise(check, base: str, directory: Path, process, monitor, output: Path, cancel: bool = False) -> dict:
    graph = workflow()
    nodes = check.read_json(base, "/object_info")
    needed = {node["class_type"] for node in graph.values()}
    if not needed.issubset(nodes):
        raise RuntimeError("calibration nodes are missing: " + ", ".join(sorted(needed - set(nodes))))
    if cancel:
        import aiohttp

        try:
            result = asyncio.run(cancel_sampling(check, base, process, monitor, graph))
        except aiohttp.ClientError as error:
            raise RuntimeError(f"the targeted cancellation transport failed ({type(error).__name__})") from None
        result.update(model_repo=windows_sfx.REPO, model_revision=windows_sfx.REVISION, seed=SEED)
        return result
    reply = post_prompt(base, graph)
    if reply.get("error") or reply.get("node_errors"):
        raise RuntimeError("ComfyUI rejected the calibration graph")
    prompt_id = str(uuid.UUID(reply["prompt_id"]))
    started, deadline = time.monotonic(), time.monotonic() + 300
    while True:
        monitor.check()
        if process.poll() is not None:
            raise RuntimeError("the calibration server exited")
        validate_queue(check.read_json(base, "/queue"), prompt_id)
        history = check.read_json(base, "/history/" + prompt_id)
        if prompt_id in history:
            record = history[prompt_id]
            if record.get("status", {}).get("status_str") != "success":
                raise RuntimeError("the calibration workflow failed; inspect the bounded startup log")
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("the calibration workflow exceeded its 300 second deadline")
        time.sleep(0.25)
    outputs = record["outputs"]
    receipts = outputs.get("19", {}).get("windows_calibration")
    if not isinstance(receipts, list) or len(receipts) != 1 or not isinstance(receipts[0], dict):
        raise RuntimeError("calibration did not produce a peak-memory receipt")
    validate_receipt(receipts[0], check.CalibrationCase.gpu_limit_mb << 20)
    found = [item for items in outputs.get("18", {}).values() for item in (items if isinstance(items, list) else [])
             if isinstance(item, dict) and item.get("filename", "").endswith(".flac") and item.get("type") == "output"]
    if len(found) != 1:
        raise RuntimeError("calibration did not produce one lossless audio file")
    root = (directory / "output").resolve()
    path = (root / found[0].get("subfolder", "") / found[0]["filename"]).resolve()
    if root not in path.parents:
        raise RuntimeError("the calibration file is outside its isolated output folder")
    audio = validate_audio(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with path.open("rb") as source, output.open("xb") as target:
        shutil.copyfileobj(source, target)
    return {"model_repo": windows_sfx.REPO, "model_revision": windows_sfx.REVISION, "seed": SEED,
            "prompt": PROMPT, "requested_seconds": SECONDS, "audio": audio, "allocator_receipt": receipts[0],
            "request_to_file_seconds": time.monotonic() - started, "output_filename": output.name,
            "workflow_sha256": hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
            "loader": "GPU-first calibration loader using ComfyUI helpers",
            "qualified_route": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibrate", action="store_true", required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cancel-during-sampling", action="store_true")
    args = parser.parse_args()
    try:
        windows_sfx.verify(args.models)
        receipt_file = args.output.with_suffix(args.output.suffix + ".calibration.json")
        if args.output.exists() or receipt_file.exists():
            raise RuntimeError("the calibration output or receipt already exists; choose a new filename")
        check = checker()
        case = check.CalibrationCase(
            lambda directory: prepare(directory, args.models),
            lambda base, directory, process, monitor: exercise(check, base, directory, process, monitor, args.output,
                                                              args.cancel_during_sampling),
        )
        result = check.run_check(args.source.resolve(), 120, 0, guarded=True, calibration=case)
        sources = [ROOT / "scripts" / name for name in
                   ("windows-sfx-check.py", "windows-comfy-check.py", "windows-calibration-node.py",
                    "windows-comfy-calibration-launcher.py")]
        sources += [ROOT / "app" / name for name in ("windows_sfx.py", "windows_memory.py")]
        result["tool_sha256"] = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sources}
        receipt_file.parent.mkdir(parents=True, exist_ok=True)
        with receipt_file.open("x", encoding="utf-8") as receipt:
            json.dump(result, receipt, indent=2)
            receipt.write("\n")
    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
