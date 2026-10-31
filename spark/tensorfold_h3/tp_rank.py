"""Rank 1 of a two-GPU pass (tp.py): the second card's half of FastH3's blocks, as a process of its own.

    CUDA_VISIBLE_DEVICES=1 TF_H3_LIB=... python tp_rank.py NAME INT8_PATH REGION_BYTES WORLD RANK [shm|p2p]

Opens the int8 copy of the blocks on its card, joins the world over shared memory ("shm:/NAME"), says it is ready in
the mailbox and then runs every job rank 0 puts there until rank 0 asks it to stop or goes away.
"""

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tp  # noqa: E402


def main():
    name, path, region, world, rank = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
    kind = sys.argv[6] if len(sys.argv) > 6 else "shm"  # the exchange: shm (host shared memory) or p2p (GPU to GPU)
    parent = os.getppid()
    box = tp.open_box(name, create=False)
    head = tp.Header.from_buffer(box)
    base = tp.address(box)

    def say(status, message):
        head.message = message.encode()[:500]
        head.status = status

    lib = tp.bind(ctypes.CDLL(os.environ["TF_H3_LIB"]))
    mark = time.perf_counter()
    model = lib.tf_h3_open(path.encode())
    if not model:
        say(-1, f"rank {rank}: tf_h3_open {path} failed")
        head.ready = 2
        return 1
    if lib.tf_h3_tp_init(model, f"{kind}:/{name}".encode(), 0, world, rank, region) != 0 or not tp.connect(lib, model, world, rank):
        say(-1, f"rank {rank}: tf_h3_tp_init failed")
        head.ready = 2
        return 1
    say(0, f"rank {rank} ready in {time.perf_counter() - mark:.1f} s")
    head.ready = 1
    seen = head.job
    idle = 0
    while True:
        if head.stop or os.getppid() != parent:
            break
        if head.job == seen:
            idle += 1
            # a pass comes every few seconds: spin briefly after one, then rest
            if idle > 20000:
                time.sleep(0.001)
            continue
        idle = 0
        seen = head.job
        at = [base + head.at[i] for i in range(7)]
        inputs = tp.Inputs(x=at[0], tables=at[1], line=at[2], cos=at[3], sin=at[4], slot=at[5], sizes=at[6],
                           rows=head.rows, lines=head.lines, rot=head.rot, tiles=head.tiles,
                           prefix_tiles=head.prefix_tiles, keep=head.keep, blocks=head.blocks, attention_out=0,
                           tile0=head.tile0, tile1=head.tile1)
        for i in range(9):
            inputs.cuts[i] = head.cuts[i]
        inputs.chunks = head.chunks
        for i in range(9):
            inputs.chunk_rows[i] = head.chunk_rows[i]
        profile = os.environ.get("TF_H3_PROFILE") == "1" and hasattr(lib, "tf_h3_profile")
        if profile:
            lib.tf_h3_profile(model, 1)
        took = lib.tf_h3_forward_host(model, ctypes.byref(inputs))
        head.took = took
        if profile:
            spent, names = (ctypes.c_double * 16)(), (ctypes.c_char_p * 16)()
            head.spent_n = lib.tf_h3_spent(model, ctypes.byref(spent), ctypes.byref(names))
            for i in range(16):
                head.spent[i] = spent[i]
            lib.tf_h3_profile(model, 0)
        for i, value in enumerate(tp.stats(lib, model)):
            head.tp_stats[i] = value
        say(0 if took >= 0 else -1, "" if took >= 0 else f"rank {rank}: the forward failed")
        head.done = seen
    lib.tf_h3_tp_close(model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
