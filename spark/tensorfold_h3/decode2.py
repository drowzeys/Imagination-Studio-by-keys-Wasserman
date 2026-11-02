"""Video decode on two cards for the MiniMax H3 video VAE (comfy/ldm/minimax/vae.py).

The decoder decodes independent temporal chunks of the latent and blends their overlaps in order afterwards. Here
the chunks go to whichever of two cards is free: the VAE's card and the other one (spark/gpus.py place_decode routes
a second VAE there). Every chunk comes back to the VAE's card, where the blending and writing run exactly as before,
in order. Each card decodes with a fully loaded copy of the decoder (not ComfyUI's dynamic VRAM loading), replaying
one CUDA graph per tile-batch shape: launching a decoder call's kernels one by one costs about as much CPU as the GPU
spends on them (~0.12 s a call), so two cards driven from one Python process would wait on each other for the GIL.
Same card model, same kernels, same tile batches, and a graph replays the kernels it recorded: the output is the
one-card output byte for byte. STUDIO_DECODE_SPLIT=off keeps one card (the workflow then has a plain VAEDecode).

ComfyUI's decode_temporal itself runs unchanged (its blending and writing are not copied here: ComfyUI is GPL-3.0 and
this repository Apache-2.0); only the chunk decodes it asks for are done ahead, on the cards. Nothing is patched in
ComfyUI's classes: the decoder instance gets its stand-ins for one decode and loses them after it.
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
    """A decoder copy's tile row with the tile batch fixed for the whole decode (both cards decode the same batches),
    each batch through its CUDA graph. Set on a copy only while its worker runs (``_decode_on_cards``)."""
    if self.__dict__.get("_studio_owner") is not threading.current_thread():  # the stock loop's own decode on this card
        yield from type(self)._decode_tile_row(self, z_row, x_idx, x_len)
        return
    batch = self._studio_batch
    slices = [z_row[..., j_pos // self.vae_ratio:(j_pos + j_len) // self.vae_ratio] for j_pos, j_len in zip(x_idx, x_len)]
    for k in range(0, len(slices), batch):
        group = torch.cat(slices[k:k + batch])
        yield from _graphed(self, group).chunk(group.shape[0])


def _decode_on_cards(self, copies, z, output_buffer=None):
    """ComfyUI's own decode_temporal, unchanged, with each of its chunk decodes (``_adaptive_decode``) done ahead of
    it on whichever card is free. The chunks the stock loop will ask for are predicted from the padded latent; each
    one it does ask for is compared with the prediction (equal shape and values) before a card's result stands in for
    it, and one that differs is decoded here on the VAE's card as the stock loop would. The blending and writing are
    ComfyUI's code running as it is (none of it is copied here)."""
    cls = type(self)
    home = z.device
    pad_tokens, num_chunks = self._decode_temporal_chunks(z.shape[2])
    padded = torch.cat([z, z[:, :, -1:].repeat(1, 1, pad_tokens, 1, 1)], dim=2) if pad_tokens > 0 else z
    starts = [i * self.tokens_chunk_size for i in range(num_chunks)]
    chunks = [padded[:, :, t:t + self.tokens_chunk_size + self.token_overlap] for t in starts]
    from . import peer_copies_work  # the package's: copies between the cards checked once
    direct = all(peer_copies_work(home.index, device.index) for device in copies if device != home)

    def to(tensor, device):  # card to card, or through host memory where peer copies do not arrive intact
        return tensor.to(device) if direct or tensor.device == device else tensor.cpu().to(device)

    free = model_management.get_free_memory(home)
    batch = int(max(1, min(4, free // (128 * 2**20 * z.shape[0]))))  # as the stock _decode_tile_row picks it
    sent = {device: [c if device == home else None for c in chunks] for device in copies}
    grad, inference = torch.is_grad_enabled(), torch.is_inference_mode_enabled()
    results, errors, done = {}, [], {device: 0 for device in copies}
    ready = threading.Condition()
    state = {"next": 0, "consumed": 0}
    stop = threading.Event()
    timings = []
    started = time.perf_counter()

    def worker(model, device):
        try:
            model._studio_batch, model._studio_graphs = batch, {}
            model._studio_owner = threading.current_thread()
            model._decode_tile_row = _tile_row.__get__(model)
            with torch.cuda.device(device), torch.inference_mode(inference), torch.set_grad_enabled(grad):
                while True:
                    with ready:
                        while state["next"] < num_chunks and state["next"] - state["consumed"] >= 2 * AHEAD and not stop.is_set():
                            ready.wait(0.5)
                        if stop.is_set() or state["next"] >= num_chunks:
                            return
                        i = state["next"]
                        state["next"] += 1
                    mark, cpu = time.perf_counter(), time.thread_time()
                    part = sent[device][i] if sent[device][i] is not None else to(chunks[i], device)
                    out = cls._adaptive_decode(model, part)  # the class's own: never the stand-in below
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
            model.__dict__.pop("_decode_tile_row", None)
            model._studio_batch = model._studio_graphs = model._studio_owner = None

    asked = {"n": 0, "own": False}

    def stand_in(part, frames=slice(None)):
        """What the stock loop calls for chunk n: the card's result when the chunk is the predicted one."""
        n = asked["n"]
        asked["n"] += 1
        expected = chunks[n] if n < num_chunks and not asked["own"] else None
        if (frames != slice(None) or expected is None or part.shape != expected.shape
                or part.device != expected.device or not torch.equal(part, expected)):
            if not asked["own"]:
                logging.warning("Studio two-card decode: a chunk differs from the predicted one; the rest on one card")
                asked["own"] = True
                stop.set()
                with ready:
                    ready.notify_all()
            return cls._adaptive_decode(self, part, frames)
        _capturing.release()  # the copies may record their graphs while this waits
        try:
            with ready:
                while n not in results and not errors:
                    ready.wait()
                if errors:
                    raise errors[0]
                out = results.pop(n)
                state["consumed"] = n + 1
                ready.notify_all()
        finally:
            _capturing.acquire()  # no GPU work of the stock loop on a card while one records a graph
        return to(out, home) if out.device != home else out

    threads = [threading.Thread(target=worker, args=(model, device), daemon=True, name=f"decode-{device}")
               for device, model in copies.items()]
    _capturing.acquire()
    self._adaptive_decode = stand_in
    try:
        for thread in threads:
            thread.start()
        dec = cls.decode_temporal(self, z, output_buffer)
    finally:
        self.__dict__.pop("_adaptive_decode", None)
        _capturing.release()
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


def _off():
    return os.environ.get("STUDIO_DECODE_SPLIT", "on").strip().lower() in ("0", "off", "no", "false")


def _self_check():
    """STUDIO_DECODE_SPLIT=self (spark/gpus.py decode_self_check): one card stands in for both."""
    return os.environ.get("STUDIO_DECODE_SPLIT", "").strip().lower() == "self"


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
                or latent.ndim != 5 or latent.shape[2] < 2 or (vae.device == vae_other.device and not _self_check())):
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
        copies = {each.device: patcher.model for each, patcher in zip((vae, vae_other), held)}
        logging.info(f"Studio two-card decode: fully loaded copies on {list(copies)} in "
                     f"{time.perf_counter() - started:.2f} s")
        # this decoder's decode_temporal, for this one decode only: nothing of ComfyUI's class is replaced
        model.decode_temporal = lambda z, output_buffer=None: _decode_on_cards(model, copies, z, output_buffer)
        try:
            return nodes.VAEDecode().decode(vae, samples)
        finally:
            model.__dict__.pop("decode_temporal", None)
            _unload(held)


NODE_CLASS_MAPPINGS = {"StudioTwoCardVAEDecode": StudioTwoCardVAEDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"StudioTwoCardVAEDecode": "VAE Decode (two cards)"}
