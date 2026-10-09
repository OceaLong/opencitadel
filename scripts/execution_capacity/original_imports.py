"""Explicit durable native namespace import; no foreign cursor survives it."""

import os
from hashlib import sha256

from scripts.acceptance.capacity_io import copy_artifacts, strict_json
from scripts.acceptance.capacity_models import Artifact
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.evidence_json import equal_streams
from scripts.execution_capacity.native_original_bodies import NativeBodyView
from scripts.execution_capacity.original_plain import canonical_parts, copy_plain_graph

BUFFER = 64 * 1024


def equal_values(left, left_owner, right, right_owner, budget):
    if not equal_streams(
        canonical_parts(left, owner=left_owner, budget=budget),
        canonical_parts(right, owner=right_owner, budget=budget),
    ):
        raise ValueError("native original expanded values differ")


def validate_entry(entry, ordinal):
    if (
        type(entry) is not dict
        or set(entry) != {"namespace", "ordinal", "reference", "manifest", "manifest_bytes"}
        or entry["namespace"] != "native-local"
        or type(entry["ordinal"]) is not int
        or entry["ordinal"] != ordinal
        or type(entry["manifest"]) is not dict
        or entry["manifest"].get("native_bodies") != []
        or type(entry["manifest_bytes"]) is not int
        or entry["manifest_bytes"] != len(encode(entry["manifest"]))
    ):
        raise ValueError("closed native import namespace required")


def open_entry(owner, entry, ordinal):
    validate_entry(entry, ordinal)
    root = owner.root / f"native-{ordinal:06}"
    reference = entry["reference"]
    if type(reference) is not dict:
        raise ValueError("closed native import reference required")
    view = NativeBodyView(
        root,
        reference.get("expanded_sha256"),
        reference,
        budget=owner.budget,
        index_bytes=owner.index_bytes,
    )
    try:
        if view.journal.manifest != entry["manifest"]:
            raise ValueError("native imported manifest differs")
        # The namespace contains this exact original directory and nothing else.
        with os.scandir(view.journal.parent) as entries:
            for position, member in enumerate(entries):
                if position != 0 or member.name != reference["directory"]:
                    raise ValueError("native imported namespace membership differs")
        return view
    except BaseException:
        view.close()
        raise


def import_body(owner, view):
    from scripts.execution_capacity.original_journal import _stamp, _Token

    owner._usable(writing=True)
    if type(view) is not NativeBodyView:
        raise ValueError("verified live native original view required")
    view.journal._usable()
    if not view.journal.sealed or view.journal.manifest.get("native_bodies") != []:
        raise ValueError("complete nonrecursive native original view required")
    ordinal = owner.index.count("begin:native-imports")
    owner.budget.charge(view.journal.manifest)
    manifest_raw = encode(view.journal.manifest)
    owner.budget.reserve(len(manifest_raw) * 2, rows=0, largest=len(manifest_raw))
    entry = {
        "namespace": "native-local",
        "ordinal": ordinal,
        "reference": dict(view.reference),
        "manifest": strict_json(manifest_raw),
        "manifest_bytes": len(manifest_raw),
    }
    validate_entry(entry, ordinal)
    owner._emit("begin", "native-imports", ordinal, entry)
    token = _Token(owner, "native-imports", ordinal)
    try:
        count = 1 + len(view.journal.body_segments.chunks) + len(view.journal.log_segments.chunks)
        owner.budget.reserve(count * 256, rows=count)
        names = ["manifest.json"]
        for segments in (view.journal.body_segments, view.journal.log_segments):
            names.extend(segments.name(chunk["ordinal"]) for chunk in segments.chunks)
        descriptors = []
        for name in names:
            # Names are exclusively derived from the already verified source.
            fd = view.journal._file(name, os.O_RDONLY)
            try:
                info = os.fstat(fd)
                hashed = sha256()
                for offset in range(0, info.st_size, BUFFER):
                    raw = os.pread(fd, min(BUFFER, info.st_size - offset), offset)
                    if len(raw) != min(BUFFER, info.st_size - offset):
                        raise ValueError("native original incomplete bytes")
                    owner.budget.reserve(len(raw), rows=0)
                    hashed.update(raw)
                view.journal._check_file(name, fd)
                if _stamp(info) != _stamp(os.fstat(fd)):
                    raise ValueError("native original changed during read")
            finally:
                os.close(fd)
            descriptors.append(
                Artifact(
                    path=view.reference["directory"] + "/" + name,
                    role="measurements",
                    schema_version=3,
                    size_bytes=info.st_size,
                    sha256=hashed.hexdigest(),
                )
            )
        owner.budget.reserve(sum(item.size_bytes for item in descriptors), rows=0)
        copy_artifacts(descriptors, view.journal.root.parent, owner.root / f"native-{ordinal:06}")
        copied_view = open_entry(owner, entry, ordinal)
        try:
            # Compare every original byte, not merely copied digest strings.
            for name in names:
                source = view.journal._file(name, os.O_RDONLY)
                target = copied_view.journal._file(name, os.O_RDONLY)
                try:
                    source_info, target_info = os.fstat(source), os.fstat(target)
                    size = source_info.st_size
                    if target_info.st_size != size:
                        raise ValueError("native original copied bytes differ")
                    for offset in range(0, size, BUFFER):
                        length = min(BUFFER, size - offset)
                        owner.budget.reserve(length * 2, rows=0)
                        left, right = (
                            os.pread(source, length, offset),
                            os.pread(target, length, offset),
                        )
                        if len(left) != length or len(right) != length or left != right:
                            raise ValueError("native original copied bytes differ")
                    view.journal._check_file(name, source)
                    copied_view.journal._check_file(name, target)
                    if _stamp(source_info) != _stamp(os.fstat(source)) or _stamp(
                        target_info
                    ) != _stamp(os.fstat(target)):
                        raise ValueError("native original changed during comparison")
                finally:
                    os.close(source)
                    os.close(target)
            copied = copy_plain_graph(
                copied_view.body, source=copied_view.journal, target=owner, parent=token
            )
            equal_values(view.body, view.journal, copied, owner, owner.budget)
            equal_values(copied_view.body, copied_view.journal, copied, owner, owner.budget)
            owner.complete(token, copied)
            owner.budget.charge(entry)
            owner.native_bodies.append(entry)
            return copied
        finally:
            copied_view.close()
    except BaseException:
        owner.valid = False
        raise


def verify_import(owner, row):
    ordinal = row["ordinal"]
    if ordinal >= len(owner.native_bodies):
        raise ValueError("missing native import namespace")
    entry = owner.native_bodies[ordinal]
    if row["kind"] == "begin":
        if owner._record_value(row) != entry:
            raise ValueError("native import occurrence namespace differs")
    elif row["kind"] == "end":
        view = open_entry(owner, entry, ordinal)
        try:
            equal_values(owner._record_value(row), owner, view.body, view.journal, owner.budget)
        finally:
            view.close()
    else:
        raise ValueError("invalid native import lifecycle")
