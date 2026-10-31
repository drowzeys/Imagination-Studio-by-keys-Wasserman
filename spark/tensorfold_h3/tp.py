"""One pass of FastH3's blocks split over two GPUs in this machine (TensorFold's sequence-parallel H3, shared memory).

Rank 0 is ComfyUI's own engine on the first card (the TensorFoldH3Blocks node); rank 1 is a process of its own on the
second card (tp_rank.py, CUDA_VISIBLE_DEVICES set to it) holding the 21 GB int8 copy of the blocks. Each pass the
node splits the stream by tile: rank r runs the rows of tiles [cuts[r], cuts[r + 1]). Rank 1's rows and the pass's
tables go to it through the mailbox (POSIX shared memory "<name>.box"); during the pass the two engines exchange
their tiles' keys and values every block through the shared exchange region ("<name>", engine device "shm:<name>").
The kernels are the one-GPU kernels, so the pass gives the bits of one GPU (TensorFold's receipt: N ranks against one,
identical streams).

Environment: STUDIO_TP (auto: on with two cards and spark/gpus.py's split; off), STUDIO_TP_GPU (the second card's
CUDA index, default 1), STUDIO_TP_REGION_GB (the exchange region, default 4).
"""

import ctypes
import mmap
import os

MAGIC = 0x48335450  # "H3TP"
BOX_BYTES = 6 << 30  # sparse: only what a pass writes is ever backed
HEADER = 4096


class Inputs(ctypes.Structure):
    """The engine's Model.Inputs (TensorFold h3-cuda-parallel): the one-GPU fields, then this rank's tile range."""
    _fields_ = [("x", ctypes.c_uint64), ("tables", ctypes.c_uint64), ("line", ctypes.c_uint64), ("cos", ctypes.c_uint64),
                ("sin", ctypes.c_uint64), ("slot", ctypes.c_uint64), ("sizes", ctypes.c_uint64), ("rows", ctypes.c_uint32),
                ("lines", ctypes.c_uint32), ("rot", ctypes.c_uint32), ("tiles", ctypes.c_uint32),
                ("prefix_tiles", ctypes.c_uint32), ("keep", ctypes.c_uint32), ("blocks", ctypes.c_uint32),
                ("attention_out", ctypes.c_uint64), ("tile0", ctypes.c_uint32), ("tile1", ctypes.c_uint32),
                ("cuts", ctypes.c_uint32 * 9),
                # peer to peer (engine tf_h3_tp_chunks): the q k v work in parts, rows tile by tile
                ("chunks", ctypes.c_uint32), ("chunk_rows", ctypes.c_uint32 * 9)]


class Header(ctypes.Structure):
    """The mailbox's first page. Rank 0 fills a job and raises ``job``; the rank answers in ``done`` and ``status``."""
    _fields_ = [("magic", ctypes.c_uint32), ("ready", ctypes.c_uint32), ("job", ctypes.c_uint32), ("done", ctypes.c_uint32),
                ("status", ctypes.c_int32), ("stop", ctypes.c_uint32), ("took", ctypes.c_double),
                ("rows", ctypes.c_uint32), ("lines", ctypes.c_uint32), ("rot", ctypes.c_uint32), ("tiles", ctypes.c_uint32),
                ("prefix_tiles", ctypes.c_uint32), ("keep", ctypes.c_uint32), ("blocks", ctypes.c_uint32),
                ("tile0", ctypes.c_uint32), ("tile1", ctypes.c_uint32), ("cuts", ctypes.c_uint32 * 9),
                # byte offsets in the mailbox of x, tables, line, cos, sin, slot, sizes
                ("at", ctypes.c_uint64 * 7), ("message", ctypes.c_char * 512),
                # TF_H3_PROFILE=1: rank 1's last pass, as tf_h3_spent and tf_h3_tp_stats report it
                ("spent", ctypes.c_double * 16), ("spent_n", ctypes.c_uint32), ("tp_stats", ctypes.c_double * 3),
                # Inputs.chunks and chunk_rows for rank 1's rows
                ("chunks", ctypes.c_uint32), ("chunk_rows", ctypes.c_uint32 * 9)]


def open_box(name, create):
    path = "/dev/shm/" + name.lstrip("/") + ".box"
    fd = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0), 0o600)
    try:
        if create:
            os.ftruncate(fd, BOX_BYTES)
        box = mmap.mmap(fd, BOX_BYTES, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
    finally:
        os.close(fd)
    return box


def address(box):
    return ctypes.addressof(ctypes.c_char.from_buffer(box))


def sweep():
    """The worlds of ComfyUI servers that are gone (killed before they could close theirs): their shared memory is
    RAM held until it is unlinked. A world's name carries its server's pid (tf-h3-<pid>-<id>)."""
    try:
        names = os.listdir("/dev/shm")
    except OSError:
        return
    for name in names:
        parts = name.split("-")
        if len(parts) >= 4 and parts[0] == "tf" and parts[1] == "h3" and parts[2].isdigit():
            try:
                os.kill(int(parts[2]), 0)
            except ProcessLookupError:
                try:
                    os.unlink("/dev/shm/" + name)
                except OSError:
                    pass
            except PermissionError:
                pass


def unlink(name):
    base = "/dev/shm/" + name.lstrip("/")
    for path in (base, base + ".ctl", base + ".p2p", base + ".box"):
        try:
            os.unlink(path)
        except OSError:
            pass


def connect(lib, model, world, rank):
    """Every peer, connected: over shared memory there is nothing to hand over (a zero record does)."""
    record = (ctypes.c_uint8 * 72)()
    return all(lib.tf_h3_tp_connect(model, peer, ctypes.byref(record)) == 0 for peer in range(world) if peer != rank)


def bind(lib):
    """The engine's calls this code uses, typed."""
    lib.tf_h3_open.restype = ctypes.c_void_p
    lib.tf_h3_open.argtypes = [ctypes.c_char_p]
    lib.tf_h3_forward.restype = ctypes.c_double
    lib.tf_h3_forward.argtypes = [ctypes.c_void_p, ctypes.POINTER(Inputs)]
    lib.tf_h3_forward_host.restype = ctypes.c_double
    lib.tf_h3_forward_host.argtypes = [ctypes.c_void_p, ctypes.POINTER(Inputs)]
    lib.tf_h3_tp_init.restype = ctypes.c_int
    lib.tf_h3_tp_init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint64]
    lib.tf_h3_tp_close.argtypes = [ctypes.c_void_p]
    lib.tf_h3_tp_connect.restype = ctypes.c_int
    lib.tf_h3_tp_connect.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint8 * 72)]
    lib.tf_h3_tp_stats.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double * 3)]
    if hasattr(lib, "tf_h3_tp_p2p"):
        lib.tf_h3_tp_p2p.restype = ctypes.c_int
    if hasattr(lib, "tf_h3_tp_chunks"):
        lib.tf_h3_tp_chunks.restype = ctypes.c_int
    if hasattr(lib, "tf_h3_profile"):
        lib.tf_h3_profile.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.tf_h3_spent.restype = ctypes.c_int
        lib.tf_h3_spent.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    return lib


def stats(lib, model):
    """(seconds in the last forward's exchanges, how many, seconds of those packing) for this rank."""
    out = (ctypes.c_double * 3)()
    lib.tf_h3_tp_stats(model, ctypes.byref(out))
    return tuple(out)


STAGES = ("norms", "q k v gate", "heads", "routing", "attention", "gate mix", "attention out", "mlp in", "quantize",
          "mlp out", "exchange")


def spent_line(values, n, names=STAGES):
    return ", ".join(f"{names[i] if i < len(names) else i} {values[i]:.2f}" for i in range(n))


def chunk_rows(sizes, lo, hi, chunks):
    """Inputs.chunk_rows for a rank owning tiles [lo, hi) whose rows come tile by tile: the first row of each of the
    `chunks` parts the engine cuts the range into (lo + n * c // chunks), then the rank's row count."""
    n = hi - lo
    ends = [0]
    for tile in sizes[lo:hi]:
        ends.append(ends[-1] + int(tile))
    return [ends[n * c // chunks] for c in range(chunks + 1)]
