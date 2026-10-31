"""Video decode on two cards for the MiniMax H3 video VAE (comfy/ldm/minimax/vae.py).

The decoder decodes independent temporal chunks of the latent and blends their overlaps in order afterwards. Here
the chunks go to whichever of two cards is free: the VAE's card and the other one (spark/gpus.py place_decode routes
a second VAE there). Every chunk comes back to the VAE's card, where the blending and writing run exactly as before,
in order. Each card decodes with a fully loaded copy of the decoder (not ComfyUI's dynamic VRAM loading), replaying
one CUDA graph per tile-batch shape: launching a decoder call's kernels one by one costs about as much CPU as the GPU
spends on them (~0.12 s a call), so two cards driven from one Python process would wait on each other for the GIL.
Same card model, same kernels, same tile batches, and a graph replays the kernels it recorded: the output is the
one-card output byte for byte. STUDIO_DECODE_SPLIT=off keeps one card (the workflow then has a plain VAEDecode).
"""

import logging
import os
import threading
import time

import torch

import comfy.model_management as model_management
import nodes

try:
    from comfy.ldm.minimax.vae import MiniMaxH3VideoVAE
except ImportError:  # an older ComfyUI: nothing to split
    MiniMaxH3VideoVAE = None

AHEAD = 2  # chunks a card may decode ahead of the blending (each ~0.1-0.3 GB at 720p)
_capturing = threading.Lock()  # PyTorch records one CUDA graph at a time in a process


def _graphed(model, group):
    """model._decode_pixels(group) from a CUDA graph per input shape: the first call of a shape runs eagerly on the
    stream the graph is recorded on (cuDNN and cuBLAS set up that stream's handles and workspaces then, which a
    capture cannot do), the second records the graph, every call from then on replays it."""
    graphs = model._studio_graphs
    side = graphs.setdefault("stream", torch.cuda.Stream(group.device))
    here = torch.cuda.current_stream(group.device)
    key = tuple(group.shape)
    held = graphs.get(key)
    if held is None:
        graphs[key] = "seen"
        side.wait_stream(here)
        with torch.cuda.stream(side):
            out = model._decode_pixels(group)
        here.wait_stream(side)
        return out
    if held == "seen":
        inp = group.clone()
        graph = torch.cuda.CUDAGraph()
        side.wait_stream(here)
        with _capturing, torch.cuda.graph(graph, stream=side, capture_error_mode="thread_local"):
            out = model._decode_pixels(inp)
        here.wait_stream(side)
        graphs[key] = held = (graph, inp, out)
    else:
        held[1].copy_(group)
    held[0].replay()
    return held[2].clone()


def _tile_row(self, z_row, x_idx, x_len):
    """_decode_tile_row with the tile batch fixed for the whole decode (both cards decode the same batches)."""
    batch = getattr(self, "_studio_batch", None)
    if batch is None:
        yield from _stock_tile_row(self, z_row, x_idx, x_len)
        return
    slices = [z_row[..., j_pos // self.vae_ratio:(j_pos + j_len) // self.vae_ratio] for j_pos, j_len in zip(x_idx, x_len)]
    for k in range(0, len(slices), batch):
        group = torch.cat(slices[k:k + batch])
        out = _graphed(self, group) if getattr(self, "_studio_graphs", None) is not None else self._decode_pixels(group)
        yield from out.chunk(group.shape[0])


def _decode_temporal(self, z, output_buffer=None):
    copies = getattr(self, "_studio_copies", None)
    if copies is None:
        return _stock_decode_temporal(self, z, output_buffer)
    # the stock decode_temporal, with the chunks' decodes on two cards
    chunk_dec = self.tokens_chunk_size * self.vae_ratio_t
    split_count = int(self.token_drop > 0) + 1
    if output_buffer is None:
        output_buffer = torch.empty(self.decode_output_shape(z.shape), dtype=torch.float32,
                                    device=model_management.intermediate_device())
    pad_tokens, num_chunks = self._decode_temporal_chunks(z.shape[2])
    if pad_tokens > 0:
        pad_z = z[:, :, -1:, :, :].repeat(1, 1, pad_tokens, 1, 1)
        z = torch.cat([z, pad_z], dim=2)
    home = z.device
    from . import peer_copies_work  # the package's: copies between the cards checked once
    direct = all(peer_copies_work(home.index, device.index) for device in copies if device != home)

    def to(tensor, device):  # card to card, or through host memory where peer copies do not arrive intact
        return tensor.to(device) if direct or tensor.device == device else tensor.cpu().to(device)

    free = model_management.get_free_memory(home)
    batch = int(max(1, min(4, free // (128 * 2**20 * z.shape[0]))))  # as the stock _decode_tile_row picks it
    latents = {device: (z if device == home else to(z, device)) for device in copies}
    grad, inference = torch.is_grad_enabled(), torch.is_inference_mode_enabled()
    results, errors, done = {}, [], {device: 0 for device in copies}
    ready = threading.Condition()
    state = {"next": 0, "consumed": 0}
    stop = threading.Event()
    timings = []

    def worker(model, device):
        try:
            model._studio_batch, model._studio_graphs = batch, {}
            with torch.cuda.device(device), torch.inference_mode(inference), torch.set_grad_enabled(grad):
                while True:
                    with ready:
                        while state["next"] < num_chunks and state["next"] - state["consumed"] >= 2 * AHEAD and not stop.is_set():
                            ready.wait(0.5)
                        if stop.is_set() or state["next"] >= num_chunks:
                            return
                        i = state["next"]
                        state["next"] += 1
                    t_start_idx = i * self.tokens_chunk_size
                    t_end_idx = t_start_idx + self.tokens_chunk_size + self.token_overlap
                    mark, cpu = time.perf_counter(), time.thread_time()
                    out = model._adaptive_decode(latents[device][:, :, t_start_idx:t_end_idx, :, :])
                    launched = time.perf_counter() - mark
                    torch.cuda.current_stream(device).synchronize()  # waits without the GIL
                    timings.append((i, device.index, round(time.perf_counter() - mark, 2), round(launched, 2),
                                    round(time.thread_time() - cpu, 2), round(mark - started, 2)))
                    with ready:
                        results[i] = out
                        done[device] += 1
                        ready.notify_all()
        except BaseException as error:  # noqa: BLE001
            with ready:
                errors.append(error)
                ready.notify_all()
        finally:
            model._studio_batch = model._studio_graphs = None

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(model, device), daemon=True, name=f"decode-{device}")
               for device, model in copies.items()]
    for thread in threads:
        thread.start()
    dec = output_buffer
    dec_overlap = None
    write_pos = 0

    def write_part(part):
        nonlocal write_pos
        part_frames = part.shape[2]
        if part_frames <= 0:
            return
        part = self._finalize_pixels(part)
        copy_frames = min(part_frames, max(0, dec.shape[2] - write_pos))
        if copy_frames > 0:
            dec[:, :, write_pos:write_pos + copy_frames, :, :].copy_(part[:, :, :copy_frames, :, :])
            write_pos += copy_frames

    try:
        for i in range(num_chunks):
            with ready:
                while i not in results and not errors:
                    ready.wait()
                if errors:
                    raise errors[0]
                clip_dec = results.pop(i)
                state["consumed"] = i + 1
                ready.notify_all()
            with _capturing:  # no GPU work of ours on a card while one records a graph
                if clip_dec.device != home:
                    clip_dec = to(clip_dec, home)
                for j in range(split_count):
                    f_start_idx = j * chunk_dec
                    f_end_idx = min(f_start_idx + chunk_dec, clip_dec.shape[2])
                    clip_dec_chunk = clip_dec[:, :, f_start_idx:f_end_idx, :, :]
                    clip_dec_chunk = clip_dec_chunk[:, :, self.frame_pre_padding:, :, :]
                    if j == 0:
                        if dec_overlap is not None:
                            clip_dec_chunk = self.blend(dec_overlap, clip_dec_chunk, self.frame_overlap, dim=-3)
                            dec_overlap = None
                        write_part(clip_dec_chunk)
                    else:
                        dec_overlap = clip_dec_chunk.contiguous()
                if i == num_chunks - 1 and dec_overlap is not None:
                    write_part(dec_overlap)
                    dec_overlap = None
                del clip_dec, clip_dec_chunk
    finally:
        stop.set()
        with ready:
            ready.notify_all()
        for thread in threads:
            thread.join()
    logging.info(f"Studio two-card decode: {num_chunks} chunks in {time.perf_counter() - started:.2f} s "
                 f"({', '.join(f'{device} {count}' for device, count in done.items())})"
                 + (f"; (chunk, card, s, s launching, cpu s, started at) {sorted(timings)}"
                    if os.environ.get("TF_H3_PROFILE") == "1" else ""))
    return dec


if MiniMaxH3VideoVAE is not None:
    _stock_decode_temporal = MiniMaxH3VideoVAE.decode_temporal
    _stock_tile_row = MiniMaxH3VideoVAE._decode_tile_row
    MiniMaxH3VideoVAE.decode_temporal = _decode_temporal
    MiniMaxH3VideoVAE._decode_tile_row = _tile_row


def _off():
    return os.environ.get("STUDIO_DECODE_SPLIT", "on").strip().lower() in ("0", "off", "no", "false")


def _unload(patchers):
    """Takes the fully loaded copies off their cards again (ComfyUI keeps a loaded model until it needs the room)."""
    loaded = model_management.current_loaded_models
    for i in range(len(loaded) - 1, -1, -1):
        if loaded[i].model is not None and any(loaded[i].model is patcher for patcher in patchers):
            loaded[i].model_unload()
            loaded.pop(i)
    model_management.soft_empty_cache()


class StudioTwoCardVAEDecode:
    """VAEDecode with a second copy of the VAE on another card (vae_other): a MiniMax H3 video decodes its temporal
    chunks on both. Anything else decodes as VAEDecode does, on vae's card."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"samples": ("LATENT",), "vae": ("VAE",), "vae_other": ("VAE",)}}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "decode"
    CATEGORY = "latent"

    def decode(self, samples, vae, vae_other):
        model = vae.first_stage_model
        latent = samples["samples"]
        if (MiniMaxH3VideoVAE is None or _off() or not isinstance(model, MiniMaxH3VideoVAE)
                or not isinstance(vae_other.first_stage_model, MiniMaxH3VideoVAE)
                or latent.ndim != 5 or latent.shape[2] < 2 or vae.device == vae_other.device):
            return nodes.VAEDecode().decode(vae, samples)
        started = time.perf_counter()
        held = []
        try:
            # fully loaded copies on both cards: a CUDA graph needs the weights to stay where they were recorded
            for each in (vae, vae_other):
                patcher = each.patcher.clone(disable_dynamic=True)
                patcher.load_device = each.device  # the loader rebuilt it for ComfyUI's default card
                held.append(patcher)
                with model_management.cuda_device_context(each.device):
                    model_management.load_models_gpu([patcher], force_full_load=True)
                # under dynamic VRAM the mixed-precision ops cast a float32 weight or bias to the input's dtype every
                # call; a full load leaves them float32 beside float16 inputs. Cast once here, to the same values (the
                # per-call cast also syncs with ComfyUI's offload stream, which a CUDA graph cannot record).
                cast = 0
                for module in patcher.model.modules():
                    if hasattr(module, "comfy_cast_weights"):
                        for name in ("weight", "bias"):
                            param = getattr(module, name, None)
                            if type(param) is torch.nn.Parameter and type(param.data) is torch.Tensor and param.dtype == torch.float32:
                                param.data = param.data.to(each.vae_dtype)
                                cast += 1
                logging.info(f"Studio two-card decode: copy for {each.device}, {cast} float32 weights and biases to {each.vae_dtype}")
                if patcher.model.post_quant_conv.weight.device != each.device:
                    raise RuntimeError(f"the copy for {each.device} loaded on {patcher.model.post_quant_conv.weight.device}")
        except Exception as error:  # noqa: BLE001
            logging.warning(f"Studio two-card decode: no fully loaded copies ({error!r}); one card")
            _unload(held)
            return nodes.VAEDecode().decode(vae, samples)
        model._studio_copies = {each.device: patcher.model for each, patcher in zip((vae, vae_other), held)}
        logging.info(f"Studio two-card decode: fully loaded copies on {list(model._studio_copies)} in "
                     f"{time.perf_counter() - started:.2f} s")
        try:
            return nodes.VAEDecode().decode(vae, samples)
        finally:
            model._studio_copies = None
            _unload(held)


NODE_CLASS_MAPPINGS = {"StudioTwoCardVAEDecode": StudioTwoCardVAEDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"StudioTwoCardVAEDecode": "VAE Decode (two cards)"}
