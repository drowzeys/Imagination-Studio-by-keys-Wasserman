"""Test rig for the two-GPU H3 engine: replay one captured pass outside ComfyUI on one GPU and on two (each exchange),
the first N blocks at a time, and say which rows differ from the one-GPU pass and where.

    TF_H3_CAPTURE=/tmp/pass.pt  (set on the Studio, then render one clip: the first pass's inputs are saved)
    CUDA_VISIBLE_DEVICES=0,1 python tests/tf_h3_tp_rig.py /tmp/pass.pt --lib .../libtf_h3.so --blocks 1,2,50 --modes shm,p2p

Device 0 of this process is rank 0's card, device 1 rank 1's (tp_rank.py, a process of its own as in the Studio).
"""
import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent.parent / "spark" / "tensorfold_h3"
sys.path.insert(0, str(HERE))
import tp  # noqa: E402

TILE = 64


def load(lib_path):
    lib = tp.bind(ctypes.CDLL(lib_path))
    lib.tf_h3_close.argtypes = [ctypes.c_void_p]
    lib.tf_h3_shape.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32 * 7)]
    return lib


def inputs_for(t, h, rows, blocks, line, cos, sin, slot, tile0=0, tile1=0, cuts=None):
    i = tp.Inputs(x=h.data_ptr(), tables=t["tables"].data_ptr(), line=line.data_ptr(), cos=cos.data_ptr(),
                  sin=sin.data_ptr(), slot=slot.data_ptr(), sizes=t["sizes"].data_ptr(), rows=rows,
                  lines=t["lines"], rot=t["rot"], tiles=t["sizes"].shape[0], prefix_tiles=t["prefix"], keep=t["keep"],
                  blocks=blocks, attention_out=0, tile0=tile0, tile1=tile1)
    for k in range(9):
        i.cuts[k] = (cuts[k] if cuts and k < len(cuts) else 0)
    return i


def single(lib, handle, t, blocks):
    h = t["h"].clone()
    i = inputs_for(t, h, h.shape[0], blocks, t["line"], t["cos"], t["sin"], t["slot"])
    torch.cuda.synchronize()
    took = lib.tf_h3_forward(handle, ctypes.byref(i))
    if took < 0:
        raise RuntimeError("one-GPU forward failed")
    torch.cuda.synchronize()
    return h.cpu(), took


class World:
    def __init__(self, lib, handle, path, kind, region_gb=4.0):
        self.lib, self.handle, self.kind = lib, handle, kind
        self.name = f"tf-h3-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.region = int(region_gb * (1 << 30))
        self.box = tp.open_box(self.name, create=True)
        self.head = tp.Header.from_buffer(self.box)
        self.head.magic = tp.MAGIC
        visible = [p.strip() for p in os.environ.get("CUDA_VISIBLE_DEVICES", "0,1").split(",")]
        cards = f"{visible[1]},{visible[0]}" if kind == "p2p" else visible[1]
        cards = os.environ.get("RIG_RANK1_CARDS", cards)
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": cards, "TF_H3_LIB": lib._name}
        libc = ctypes.CDLL(None)
        self.proc = subprocess.Popen([sys.executable, str(HERE / "tp_rank.py"), self.name, path + ".tf-int8",
                                      str(self.region), "2", "1", kind], env=env,
                                     preexec_fn=lambda: libc.prctl(1, signal.SIGTERM))
        if lib.tf_h3_tp_init(handle, f"{kind}:/{self.name}".encode(), 0, 2, 0, self.region) != 0:
            raise RuntimeError(f"tf_h3_tp_init {kind} failed")
        mark = time.perf_counter()
        while self.head.ready == 0:
            if self.proc.poll() is not None or time.perf_counter() - mark > 600:
                raise RuntimeError(f"rank 1 did not start: {self.head.message.decode(errors='replace')}")
            time.sleep(0.05)
        if self.head.ready != 1:
            raise RuntimeError(self.head.message.decode(errors="replace"))
        if not tp.connect(lib, handle, 2, 0):
            raise RuntimeError("connect failed")
        print(f"  world {kind}: rank 1 on CUDA_VISIBLE_DEVICES={cards} ({self.head.message.decode(errors='replace')})", flush=True)

    def view(self, offset, dtype, count):
        return torch.frombuffer(self.box, dtype=dtype, count=count, offset=offset)

    def run(self, t, blocks):
        """run_two of spark/tensorfold_h3, the first `blocks` blocks (0: all)."""
        h0 = t["h"].clone()
        sizes, slot = t["sizes"], t["slot"]
        tiles = sizes.shape[0]
        cum = torch.cumsum(sizes.to(torch.int64), 0)
        cut = int(torch.searchsorted(cum, cum[-1] // 2).item()) + 1 + int(os.environ.get("RIG_CUT_SHIFT", "0"))
        cut = max(1, min(cut, tiles - 1))  # RIG_CUT_SHIFT: move the split (output must not depend on it)
        mask = slot.to(torch.int64) // TILE >= cut
        own = [torch.nonzero(~mask).flatten(), torch.nonzero(mask).flatten()]
        chunks = int(os.environ.get("RIG_CHUNKS", "0")) if self.kind == "p2p" and t["keep"] > 0 else 0
        rows_at = [[0] * 9, [0] * 9]
        if chunks >= 2 or os.environ.get("RIG_SORT_ROWS", "0") == "1":  # each rank's rows tile by tile (slot order)
            own = [o[torch.argsort(slot.to(torch.int64)[o])] for o in own]
        if chunks >= 2:
            for r in range(2):
                rows_at[r][:chunks + 1] = tp.chunk_rows(sizes.tolist(), [0, cut, tiles][r], [0, cut, tiles][r + 1], chunks)
        else:
            chunks = 0
        cuts = [0, cut, tiles]
        hidden = h0.shape[1]
        head = self.head
        parts = [h0.index_select(0, own[1]), t["tables"], t["line"].index_select(0, own[1]), t["cos"].index_select(0, own[1]),
                 t["sin"].index_select(0, own[1]), slot.index_select(0, own[1]), sizes]
        at, offsets = tp.HEADER, []
        for part in parts:
            offsets.append(at)
            self.view(at, part.dtype, part.numel()).copy_(part.reshape(-1).cpu())
            at = (at + part.numel() * part.element_size() + 4095) // 4096 * 4096
        for k, off in enumerate(offsets):
            head.at[k] = off
        head.rows, head.lines, head.rot, head.tiles = own[1].numel(), t["lines"], t["rot"], tiles
        head.prefix_tiles, head.keep, head.blocks, head.tile0, head.tile1 = t["prefix"], t["keep"], blocks, cut, tiles
        for k in range(9):
            head.cuts[k] = cuts[k] if k < 3 else 0
            head.chunk_rows[k] = rows_at[1][k]
        head.chunks = chunks
        job = (head.job + 1) & 0xffffffff
        head.job = job
        mine = [h0.index_select(0, own[0]).contiguous(), t["line"].index_select(0, own[0]).contiguous(),
                t["cos"].index_select(0, own[0]).contiguous(), t["sin"].index_select(0, own[0]).contiguous(),
                slot.index_select(0, own[0]).contiguous()]
        i = inputs_for(t, mine[0], own[0].numel(), blocks, mine[1], mine[2], mine[3], mine[4], 0, cut, cuts)
        i.chunks = chunks
        for k in range(9):
            i.chunk_rows[k] = rows_at[0][k]
        torch.cuda.synchronize()
        took = self.lib.tf_h3_forward(self.handle, ctypes.byref(i))
        mark = time.perf_counter()
        while head.done != job:
            if self.proc.poll() is not None or time.perf_counter() - mark > 120:
                raise RuntimeError(f"rank 1: {head.message.decode(errors='replace')}")
            time.sleep(0.0005)
        if took < 0 or head.status < 0:
            raise RuntimeError(f"forward failed: rank 0 {took}, rank 1 {head.message.decode(errors='replace')}")
        theirs = self.view(offsets[0], torch.bfloat16, own[1].numel() * hidden).view(-1, hidden).to(h0.device)
        out = h0.clone()
        out.index_copy_(0, own[0], mine[0])
        out.index_copy_(0, own[1], theirs)
        torch.cuda.synchronize()
        return out.cpu(), own, (took, head.took), tp.stats(self.lib, self.handle), tuple(head.tp_stats)

    def close(self):
        self.head.stop = 1
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.lib.tf_h3_tp_close(self.handle)
        tp.unlink(self.name)


def compare(ref, got, own):
    same = torch.equal(ref, got)
    d = (ref.float() - got.float()).abs()
    rows_bad = (d.amax(dim=1) > 0)
    r0 = int(rows_bad[own[0].cpu()].sum()); r1 = int(rows_bad[own[1].cpu()].sum())
    cos = torch.nn.functional.cosine_similarity(ref.float().flatten(), got.float().flatten(), dim=0).item()
    return (f"identical={same} max|d|={d.max().item():.4g} cos={cos:.6f} rows differing: rank 0 {r0}/{own[0].numel()}, "
            f"rank 1 {r1}/{own[1].numel()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--lib", required=True)
    ap.add_argument("--blocks", default="1,2,50")
    ap.add_argument("--modes", default="shm,p2p")
    a = ap.parse_args()
    raw = torch.load(a.capture)
    t = {k: (v.cuda(0) if torch.is_tensor(v) else v) for k, v in raw.items()}
    print(f"pass: {t['h'].shape[0]} rows, {t['sizes'].shape[0]} tiles, prefix {t['prefix']}, keep {t['keep']}", flush=True)
    lib = load(a.lib)
    torch.cuda.set_device(0)
    handle = lib.tf_h3_open(t["path"].encode())
    if not handle:
        raise SystemExit(f"could not open {t['path']}")
    blocks = [int(b) for b in a.blocks.split(",")]
    refs = {}
    for n in blocks:
        r1, took = single(lib, handle, t, n)
        r2, _ = single(lib, handle, t, n)
        refs[n] = r1
        print(f"one GPU, {n} blocks: {took:.2f} s, deterministic={torch.equal(r1, r2)}", flush=True)
    for kind in a.modes.split(","):
        w = World(lib, handle, t["path"], kind)
        try:
            for n in blocks:
                out, own, took, x0, x1 = w.run(t, n)
                print(f"  {kind}, {n} blocks: rank 0 {took[0]:.2f} s, rank 1 {took[1]:.2f} s, exchange {x0[0]:.2f}/{x1[0]:.2f} s "
                      f"in {int(x0[1])}; {compare(refs[n], out, own)}", flush=True)
        finally:
            w.close()
    lib.tf_h3_close(handle)


if __name__ == "__main__":
    main()
