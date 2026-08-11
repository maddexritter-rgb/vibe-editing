#!/usr/bin/env python3
"""Brand ENCODE GATE — MACHINE-WIDE semaphore for video encode jobs (locked 2026-06-06).

Why this exists: parallel.py caps concurrent encodes PER CALL. But if 6 Claude sessions
are each running their own batches, every session honors "cap=3" while the machine sees
6×3 = 18 ffmpegs fighting over the M3 Max's 2 hardware media engines. Result: severe
contention, every session feels "slow", total throughput drops.

This module gives the WHOLE MACHINE a single semaphore with N slots. Every encode job —
regardless of which session, which skill, which Python process spawned it — has to
acquire a slot before running. Implementation = POSIX `flock` on N tiny lockfiles in
/tmp/acq_encode_slots/. Works across processes, no daemon needed, survives crashes
(stale locks auto-release on process exit because flock is per-fd).

Default cap = 3 (2 media engines + 1 in-flight for IO/setup overlap). Override:
    VIBE_ENCODE_SLOTS=4   # raise the cap (test before trusting)
    VIBE_ENCODE_SLOTS=1   # serialize completely (debugging)

Use it as a context manager around ffmpeg calls:

    from encode_gate import gate
    with gate():
        subprocess.run(ffmpeg_cmd, check=True)

Inside parallel.run_commands(kind="encode") it's already wired — most skills get it free.
"""
import tempfile
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

# How many concurrent hardware encodes the machine tolerates well.
# M3 Max has 2 media engines; 3 = both pinned + 1 ready to swap in.
SLOTS = int(os.environ.get("VIBE_ENCODE_SLOTS", "3"))

# Lock dir lives in /tmp so it's automatically cleaned on reboot and writable by every user.
SLOT_DIR = Path(os.environ.get("VIBE_ENCODE_SLOT_DIR") or os.path.join(tempfile.gettempdir(), "acq_encode_slots"))


def _ensure_slots():
    SLOT_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(SLOTS):
        f = SLOT_DIR / f"slot_{i}"
        if not f.exists():
            f.touch()
    # Allow the dir to grow if SLOTS was raised, but don't delete existing slot files.


@contextmanager
def gate(timeout: float | None = None, poll: float = 0.25):
    """Acquire one of SLOTS encode slots, blocking until one frees.

    timeout: max seconds to wait for a slot. None = wait forever (default).
    poll: how often to retry while all slots are taken.

    Releases the slot automatically when the `with` block exits, even on exception.
    """
    _ensure_slots()
    start = time.monotonic()
    last_log = start
    fds = []  # we open all slot files once, lock the first one we can

    try:
        # Open every slot file once up-front; we'll try each non-blocking lock per pass.
        for i in range(SLOTS):
            fd = os.open(str(SLOT_DIR / f"slot_{i}"), os.O_RDWR)
            fds.append(fd)

        while True:
            for fd in fds:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    # Got a slot. Close the other fds so they don't leak.
                    held_fd = fd
                    for other in fds:
                        if other is not held_fd:
                            os.close(other)
                    fds = [held_fd]
                    try:
                        yield
                    finally:
                        fcntl.flock(held_fd, fcntl.LOCK_UN)
                        os.close(held_fd)
                        fds = []
                    return
                except BlockingIOError:
                    continue  # try the next slot

            if timeout is not None and (time.monotonic() - start) > timeout:
                raise TimeoutError(f"encode_gate: no slot in {timeout}s (cap={SLOTS})")
            # Optional gentle stderr breadcrumb every ~30s so a stuck queue is visible.
            now = time.monotonic()
            if now - last_log > 30:
                try:
                    import sys
                    print(f"[encode_gate] waiting for a slot (cap={SLOTS}, waited {int(now-start)}s)", file=sys.stderr)
                except Exception:
                    pass
                last_log = now
            time.sleep(poll)
    finally:
        # Defensive close on any error path.
        for fd in fds:
            try:
                os.close(fd)
            except Exception:
                pass


def stats():
    """Quick snapshot of slot state: how many free vs busy."""
    _ensure_slots()
    free = 0
    busy = 0
    for i in range(SLOTS):
        fd = os.open(str(SLOT_DIR / f"slot_{i}"), os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                free += 1
            except BlockingIOError:
                busy += 1
        finally:
            os.close(fd)
    return {"cap": SLOTS, "free": free, "busy": busy}


if __name__ == "__main__":
    import json
    print(json.dumps(stats(), indent=2))
