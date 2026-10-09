"""Fail-closed checkpoint retention ceilings; not a full capacity/RSS claim.

InventoryQueries preflights server-encoded row sizes before typed transfer.
These limits bound encoded operands, not whole-process or server RSS.
"""

import json
import threading

from scripts.execution_capacity.guest_seal_entry import parse_evidence_limits  # noqa: F401

ROW_BYTES = 1024 * 1024
PHASE_BYTES = 16 * 1024 * 1024
PHASE_ROWS = 16_384


class EvidenceQuotaError(ValueError):
    pass


class _WorkspaceLease:
    def __init__(self, budget, size, rows):
        self._budget, self._size, self._rows = budget, size, rows
        self._active = True

    def release(self):
        with self._budget._lock:
            if not self._active:
                raise ValueError("evidence workspace already released")
            self._budget._release_workspace(self._size, self._rows)
            self._active = False

    def promote(self):
        """Keep a failed scope charged while its traceback may retain its graph."""
        with self._budget._lock:
            if not self._active:
                raise ValueError("evidence workspace already released or promoted")
            self._budget._promote_workspace(self._size, self._rows)
            self._active = False


class EvidenceBudget:
    def __init__(
        self,
        *,
        bytes_limit=PHASE_BYTES,
        rows_limit=PHASE_ROWS,
        row_limit=ROW_BYTES,
        work_bytes_limit=None,
        work_rows_limit=None,
        parent=None,
    ):
        self.bytes_limit, self.rows_limit, self.row_limit = bytes_limit, rows_limit, row_limit
        self.bytes = self.rows = 0
        self.workspace_bytes = self.workspace_rows = 0
        self.workspace_peak_bytes = self.workspace_peak_rows = 0
        self.workspace_work_bytes = self.workspace_work_rows = 0
        self.work_bytes_limit = bytes_limit if work_bytes_limit is None else work_bytes_limit
        self.work_rows_limit = rows_limit if work_rows_limit is None else work_rows_limit
        self.parent = parent
        self._lock = parent._lock if parent is not None else threading.RLock()
        if any(
            type(n) is not int or n < 1
            for n in (
                bytes_limit,
                rows_limit,
                row_limit,
                self.work_bytes_limit,
                self.work_rows_limit,
            )
        ):
            raise ValueError("positive evidence budget limits required")

    def child(self, **limits):
        return EvidenceBudget(
            bytes_limit=limits.get("bytes_limit", self.bytes_limit),
            rows_limit=limits.get("rows_limit", self.rows_limit),
            row_limit=limits.get("row_limit", self.row_limit),
            work_bytes_limit=limits.get("work_bytes_limit", self.work_bytes_limit),
            work_rows_limit=limits.get("work_rows_limit", self.work_rows_limit),
            parent=self,
        )

    def check(self, size, rows=1, *, largest=None):
        if any(type(n) is not int or n < 0 for n in (size, rows)):
            raise ValueError("nonnegative evidence accounting required")
        if (
            (largest is not None and largest > self.row_limit)
            or self.bytes + self.workspace_bytes + size > self.bytes_limit
            or self.rows + self.workspace_rows + rows > self.rows_limit
        ):
            raise EvidenceQuotaError("private evidence retention quota exceeded")
        if self.parent is not None:
            self.parent.check(size, rows, largest=largest)

    def reserve(self, size, rows=1, *, largest=None):
        with self._lock:
            self.check(size, rows, largest=largest)
            self._charge(size, rows)

    def _charge(self, size, rows):
        self.bytes += size
        self.rows += rows
        if self.parent is not None:
            self.parent._charge(size, rows)

    def reserve_workspace(self, size, rows=0, *, largest=None):
        """Lease only caller-bounded temporary bytes; permanent charges are untouched."""
        with self._lock:
            self._check_workspace(size, rows, largest=largest)
            self._charge_workspace(size, rows)
            return _WorkspaceLease(self, size, rows)

    def _check_workspace(self, size, rows, *, largest=None):
        self.check(size, rows, largest=largest)
        if (
            self.workspace_work_bytes + size > self.work_bytes_limit
            or self.workspace_work_rows + rows > self.work_rows_limit
        ):
            raise EvidenceQuotaError("private evidence cumulative workspace quota exceeded")
        if self.parent is not None:
            self.parent._check_workspace(size, rows, largest=largest)

    def _charge_workspace(self, size, rows):
        self.workspace_bytes += size
        self.workspace_rows += rows
        self.workspace_peak_bytes = max(self.workspace_peak_bytes, self.workspace_bytes)
        self.workspace_peak_rows = max(self.workspace_peak_rows, self.workspace_rows)
        self.workspace_work_bytes += size
        self.workspace_work_rows += rows
        if self.parent is not None:
            self.parent._charge_workspace(size, rows)

    def _release_workspace(self, size, rows):
        if size > self.workspace_bytes or rows > self.workspace_rows:
            raise ValueError("evidence workspace owner differs")
        self.workspace_bytes -= size
        self.workspace_rows -= rows
        if self.parent is not None:
            self.parent._release_workspace(size, rows)

    def _promote_workspace(self, size, rows):
        # Validate every ancestor before mutating one. Active bytes were already
        # admitted at each owner, so this preserves each owner's P+A balance.
        lineage = []
        current = self
        while current is not None:
            if size > current.workspace_bytes or rows > current.workspace_rows:
                raise ValueError("evidence workspace owner differs")
            lineage.append(current)
            current = current.parent
        for account in lineage:
            account.workspace_bytes -= size
            account.workspace_rows -= rows
            account.bytes += size
            account.rows += rows

    def charge_bytes(self, size):
        self.reserve(size, largest=size)

    def charge(self, value):
        # Reject any one potentially huge encoder token or sort key collection
        # before JSONEncoder allocates it. The complete size remains checked
        # incrementally; caller-owned graphs have their own pre-expansion budget.
        def tokens(item):
            if isinstance(item, str):
                self.check(len(item) * 12 + 2, rows=0, largest=len(item) * 12 + 2)
            elif isinstance(item, dict):
                self.check(len(item) * 16, rows=0)
                for key, child in item.items():
                    tokens(key)
                    tokens(child)
            elif isinstance(item, (tuple, list)):
                self.check(len(item) * 8, rows=0)
                for child in item:
                    tokens(child)

        tokens(value)
        size = 0
        for chunk in json.JSONEncoder(sort_keys=True, allow_nan=False, default=str).iterencode(
            value
        ):
            size += len(chunk.encode())
            if size > self.row_limit:
                raise EvidenceQuotaError("private evidence row quota exceeded")
        self.charge_bytes(size)
