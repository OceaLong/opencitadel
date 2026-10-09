"""Versioned environment-read bodies owned by the durable recovery directory.

Only the native SQLite format table marks references. Ordinary JSON body fields
are never interpreted as locators. All physical body files are retained before
the small SQLite reference is committed.
"""

import os
import re
from hashlib import sha256
from uuid import uuid4

from scripts.execution_capacity.original_journal import OriginalJournal, _stamp
from scripts.execution_capacity.original_plain import canonical_original_digest, copy_plain_graph

SCHEMA = "opencitadel.environment-read.original.v2"


def manifest_digest(journal):
    fd = journal._file("manifest.json", os.O_RDONLY)
    try:
        before = os.fstat(fd)
        journal.budget.reserve(before.st_size * 2, rows=0, largest=before.st_size)
        raw = os.pread(fd, before.st_size, 0)
        journal._check_file("manifest.json", fd)
        if len(raw) != before.st_size or _stamp(before) != _stamp(os.fstat(fd)):
            raise ValueError("native original manifest changed")
        return sha256(raw).hexdigest()
    finally:
        os.close(fd)


def write_body(root, identity, body, *, source, budget, index_bytes):
    if canonical_original_digest(body, owner=source, budget=budget) != identity:
        raise ValueError("native environment read identity differs")
    name = "original-" + uuid4().hex
    with OriginalJournal.create(root / name, budget=budget, index_bytes=index_bytes) as journal:
        token = journal.begin("operand:projection", {"schema": SCHEMA, "identity": identity})
        copied = copy_plain_graph(body, source=source, target=journal, parent=token)
        if canonical_original_digest(copied, owner=journal, budget=budget) != identity:
            raise ValueError("native original copied body identity differs")
        journal.complete(token, copied)
        journal.seal({"schema": SCHEMA, "kind": "environment_read", "identity": identity})
        return {
            "schema": SCHEMA,
            "namespace": "recovery-local",
            "directory": name,
            "manifest_sha256": manifest_digest(journal),
            "expanded_sha256": identity,
        }


class NativeBodyView:
    def __init__(self, root, identity, reference, *, budget, index_bytes):
        if (
            type(reference) is not dict
            or set(reference)
            != {"schema", "namespace", "directory", "manifest_sha256", "expanded_sha256"}
            or reference["schema"] != SCHEMA
            or reference["namespace"] != "recovery-local"
            or type(reference["directory"]) is not str
            or re.fullmatch(r"original-[0-9a-f]{32}", reference["directory"]) is None
            or reference["expanded_sha256"] != identity
            or type(identity) is not str
            or re.fullmatch(r"[0-9a-f]{64}", identity) is None
            or type(reference["manifest_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", reference["manifest_sha256"]) is None
        ):
            raise ValueError("closed native original reference required")
        self.reference = dict(reference)
        self.identity = identity
        self.journal = OriginalJournal.open(
            root / reference["directory"], budget=budget, index_bytes=index_bytes
        )
        try:
            expected = {"schema": SCHEMA, "kind": "environment_read", "identity": identity}
            if (
                self.journal.binding != expected
                or self.journal.manifest["original_roots"]
                or manifest_digest(self.journal) != reference["manifest_sha256"]
                or any(
                    count != (1 if family == "operand:projection" else 0)
                    for family, count in self.journal.manifest["families"].items()
                )
            ):
                raise ValueError("native original owner closure differs")
            self.body = self.journal.sequence("operand:projection")[0]
            if canonical_original_digest(self.body, owner=self.journal, budget=budget) != identity:
                raise ValueError("native original expanded body identity differs")
        except BaseException:
            self.close()
            raise

    def close(self):
        self.journal.close()
