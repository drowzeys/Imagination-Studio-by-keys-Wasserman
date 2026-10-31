"""ComfyUI node: FastH3's transformer blocks on TensorFold's CUDA engine (int8 on the tensor cores).

ComfyUI keeps the text encoder, the sampler, the patch projections, the final layer and the decoders. This node
replaces the 50 blocks of each denoising pass with one call into libtf_h3.so (TensorFold's H3 family, built from
zig/src/families/h3/cuda.zig), which reads the same checkpoint file and quantizes it on the GPU when first used.

A LoRA on the model (LoraLoaderModelOnly ahead of this node) is taken off ComfyUI's patches for the projections the
engine holds and merged here instead, as ComfyUI merges it into the bf16 weights (its LoRA arithmetic, then the
seeded rounding to bf16); the engine quantizes the merged weight to int8. Only the touched projections are redone,
and a later render without the LoRA puts the checkpoint's own back. The int8 copy saved beside the checkpoint is
never changed. The LoRA's other keys (patch projections, final layer, modulation) stay ComfyUI's.

Environment: TF_H3_LIB (the library; default beside this file), TF_H3_CHECK=1 (first pass of a run: also run
ComfyUI's own blocks and log how close the engine is), TF_H3_PROFILE=1 (log where a pass's time went).
"""

import ctypes
import logging
import math
import os
import threading
import time
import uuid

import torch

import comfy.patcher_extension
import folder_paths
from comfy.ldm.minimax.model import MiniMaxH3Model

TILE = 64
CUBE = (4, 4, 4)


import sys  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tp  # noqa: E402  (two GPUs: the shared-memory world beside this file)

# the engine's Model.Inputs; an engine from before the tile range reads only the fields it knows
Inputs = tp.Inputs


_library = None
_engines = {}   # checkpoint path -> engine handle; the int8 weights stay on the GPU while ComfyUI runs
_merged = {}    # checkpoint path -> {model key: the patches merged into that projection}

# the engine's projections, by their names in the model and the number tf_h3_set_weight knows them by
PROJECTIONS = {"attn.qkv_proj.weight": 0, "attn.to_gate_compress.weight": 1, "attn.out_proj.weight": 2,
               "mlp.fc1.weight": 3, "mlp.fc2.weight": 4}
# what else of a block the engine holds: a patch on it cannot be honoured
HELD = ("norm1.", "norm2.", "attn.q_norm.", "attn.k_norm.", "attn.qkv_proj.", "attn.to_gate_compress.",
        "attn.out_proj.", "mlp.fc1.", "mlp.fc2.")


def library():
    global _library
    if _library is None:
        path = os.environ.get("TF_H3_LIB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "libtf_h3.so")
        lib = ctypes.CDLL(path)
        lib.tf_h3_open.restype = ctypes.c_void_p
        lib.tf_h3_open.argtypes = [ctypes.c_char_p]
        lib.tf_h3_close.argtypes = [ctypes.c_void_p]
        lib.tf_h3_shape.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32 * 7)]
        lib.tf_h3_forward.restype = ctypes.c_double
        lib.tf_h3_forward.argtypes = [ctypes.c_void_p, ctypes.POINTER(Inputs)]
        lib.tf_h3_profile.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.tf_h3_spent.restype = ctypes.c_int
        lib.tf_h3_spent.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double * 16), ctypes.POINTER(ctypes.c_char_p * 16)]
        if hasattr(lib, "tf_h3_set_weight"):
            lib.tf_h3_set_weight.restype = ctypes.c_int
            lib.tf_h3_set_weight.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint64,
                                             ctypes.c_uint32, ctypes.c_uint32]
        if hasattr(lib, "tf_h3_tp_init"):
            tp.bind(lib)
        _library = lib
    return _library


def engine(path):
    handle = _engines.get(path)
    if handle is None:
        torch.cuda.synchronize()
        handle = library().tf_h3_open(path.encode())
        if not handle:
            raise RuntimeError(f"TensorFold H3: could not load {path}")
        shape = (ctypes.c_uint32 * 7)()
        library().tf_h3_shape(handle, ctypes.byref(shape))
        logging.info(f"TensorFold H3: {shape[4]} blocks of {shape[0]}/{shape[1]}/{shape[3]} in int8, "
                     f"{'read from the saved copy' if shape[6] else 'converted from the checkpoint (saved for the next start)'} in {shape[5] / 1000:.1f} s")
        _engines[path] = handle
    return handle


class World:
    """Rank 1 on the second card (tp_rank.py) and this engine joined to it as rank 0, over shared memory."""

    def __init__(self, path, handle):
        import subprocess
        import signal
        tp.sweep()
        self.name = f"tf-h3-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.region = int(float(os.environ.get("STUDIO_TP_REGION_GB", "4")) * (1 << 30))
        self.box = tp.open_box(self.name, create=True)
        self.head = tp.Header.from_buffer(self.box)
        self.base = tp.address(self.box)
        self.head.magic = tp.MAGIC
        int8 = path + ".tf-int8"
        if not os.path.exists(int8):
            raise RuntimeError(f"no int8 copy at {int8} for the second card (one render on one card writes it)")
        # the exchange: peer to peer (each GPU writes its tiles into the other's memory over PCIe) when the engine
        # has it, else host shared memory; STUDIO_TP_EXCHANGE=shm|p2p|auto
        self.kind = exchange_kind()
        two = rank_one_gpu()
        visible = [part.strip() for part in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if part.strip()]
        # peer to peer opens rank 0's region on rank 0's card, so rank 1 sees that card too (its own still first: device 0)
        cards = f"{two},{visible[0]}" if self.kind == "p2p" and visible and visible[0] != two else two
        cards = os.environ.get("STUDIO_TP_RANK1_CARDS", "").strip() or cards  # a diagnostic: rank 1's CUDA_VISIBLE_DEVICES
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": cards,
               "TF_H3_LIB": os.environ.get("TF_H3_LIB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "libtf_h3.so")}
        libc = ctypes.CDLL(None)

        def die_with_parent():
            libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG: the rank goes when ComfyUI goes

        mark = time.perf_counter()
        self.proc = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tp_rank.py"),
                                      self.name, int8, str(self.region), "2", "1", self.kind], env=env, preexec_fn=die_with_parent)
        # this rank's region first (peer to peer: its IPC handle, which rank 1 opens before it says it is ready) ...
        if library().tf_h3_tp_init(handle, f"{self.kind}:/{self.name}".encode(), 0, 2, 0, self.region) != 0:
            self.close()
            raise RuntimeError(f"tf_h3_tp_init ({self.kind}) failed on the first card")
        self.handle = handle
        while self.head.ready == 0:
            if self.proc.poll() is not None or time.perf_counter() - mark > 600:
                self.close(handle)
                raise RuntimeError(f"rank 1 did not start: {self.head.message.decode(errors='replace')}")
            time.sleep(0.05)
        if self.head.ready != 1:
            message = self.head.message.decode(errors="replace")
            self.close(handle)
            raise RuntimeError(message)
        # ... then rank 1's, published by now: connecting never waits on a rank that has gone
        if not tp.connect(library(), handle, 2, 0):
            self.close(handle)
            raise RuntimeError(f"connecting rank 1 ({self.kind}) failed on the first card")
        logging.info(f"TensorFold H3: two GPUs, rank 1 on CUDA device {cards} ({self.head.message.decode(errors='replace')}), "
                     f"exchange {self.kind} {self.region >> 20} MB in {self.name}")

    def view(self, offset, dtype, count):
        return torch.frombuffer(self.box, dtype=dtype, count=count, offset=offset)

    def close(self, handle=None):
        try:
            self.head.stop = 1
            if self.proc.poll() is None:
                self.proc.wait(timeout=20)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        if handle is not None:
            library().tf_h3_tp_close(handle)
        tp.unlink(self.name)


_worlds = {}    # checkpoint path -> World, while ComfyUI runs
_tp_off = set()  # checkpoint paths whose two-GPU world failed this session: one GPU for them from then on


def iommu_passthrough():
    """True when no IOMMU translates the NVIDIA GPUs' DMA (no IOMMU group, or group type identity: iommu=pt). With the
    IOMMU translating (AMD-Vi / VT-d DMA, DMA-FQ), CUDA peer copies report success and move nothing (CUDA guide:
    P2P is unsupported on bare-metal Linux with the IOMMU on)."""
    import glob
    gpus = [d for d in glob.glob("/sys/bus/pci/devices/*") if open(d + "/vendor").read().strip() == "0x10de"
            and open(d + "/class").read().strip().startswith("0x03")]
    for d in gpus:
        kind = os.path.join(d, "iommu_group", "type")
        if os.path.exists(kind) and open(kind).read().strip() != "identity":
            return False
    return bool(gpus)


_peer = {}  # (a, b) -> whether copies between CUDA devices a and b arrive intact


def peer_copies_work(a=0, b=1):
    """Whether torch's copies between CUDA devices a and b arrive intact both ways (checked once). With the IOMMU
    translating (AMD-Vi / VT-d in DMA mode), or a PCIe path that drops peer writes, they report success and leave zeros
    while the driver reports peer access: work for the other card then goes through host memory (docs/two-gpus.md)."""
    key = (a, b)
    if key not in _peer:
        ok = False
        if os.environ.get("STUDIO_PEER_COPIES", "auto").strip().lower() in ("0", "off", "no", "false"):
            _peer[key] = False  # STUDIO_PEER_COPIES=off: every copy between the cards through host memory
            return False
        result, failure = [], []

        def test():  # on a thread of its own: the engine's CUDA context may be current on the caller's
            try:
                if torch.cuda.device_count() > max(a, b):
                    x = torch.randint(1, 255, (1 << 20,), dtype=torch.uint8, device=f"cuda:{a}")
                    y = x.to(f"cuda:{b}")
                    z = y.to(f"cuda:{a}")
                    torch.cuda.synchronize(a)
                    torch.cuda.synchronize(b)
                    host = x.cpu()
                    result.append(torch.equal(host, y.cpu()) and torch.equal(host, z.cpu()))
            except Exception as error:  # noqa: BLE001
                failure.append(error)

        thread = threading.Thread(target=test, name="peer-copy-test")
        thread.start()
        thread.join()
        ok = bool(result and result[0])
        if failure:
            logging.warning(f"TensorFold H3: the copy test between cuda:{a} and cuda:{b} failed: {failure[0]}")
        if ok:
            logging.info(f"TensorFold H3: copies between cuda:{a} and cuda:{b} checked, they arrive intact")
        else:
            logging.warning(f"TensorFold H3: copies between cuda:{a} and cuda:{b} do not arrive intact (an IOMMU in DMA "
                            f"mode? boot with iommu=pt); the cards exchange through host memory")
        _peer[key] = ok
    return _peer[key]


def exchange_kind():
    """STUDIO_TP_EXCHANGE: shm (kernels pack into and unpack out of the shared host region), dma (the same region,
    filled and read with bulk copy-engine copies; engine tf_h3_tp_dma) or p2p (GPU to GPU; engine tf_h3_tp_p2p).
    p2p is used only when the GPUs' IOMMU groups pass through (iommu=pt), else dma: translated, peer copies move nothing."""
    wanted = os.environ.get("STUDIO_TP_EXCHANGE", "shm").strip().lower()
    lib = library()
    if wanted == "p2p" and hasattr(lib, "tf_h3_tp_p2p"):
        if iommu_passthrough() and peer_copies_work(0, 1):
            return "p2p"
        if not iommu_passthrough():
            logging.warning("TensorFold H3: STUDIO_TP_EXCHANGE=p2p but the IOMMU translates the GPUs (boot with iommu=pt); using dma")
        wanted = "dma"
    if wanted == "dma" and hasattr(lib, "tf_h3_tp_dma"):
        return "dma"
    return "shm"


def tp_chunks(w, keep):
    """The parts a peer-to-peer pass's q k v work is cut into (STUDIO_TP_CHUNKS, default 4, at most 8); 0 for one part:
    another exchange, dense attention, or an engine without tf_h3_tp_chunks."""
    try:
        wanted = int(os.environ.get("STUDIO_TP_CHUNKS", "4"))
    except ValueError:
        wanted = 4
    if wanted < 2 or keep <= 0 or getattr(w, "kind", "") != "p2p" or not hasattr(library(), "tf_h3_tp_chunks"):
        return 0
    return min(wanted, 8)


def rank_one_gpu():
    """The second card for rank 1, as CUDA_VISIBLE_DEVICES names it: STUDIO_TP_GPU, else the second of the cards this
    ComfyUI was pinned to (spark/gpu.sh, STUDIO_GPUS: the head's first), else index 1."""
    explicit = os.environ.get("STUDIO_TP_GPU", "").strip()
    if explicit:
        return explicit
    visible = [part.strip() for part in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if part.strip()]
    return visible[1] if len(visible) >= 2 else "1"


def tp_wanted():
    wanted = os.environ.get("STUDIO_TP", "auto").strip().lower()
    if wanted in ("0", "off", "no", "false"):
        return False
    try:
        return torch.cuda.device_count() >= 2
    except Exception:  # noqa: BLE001
        return False


def world(path):
    """The two-GPU world for this checkpoint, started on first use; None when it is off or has failed."""
    if path in _tp_off or not tp_wanted() or not hasattr(library(), "tf_h3_tp_init"):
        return None
    if path not in _worlds:
        try:
            _worlds[path] = World(path, engine(path))
        except Exception as error:  # noqa: BLE001
            kind = exchange_kind()
            if kind != "shm":
                # peer to peer refused (no P2P between these cards, as on GeForce): bulk copies through host memory;
                # those refused too: the plain shared-memory exchange
                fallback = "dma" if kind == "p2p" and hasattr(library(), "tf_h3_tp_dma") else "shm"
                logging.warning(f"TensorFold H3: the {kind} exchange failed ({error}); trying {fallback}")
                os.environ["STUDIO_TP_EXCHANGE"] = fallback
                return world(path)
            logging.warning(f"TensorFold H3: two GPUs unavailable, one GPU from here: {error}")
            _tp_off.add(path)
            return None
    return _worlds[path]


def release():
    for path, w in list(_worlds.items()):
        w.close(_engines.get(path))
    _worlds.clear()
    for handle in _engines.values():
        library().tf_h3_close(handle)
    _engines.clear()
    _merged.clear()


def take_block_patches(patcher):
    """The patches on the engine's projections, removed from `patcher` (a clone): {model key: patches}."""
    taken = {}
    for key in list(patcher.patches):
        if not (isinstance(key, str) and key.startswith("diffusion_model.blocks.")):
            continue
        part = key.split(".", 3)[3]                     # diffusion_model.blocks.N.<part>
        if part in PROJECTIONS:
            taken[key] = patcher.patches.pop(key)
        elif part.startswith(HELD):
            raise ValueError(f"TensorFold H3: a LoRA patches {key}, which the engine holds and cannot take; "
                             "render this LoRA with --engine comfy")
    return taken


def merge(handle, path, patches):
    """Brings the engine's projections to the checkpoint's weights with `patches` merged in, redoing only the ones
    whose patches changed since the last call."""
    import comfy.float
    import comfy.lora
    import comfy.model_management
    import comfy.utils
    from safetensors import safe_open

    done = _merged.setdefault(path, {})
    def same(a, b):
        return a is not None and b is not None and len(a) == len(b) and all(
            x[1] is y[1] and x[0] == y[0] and x[2] == y[2] and x[3] == y[3] for x, y in zip(a, b))
    todo = sorted(k for k in set(patches) | set(done) if not same(patches.get(k), done.get(k)))
    if not todo:
        return
    if not hasattr(library(), "tf_h3_set_weight"):
        raise RuntimeError("TensorFold H3: this engine cannot take a LoRA; rebuild it (spark/build-engine.sh)")
    mark = time.perf_counter()
    device = torch.device("cuda")
    compute = comfy.model_management.lora_compute_dtype(device)
    torch.cuda.synchronize()
    with safe_open(path, framework="pt", device="cpu") as f:
        for key in todo:
            block, part = key.split(".", 3)[2:]
            weight = f.get_tensor(key[len("diffusion_model."):])
            if key in patches:
                w = comfy.lora.calculate_weight(patches[key], weight.to(device, dtype=compute, copy=True), key)
                w = comfy.float.stochastic_rounding(w, torch.bfloat16, seed=comfy.utils.string_to_seed(key))
            else:
                w = weight.to(device)
            w = w.to(torch.bfloat16).contiguous()
            torch.cuda.synchronize()
            if library().tf_h3_set_weight(handle, int(block), PROJECTIONS[part], w.data_ptr(), w.shape[0], w.shape[1]):
                done.clear()   # the engine is part merged: the next call redoes everything
                raise RuntimeError(f"TensorFold H3: the engine refused {key} {tuple(w.shape)}")
            del w, weight
            if key in patches:
                done[key] = list(patches[key])
            else:
                done.pop(key, None)
    try:
        # the checkpoint's pages the merge read are not needed again: give them back
        fd = os.open(path, os.O_RDONLY)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        os.close(fd)
    except OSError:
        pass
    merged = sum(1 for k in todo if k in patches)
    logging.info(f"TensorFold H3: LoRA merged into {merged} projections"
                 + (f", {len(todo) - merged} put back to the checkpoint's" if len(todo) > merged else "")
                 + f", in {time.perf_counter() - mark:.1f} s")


def tile_plan(layout, device):
    """FastVideo's tile order: every segment ahead of the video in its own 64-row tiles, the video in 4x4x4 cubes.

    Returns each row's padded slot, each tile's real rows, and the number of prefix tiles.
    """
    _text, latent_t, latent_h, latent_w, _audio = layout.signature
    grid = (int(latent_t), int(latent_h) // 2, int(latent_w) // 2)
    tiles, prefix = [], 0
    for a, b, kind in layout.segments:
        n = b - a
        if kind != "video":
            m = (n + TILE - 1) // TILE
            seg = torch.full((m * TILE,), -1, dtype=torch.int64)
            seg[:n] = torch.arange(a, b)
            tiles.append(seg.view(m, TILE))
            prefix += m
            continue
        if grid[0] * grid[1] * grid[2] != n:
            raise RuntimeError(f"TensorFold H3: video segment of {n} rows does not match the latent grid {grid}")
        pt, ph, pw = ((g + c - 1) // c * c for g, c in zip(grid, CUBE))
        padded = torch.full((pt, ph, pw), -1, dtype=torch.int64)
        padded[:grid[0], :grid[1], :grid[2]] = torch.arange(a, b).view(*grid)
        cubes = (padded.view(pt // CUBE[0], CUBE[0], ph // CUBE[1], CUBE[1], pw // CUBE[2], CUBE[2])
                 .permute(0, 2, 4, 1, 3, 5).reshape(-1, TILE))
        order = torch.argsort((cubes < 0).to(torch.int8), dim=1, stable=True)
        tiles.append(torch.gather(cubes, 1, order))
    tiles = torch.cat(tiles)
    src = tiles.reshape(-1)
    live = src >= 0
    slot = torch.empty(layout.seq_len, dtype=torch.int64)
    slot[src[live]] = torch.nonzero(live).flatten()
    return (slot.to(torch.int32).to(device), (tiles >= 0).sum(1).to(torch.int32).to(device), prefix)


def cosine(a, b):
    a, b = a.float().flatten(), b.float().flatten()
    return float(torch.dot(a, b) / (a.norm() * b.norm()))


_moved = {}  # (tiles, rows) of a pass -> rows moved from rank 0's half to rank 1's, learned from the passes' waits


def balance(key, moved, mine, theirs, took1, rows1, rows):
    """After a two-GPU pass: move rows towards the rank that waited longer for the other's tiles. A rank's wait is its
    exchange time less its sending (tp.stats); its compute is its forward less that wait. STUDIO_TP_BALANCE=off keeps
    the even split."""
    if os.environ.get("STUDIO_TP_BALANCE", "on").strip().lower() in ("0", "off", "no", "false"):
        return
    wait0, wait1 = mine[0] - mine[2], theirs[0] - theirs[2]
    compute1 = took1 - wait1
    if rows1 <= 0 or compute1 <= 0 or abs(wait1 - wait0) < 0.02:
        return
    step = (wait1 - wait0) / (2 * compute1 / rows1)  # rows whose compute closes half the gap on each side
    _moved[key] = max(-rows / 4, min(rows / 4, moved + 0.7 * step))


class Blocks:
    """One patched model's state: the engine, and what stays the same through a sampling run."""

    def __init__(self, diffusion_model, path, sparsity, patches=None):
        self.model = diffusion_model
        self.path = path
        self.sparsity = sparsity
        self.patches = patches or {}
        self.plans = {}
        self.checked = False
        self.passes = 0

    def plan(self, layout, device):
        key = (tuple(layout.signature), tuple(layout.segments))
        if key not in self.plans:
            self.plans = {key: tile_plan(layout, device)}
        return self.plans[key]

    def run(self, args, blocks=0, dense=False):
        h, layout = args["img"], args["layout"]
        device = h.device
        rows = h.shape[0]
        if not h.is_contiguous() or h.dtype != torch.bfloat16:
            raise RuntimeError(f"TensorFold H3 wants a contiguous bf16 stream, got {h.dtype}")
        tables = torch.stack([torch.stack(block.adaln_proj(args["t_emb"]), dim=1) for block in self.model.blocks])
        tables = tables.to(torch.float32).contiguous()                      # (blocks, lines, 6, hidden)
        line = torch.empty(rows, dtype=torch.int32, device=device)
        for a, b, row in args["mod_segments"]:
            line[a:b] = row if isinstance(row, int) else row.to(torch.int32)
        rope = args["rope_freqs"]                                            # (1, rows, 1, rot, 2, 2): c, -s, s, c
        cos = rope[0, :, 0, :, 0, 0].to(torch.float32).contiguous()
        sin = rope[0, :, 0, :, 1, 0].to(torch.float32).contiguous()
        slot, sizes, prefix = self.plan(layout, device)
        video_tiles = sizes.shape[0] - prefix
        keep = 0 if dense or self.sparsity <= 0 else max(1, min(math.ceil((1.0 - self.sparsity) * video_tiles), video_tiles))
        inputs = Inputs(x=h.data_ptr(), tables=tables.data_ptr(), line=line.data_ptr(), cos=cos.data_ptr(), sin=sin.data_ptr(),
                        slot=slot.data_ptr(), sizes=sizes.data_ptr(), rows=rows, lines=tables.shape[1], rot=cos.shape[1],
                        tiles=sizes.shape[0], prefix_tiles=prefix, keep=keep, blocks=blocks, attention_out=0)
        handle = engine(self.path)
        merge(handle, self.path, self.patches)
        capture = os.environ.get("TF_H3_CAPTURE", "").strip()
        if capture and not os.path.exists(capture) and not self.patches and blocks == 0:
            # the test rig (tests/tf_h3_tp_rig.py): this pass's inputs, to replay outside ComfyUI on one GPU and two
            torch.save({"path": self.path, "h": h.detach().cpu().clone(), "tables": tables.cpu(), "line": line.cpu(),
                        "cos": cos.cpu(), "sin": sin.cpu(), "slot": slot.cpu(), "sizes": sizes.cpu(), "prefix": int(prefix),
                        "keep": int(keep), "lines": int(tables.shape[1]), "rot": int(cos.shape[1])}, capture)
            logging.info(f"TensorFold H3: this pass's inputs saved to {capture} ({rows} rows, {sizes.shape[0]} tiles)")
        # two GPUs: the pass split by tile (tp.py); not with a LoRA (the second card holds the checkpoint's weights)
        # nor for the checks' partial passes
        w = world(self.path) if not self.patches and blocks == 0 else None
        if w is not None:
            try:
                return self.run_two(w, handle, h, tables, line, cos, sin, slot, sizes, prefix, keep), keep, sizes.shape[0]
            except Exception as error:  # noqa: BLE001
                logging.warning(f"TensorFold H3: the two-GPU pass failed, one GPU from here: {error}")
                _tp_off.add(self.path)
                _worlds.pop(self.path, None)
                try:
                    w.close(handle)
                except Exception:  # noqa: BLE001
                    pass
        # the engine's kernels go on the default stream: whatever torch has queued on its own must have landed
        torch.cuda.synchronize()
        took = library().tf_h3_forward(handle, ctypes.byref(inputs))
        if took < 0:
            raise RuntimeError("TensorFold H3: the forward failed")
        return took, keep, sizes.shape[0]

    def run_two(self, w, handle, h, tables, line, cos, sin, slot, sizes, prefix, keep):
        """One pass on two GPUs: rank 1 takes the tiles from ``cut`` on (about half the rows), rank 0 the rest; ``h``
        is written only once both are done, so a failure leaves it as it was."""
        tiles = sizes.shape[0]
        clock = [time.perf_counter()]
        cum = torch.cumsum(sizes.to(torch.int64), 0)
        # half the rows each, less the rows the earlier passes of this shape moved to rank 1 (balance()): rank 0 also
        # holds the prefix tiles, whose queries see every tile. The output does not depend on the cut.
        key = (tiles, h.shape[0])
        moved = _moved.get(key, 0.0)
        cut = int(torch.searchsorted(cum, max(1, int(cum[-1]) // 2 - int(moved))).item()) + 1
        cut = max(1, min(cut, tiles - 1))
        tile_of = slot.to(torch.int64) // TILE
        mask = tile_of >= cut
        own = [torch.nonzero(~mask).flatten(), torch.nonzero(mask).flatten()]
        cuts = [0, cut, tiles]
        # peer to peer, sparse: the engine does each rank's q k v work in parts and sends a part while it computes the
        # next (STUDIO_TP_CHUNKS, default 4; 0 or 1: one part). It needs each rank's rows tile by tile (slot order);
        # the row order changes no bits (the kernels are row-wise and the int8 products exact).
        chunks = tp_chunks(w, keep)
        rows_at = [[0] * 9, [0] * 9]
        if chunks:
            own = [o[torch.argsort(slot.to(torch.int64)[o])] for o in own]
            host_sizes = sizes.tolist()
            for r in range(2):
                rows_at[r][:chunks + 1] = tp.chunk_rows(host_sizes, cuts[r], cuts[r + 1], chunks)
        hidden = h.shape[1]
        # rank 1's job: its rows and the pass's tables and tile sizes, in the mailbox
        head, base = w.head, w.base
        parts = [h.index_select(0, own[1]), tables, line.index_select(0, own[1]), cos.index_select(0, own[1]),
                 sin.index_select(0, own[1]), slot.index_select(0, own[1]), sizes]
        at = tp.HEADER
        offsets = []
        for part in parts:
            offsets.append(at)
            n = part.numel()
            w.view(at, part.dtype, n).copy_(part.reshape(-1))
            at = (at + n * part.element_size() + 4095) // 4096 * 4096
        if at > tp.BOX_BYTES:
            raise RuntimeError(f"a pass of {at >> 20} MB does not fit the mailbox")
        for i, offset in enumerate(offsets):
            head.at[i] = offset
        head.rows, head.lines, head.rot, head.tiles = own[1].numel(), tables.shape[1], cos.shape[1], tiles
        head.prefix_tiles, head.keep, head.blocks, head.tile0, head.tile1 = prefix, keep, 0, cut, tiles
        for i in range(9):
            head.cuts[i] = cuts[i] if i < 3 else 0
            head.chunk_rows[i] = rows_at[1][i]
        head.chunks = chunks
        job = (head.job + 1) & 0xffffffff
        head.job = job
        clock.append(time.perf_counter())  # rank 1's job in the mailbox
        # rank 0's half here, at the same time
        mine = [h.index_select(0, own[0]).contiguous(), line.index_select(0, own[0]).contiguous(),
                cos.index_select(0, own[0]).contiguous(), sin.index_select(0, own[0]).contiguous(),
                slot.index_select(0, own[0]).contiguous()]
        inputs = Inputs(x=mine[0].data_ptr(), tables=tables.data_ptr(), line=mine[1].data_ptr(), cos=mine[2].data_ptr(),
                        sin=mine[3].data_ptr(), slot=mine[4].data_ptr(), sizes=sizes.data_ptr(), rows=own[0].numel(),
                        lines=tables.shape[1], rot=cos.shape[1], tiles=tiles, prefix_tiles=prefix, keep=keep, blocks=0,
                        attention_out=0, tile0=0, tile1=cut)
        for i in range(9):
            inputs.cuts[i] = cuts[i] if i < 3 else 0
            inputs.chunk_rows[i] = rows_at[0][i]
        inputs.chunks = chunks
        torch.cuda.synchronize()
        clock.append(time.perf_counter())  # rank 0's rows ready
        took = library().tf_h3_forward(handle, ctypes.byref(inputs))
        mark = time.perf_counter()
        clock.append(mark)  # rank 0's forward done
        while head.done != job:
            if w.proc.poll() is not None:
                raise RuntimeError(f"rank 1 exited ({w.proc.returncode}): {head.message.decode(errors='replace')}")
            if time.perf_counter() - mark > 120:
                raise RuntimeError("rank 1 did not finish the pass in 120 s")
            time.sleep(0.0005)
        clock.append(time.perf_counter())  # rank 1 done
        if took < 0 or head.status < 0:
            raise RuntimeError(f"the forward failed: rank 0 {took:.2f}, rank 1 {head.message.decode(errors='replace')}")
        theirs = w.view(offsets[0], torch.bfloat16, own[1].numel() * hidden).view(-1, hidden).to(h.device)
        h.index_copy_(0, own[0], mine[0])
        h.index_copy_(0, own[1], theirs)
        mine_x, theirs_x = tp.stats(library(), handle), tuple(head.tp_stats)
        balance(key, moved, mine_x, theirs_x, head.took, own[1].numel(), h.shape[0])
        if os.environ.get("TF_H3_PROFILE") == "1" or self.passes == 0:
            torch.cuda.synchronize()
            clock.append(time.perf_counter())  # h written back
            d = [b - a for a, b in zip(clock, clock[1:])]
            line_ = (f"TensorFold H3 two-GPU pass {self.passes + 1}: {clock[-1] - clock[0]:.2f} s = mailbox {d[0]:.3f} "
                     f"+ own rows {d[1]:.3f} + rank 0 forward {d[2]:.2f} + waiting for rank 1 {d[3]:.3f} "
                     f"+ write back {d[4]:.3f}; exchanges rank 0 {mine_x[0]:.2f} s in {int(mine_x[1])} "
                     f"(packing {mine_x[2]:.2f}), rank 1 {theirs_x[0]:.2f} s in {int(theirs_x[1])} (packing {theirs_x[2]:.2f}); "
                     f"rank 1 forward {head.took:.2f} s; cut at tile {cut}, {moved:.0f} rows moved to rank 1; {chunks or 1} parts")
            if head.spent_n:
                line_ += f"; rank 1 stages: {tp.spent_line(head.spent, head.spent_n)}"
            logging.info(line_)
        if self.passes == 0:
            logging.info(f"TensorFold H3: two GPUs, tiles 0-{cut - 1} ({own[0].numel()} rows) here and {cut}-{tiles - 1} "
                         f"({own[1].numel()} rows) on rank 1; rank 0 {took:.2f} s, rank 1 {head.took:.2f} s")
        return took

    def check(self, args):
        """The engine against ComfyUI's own blocks on this pass's stream: after one block and after all of them."""
        if self.patches:
            logging.info("TensorFold H3 check: ComfyUI's blocks run without the LoRA here (the engine holds it), "
                         "so the figures below include the LoRA's own effect")
        start = args["img"].clone()
        options = args["transformer_options"]

        def reference(count):
            h = start.clone()
            for block in self.model.blocks[:count]:
                h = block(h, args["t_emb"], args["mod_segments"], args["rope_freqs"], transformer_options=options)
            return h

        for count in (1, len(self.model.blocks)):
            want = reference(count)
            for label, dense in (("dense", True), ("sparse", False)):
                if not dense and self.sparsity <= 0:
                    continue
                got = start.clone()
                self.run({**args, "img": got}, blocks=count, dense=dense)
                error = float((got.float() - want.float()).norm() / want.float().norm())
                logging.info(f"TensorFold H3 check: {count} block(s), engine {label} against ComfyUI dense: "
                             f"cosine {cosine(got, want):.5f}, relative error {error:.4f}")
            del want

    def sources(self, args):
        """TF_H3_CHECK=2: which rounding costs what. ComfyUI's own block with one of the engine's int8 roundings
        emulated at a time, against the same block untouched; errors are relative to what the block adds."""
        import comfy.ops

        options = args["transformer_options"]

        def rows(x, group=0):
            # int8 with a scale a row (or a row and `group` columns), as the engine rounds activations
            shape = x.shape
            y = x.float().reshape(shape[0], -1, group) if group else x.float().reshape(shape[0], 1, -1)
            top = y.abs().amax(dim=-1, keepdim=True).clamp_min(1e-12)
            return ((y * (127.0 / top)).round().clamp(-127, 127) * (top / 127.0)).reshape(shape).to(x.dtype)

        def split(x, count=128):
            # the same, with the `count` channels that peak highest rounded on a scale of their own
            y = x.float()
            peaks = y.abs().amax(dim=0)
            chosen = torch.zeros_like(peaks, dtype=torch.bool)
            chosen[torch.topk(peaks, count).indices] = True
            out = torch.empty_like(y)
            out[:, chosen] = rows(y[:, chosen])
            out[:, ~chosen] = rows(y[:, ~chosen])
            crests.append(float((y.abs().amax(dim=1) / y.pow(2).mean(dim=1).sqrt()).mean()))
            crests.append(float((y[:, ~chosen].abs().amax(dim=1) / y[:, ~chosen].pow(2).mean(dim=1).sqrt()).mean()))
            return out.to(x.dtype)

        crests = []

        def wrap_input(module, quant):
            original = module.forward
            module.forward = lambda x, *a, **k: original(quant(x), *a, **k)
            return lambda: setattr(module, "forward", original)

        # all 50 blocks with the activations' rounding emulated, a scale a row against 128 channels apart
        def through(quant):
            undo = []
            if quant is not None:
                for block in self.model.blocks:
                    for module in (block.attn.qkv_proj, block.attn.out_proj, block.mlp.fc1):
                        undo.append(wrap_input(module, quant))
            x = args["img"].clone()
            for block in self.model.blocks:
                x = block(x, args["t_emb"], args["mod_segments"], args["rope_freqs"], transformer_options=options)
            for u in undo:
                u()
            return x

        want = through(None)
        def clipped(x, sigmas, count=0):
            # a scale a row that reaches `sigmas` times the row's rms (never past its largest); larger values saturate
            y = x.float()
            chosen = torch.zeros(y.shape[1], dtype=torch.bool, device=y.device)
            if count:
                chosen[torch.topk(y.abs().amax(dim=0), count).indices] = True
            out = torch.empty_like(y)
            for part in ((~chosen,) if not count else (chosen, ~chosen)):
                z = y[:, part]
                top = torch.minimum(z.abs().amax(dim=1, keepdim=True), sigmas * z.pow(2).mean(dim=1, keepdim=True).sqrt()).clamp_min(1e-12)
                out[:, part] = (z * (127.0 / top)).round().clamp(-127, 127) * (top / 127.0)
            return out.to(x.dtype)

        for label, quant in (("a scale a row", rows), ("clipped at 8 rms", lambda x: clipped(x, 8.0)), ("clipped at 6 rms", lambda x: clipped(x, 6.0)),
                             ("clipped at 5 rms", lambda x: clipped(x, 5.0)), ("clipped at 4 rms", lambda x: clipped(x, 4.0)),
                             ("128 apart, clipped at 6 rms", lambda x: clipped(x, 6.0, 128)), ("128 apart, clipped at 5 rms", lambda x: clipped(x, 5.0, 128)),
                             ("128 apart, clipped at 4 rms", lambda x: clipped(x, 4.0, 128))):
            got = through(quant)
            logging.info(f"TensorFold H3 sources: 50 blocks, activations rounded with {label}: cosine {cosine(got, want):.5f}, "
                         f"relative error {float((got.float() - want.float()).norm() / want.float().norm()):.4f}")
            del got
        got = args["img"].clone()
        self.run({**args, "img": got}, dense=True)
        logging.info(f"TensorFold H3 sources: 50 blocks, the engine: cosine {cosine(got, want):.5f}, "
                     f"relative error {float((got.float() - want.float()).norm() / want.float().norm()):.4f}")
        del want, got
        crests.clear()
        for index in ():
            block = self.model.blocks[index]
            # the stream as this block meets it
            x = args["img"].clone()
            for before in self.model.blocks[:index]:
                x = before(x, args["t_emb"], args["mod_segments"], args["rope_freqs"], transformer_options=options)
            want = block(x.clone(), args["t_emb"], args["mod_segments"], args["rope_freqs"], transformer_options=options)
            added = float((want.float() - x.float()).norm())
            xf = x.float()
            crest = float((xf.abs().amax(dim=1) / xf.pow(2).mean(dim=1).sqrt()).mean())
            peaks = xf.abs().amax(dim=0)
            top = torch.topk(peaks, 64).values
            report = [f"block {index}: stream crest {crest:.1f} (a row's largest over its rms), the 64 largest channels peak at "
                      f"{float(top.min()):.1f} to {float(top.max()):.1f} against a median channel peak of {float(peaks.median()):.2f}"]
            tests = {"q k v input rows": [(block.attn.qkv_proj, rows)], "attention out input rows": [(block.attn.out_proj, rows)],
                     "mlp input rows": [(block.mlp.fc1, rows)], "all three": [(block.attn.qkv_proj, rows), (block.attn.out_proj, rows), (block.mlp.fc1, rows)],
                     "all three, 128 channels apart": [(block.attn.qkv_proj, split), (block.attn.out_proj, split), (block.mlp.fc1, split)],
                     "all three, 32 apart": [(block.attn.qkv_proj, lambda x: split(x, 32)), (block.attn.out_proj, lambda x: split(x, 32)), (block.mlp.fc1, lambda x: split(x, 32))]}
            for label, wraps in tests.items():
                undo = [wrap_input(module, quant) for module, quant in wraps]
                got = block(x.clone(), args["t_emb"], args["mod_segments"], args["rope_freqs"], transformer_options=options)
                for u in undo:
                    u()
                report.append(f"{label} {float((got.float() - want.float()).norm()) / added:.4f}")
            got = x.clone()
            self.run({**args, "img": got}, blocks=1, dense=True) if index == 0 else None
            if index == 0:
                report.append(f"the engine's whole block {float((got.float() - want.float()).norm()) / added:.4f}")
            report.append("crest of q k v / attention out / mlp inputs, whole then without the 128: " + " ".join(f"{c:.1f}" for c in crests[:6]))
            crests.clear()
            logging.info("TensorFold H3 sources: " + "; ".join(report))
            del want, x

    def first(self, args, extra):
        if os.environ.get("TF_H3_CHECK") == "2" and not self.checked:
            self.checked = True
            self.sources(args)
        if os.environ.get("TF_H3_CHECK") == "1" and not self.checked:
            self.checked = True
            self.check(args)
        profile = os.environ.get("TF_H3_PROFILE") == "1"
        handle = engine(self.path)
        if profile:
            library().tf_h3_profile(handle, 1)
        mark = time.perf_counter()
        took, keep, tiles = self.run(args)
        self.passes += 1
        if self.passes == 1:
            mode = f"{keep} video tiles kept a query tile" if keep else "dense attention"
            logging.info(f"TensorFold H3: {args['img'].shape[0]} rows in {tiles} tiles, {mode}, first pass {took:.2f} s")
        if profile:
            spent, names = (ctypes.c_double * 16)(), (ctypes.c_char_p * 16)()
            n = library().tf_h3_spent(handle, ctypes.byref(spent), ctypes.byref(names))
            parts = ", ".join(f"{names[i].decode()} {spent[i]:.2f}" for i in range(n))
            logging.info(f"TensorFold H3 pass {self.passes}: {time.perf_counter() - mark:.2f} s ({parts})")
            library().tf_h3_profile(handle, 0)
        return {"img": args["img"]}


def passthrough(args, extra):
    return {"img": args["img"]}


class TensorFoldH3Blocks:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "unet_name": (folder_paths.get_filename_list("diffusion_models"), {"tooltip": "The FastH3 bf16 checkpoint the model was loaded from."}),
            "sparsity": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 0.99, "step": 0.05,
                                   "tooltip": "FastH3's sparse attention: the share of video tiles a tile leaves out. 0 is dense."}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "patch"
    CATEGORY = "model/patch"

    def patch(self, model, unet_name, sparsity):
        diffusion_model = model.get_model_object("diffusion_model")
        if not isinstance(diffusion_model, MiniMaxH3Model):
            raise ValueError("TensorFold H3 needs a MiniMax H3 / FastH3 model")
        path = folder_paths.get_full_path_or_raise("diffusion_models", unet_name)
        m = model.clone()
        state = Blocks(diffusion_model, path, sparsity, take_block_patches(m))
        if state.patches:
            # what ComfyUI patches differs now from the model it came from: its loaded copy must not count as this one
            m.patches_uuid = uuid.uuid4()
            logging.info(f"TensorFold H3: {len(state.patches)} projections carry a LoRA; the engine merges it")
        for i in range(len(diffusion_model.blocks)):
            m.set_model_patch_replace(state.first if i == 0 else passthrough, "dit", "double_block", i)

        def no_prefetch(executor, x, timestep, context, transformer_options={}, **kwargs):
            # ComfyUI would stream its own copy of every block's weights onto the GPU ahead of the block
            transformer_options["prefetch_dynamic_vbars"] = False
            return executor(x, timestep, context, transformer_options, **kwargs)

        m.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, "tensorfold_h3", no_prefetch)

        def reset(model_patcher):
            state.checked = False
            state.passes = 0

        m.add_callback_with_key(comfy.patcher_extension.CallbacksMP.ON_CLEANUP, "tensorfold_h3", reset)
        return (m,)


NODE_CLASS_MAPPINGS = {"TensorFoldH3Blocks": TensorFoldH3Blocks}
NODE_DISPLAY_NAME_MAPPINGS = {"TensorFoldH3Blocks": "TensorFold H3 Blocks (CUDA int8)"}

try:  # the two-card video decode (decode2.py): a failure there must not take the engine's node with it
    from . import decode2
    NODE_CLASS_MAPPINGS.update(decode2.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(decode2.NODE_DISPLAY_NAME_MAPPINGS)
except Exception as error:  # noqa: BLE001
    logging.warning(f"Studio two-card decode unavailable: {error}")
