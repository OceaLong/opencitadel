"""Bounded private PG16 jsonlog append evidence. No install or logging changes."""

import itertools
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.pg_diagnostics_plan import query_identifier, read_plan

MAX_LOG_BYTES = 16 * 1024 * 1024


@dataclass
class LogRange:
    path: Path
    device: int
    inode: int
    offset: int
    tail: bytes

    @classmethod
    def start(cls, path):
        path = Path(path)
        if path.resolve() != path.absolute():
            raise ValueError("log symlink path")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("log is not a regular file")
            tail = os.pread(fd, min(info.st_size, 4096), max(0, info.st_size - 4096))
            if tail and not tail.endswith(b"\n"):
                raise ValueError("partial log line at start")
            return cls(path, info.st_dev, info.st_ino, info.st_size, tail)
        finally:
            os.close(fd)

    def finish(self, path, *, budget=None):
        if Path(path) != self.path:
            raise ValueError("log rotated")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            size = info.st_size - self.offset
            if (info.st_dev, info.st_ino) != (
                self.device,
                self.inode,
            ) or not 0 <= size <= MAX_LOG_BYTES:
                raise ValueError("log rotated/truncated/exceeds bounded range")
            if os.pread(fd, len(self.tail), self.offset - len(self.tail)) != self.tail:
                raise ValueError("log prefix changed")
            if budget is not None:
                budget.reserve(size * 64 + len(self.tail), rows=size, largest=size)
            raw = os.pread(fd, size, self.offset)
            if len(raw) != size or (raw and not raw.endswith(b"\n")):
                raise ValueError("partial log append")
            return raw
        finally:
            os.close(fd)


def parse_trace(raw, *, pid, session_id, txid, sql):
    """Outer plan terminates the trace; nested plans keep individual query IDs.

    No sum is made over nested executions: function recursion can include buffers
    already counted by an outer node. Unsupported parallel attribution fails closed.
    """
    if not raw or len(raw) > MAX_LOG_BYTES or not raw.endswith(b"\n"):
        raise ValueError("missing/partial bounded log trace")
    selected, plans = [], []
    for line in raw.splitlines():
        row = strict_json(line)
        if row.get("leader_pid") == pid:
            raise ValueError("parallel-worker trace attribution unavailable")
        if row.get("pid") != pid:
            continue
        if row.get("session_id") != session_id or row.get("txid") != txid:
            raise ValueError("log backend/session/transaction identity differs")
        if type(row.get("line_num")) is not int:
            raise ValueError("log sequence identity unavailable")
        selected.append(row)
        match = re.fullmatch(
            r"duration: ([0-9]+(?:\.[0-9]+)?) ms\s+plan:\n(.*)", row.get("message", ""), re.DOTALL
        )
        if match:
            document = strict_json(match[2].encode())
            document["Execution Time"] = float(match[1])
            plans.append((document, read_plan([document]), row))
    if not selected or any(
        b["line_num"] != a["line_num"] + 1 for a, b in itertools.pairwise(selected)
    ):
        raise ValueError("missing/noncontiguous backend trace")
    top = [p for p in plans if p[0].get("Query Text") == sql]
    if len(top) != 1 or not plans or top[0] is not plans[-1]:
        raise ValueError("missing/ambiguous terminal outer query plan")
    if query_identifier(top[0][2].get("query_id")) != top[0][1].query_id:
        raise ValueError("outer log query identifier differs")
    return top[0][1], [p[1] for p in plans[:-1]]
