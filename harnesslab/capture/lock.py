"""Single writer for data/runs/captured/.

The capture backfill and a manual `POST /api/import` into `captured` must never write the same run
at once (spec §5.7). Both take an exclusive, non-blocking flock on `captured/.lock`; the loser fails
fast with CaptureLocked instead of waiting or interleaving writes.
"""
from __future__ import annotations

import fcntl
import os


class CaptureLocked(RuntimeError):
    pass


class CaptureLock:
    def __init__(self, runs_root: str, name: str = ""):
        """`name` selects WHICH lock. The default is the capture lock every writer contends for.

        The sniffer holds `sniffer` for its whole life, so a second sniffer cannot start, while
        taking the capture lock only for the length of a batch -- holding that one for hours would
        block every manual import on the machine.
        """
        leaf = f".{name}.lock" if name else ".lock"
        self.path = os.path.join(runs_root, "captured", leaf)
        self._fd = None

    def __enter__(self) -> "CaptureLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:              # contention, or a mount with no locking (NFS, some containers)
            os.close(fd)                  # never leak the descriptor, whichever it was
            raise CaptureLocked(f"another capture writer holds {self.path}" if isinstance(e, BlockingIOError)
                                else f"cannot lock {self.path}: {type(e).__name__}: {e}") from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return self

    def __exit__(self, *exc) -> bool:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        return False
