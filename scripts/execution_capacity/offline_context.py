"""Runner-owned locations and mandatory original semantic replay.

Location records never attest to bytes. Every public operation independently
opens original ledgers, images and private views, and replays the original
predicates before exposing a safe projection. Secrets remain memory-only.
"""

import asyncio
import hashlib
import json
import os
from contextlib import ExitStack, closing
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path, PurePosixPath

from scripts.acceptance.capacity_c2c_models import C2c
from scripts.acceptance.capacity_index import CapacityIndex
from scripts.acceptance.capacity_models import SourceOrigin
from scripts.execution_capacity.attempt import ReadOnlyAttemptLedger
from scripts.execution_capacity.c2c_export import _receipt
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.evidence_json import json_digest
from scripts.execution_capacity.original_shards import OriginalView
from scripts.execution_capacity.proof_copy import copy_private, directory
from scripts.execution_capacity.retained_final import replay_final
from scripts.execution_capacity.round_originals import verify_round_lineage
from scripts.execution_capacity.safe_projection import project_unit, typed_digest
from scripts.execution_capacity.seal_finalizer import VerifiedBase


class PrivateProofError(ValueError):
    """A fixed public error; original inputs/paths/secrets never become messages."""


PROJECT_WORKING_BYTES = 64 * 1024 * 1024
PROJECT_ORIGINAL_BYTES = 8 * 1024 * 1024
PROJECT_LEGACY_BYTES = 2 * 1024 * 1024
PROJECT_ORIGINAL_MEMBERS = 65_536
PROJECT_INDEX_BYTES = 4 * 1024 * 1024
PROJECT_ROW_BYTES = 4 * 1024 * 1024
LEGACY_COPY_INDEX_BYTES = 4 * 1024 * 1024
MAX_ORIGINAL_PATH_BYTES = 4096
MAX_ORIGINAL_FILE_BYTES = (1 << 63) - 1


@dataclass(frozen=True, repr=False)
class BaseLocation:
    origin: Path
    retained: Path
    image: Path


@dataclass(frozen=True, repr=False)
class RoundLocation:
    parent_origin: Path
    parent_retained: Path
    child_origin: Path
    child_retained: Path
    base_origin: Path


def _absolute(path):
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise ValueError("canonical trusted original location required")
    try:
        length = len(os.fsencode(path))
    except UnicodeEncodeError as error:
        raise ValueError("bounded original location required") from error
    if length > MAX_ORIGINAL_PATH_BYTES:
        raise ValueError("bounded original location required")


def _files(location, ledger, view, budget, *, base=None):
    yield "plan.json", _checked_file(location, "plan.json", ledger.plan_sha256, budget)
    yield "attempt.jsonl", _checked_file(location, "attempt.jsonl", ledger.ledger_sha256, budget)
    if view is not None:
        name = "c2c-originals/manifest.json"
        manifest = _receipt(view.root / "manifest.json", budget)
        yield name, _checked_file(location, name, manifest["sha256"], budget)
        from scripts.execution_capacity.guest_seal_entry import (
            artifact_relative,
            original_artifacts,
        )

        for name, descriptor in original_artifacts(view.manifest):
            relative = artifact_relative(name)
            receipt = _checked_file(location, relative, descriptor["sha256"], budget)
            if receipt["size_bytes"] != descriptor["size_bytes"]:
                raise ValueError("original file length changed after replay")
            yield relative, receipt
    if base is not None:
        yield (
            "sealed-base.json",
            _checked_file(location, "sealed-base.json", base.artifact_digest, budget),
        )
        for name, row in base.private["artifacts"].items():
            relative = "seal-guest-" + name + ".json"
            receipt = _checked_file(location, relative, row["sha256"], budget)
            if receipt["size_bytes"] != row["size_bytes"]:
                raise ValueError("original file length changed after replay")
            yield relative, receipt
        for name, row in base.private["c2c_export"]["files"].items():
            relative = "c2c-ledger/" + name
            receipt = _checked_file(location, relative, row["sha256"], budget)
            if receipt["size_bytes"] != row["size_bytes"]:
                raise ValueError("original file length changed after replay")
            yield relative, receipt


def _checked_file(location, name, digest, budget):
    row = _receipt(location / name, budget)
    if row["sha256"] != digest:
        raise ValueError("original file changed after replay")
    return row


def _member_bytes(name, receipt):
    path = PurePosixPath(name) if type(name) is str else None
    if (
        type(name) is not str
        or len(name) > 1024
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != name
        or type(receipt) is not dict
        or set(receipt) != {"sha256", "size_bytes"}
        or type(receipt["sha256"]) is not str
        or len(receipt["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in receipt["sha256"])
        or type(receipt["size_bytes"]) is not int
        or not 0 <= receipt["size_bytes"] <= MAX_ORIGINAL_FILE_BYTES
    ):
        raise ValueError("closed original file receipt required")
    return json.dumps(
        [name, receipt], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _record_fingerprint(fingerprints, origin, source, files):
    """Consume a fresh complete member stream and bind repeat visits in this replay."""
    digest = hashlib.sha256(b"C2C-original-members-v1\x00")
    count = total = 0
    with closing(files):
        for name, receipt in files:
            raw = _member_bytes(name, receipt)
            count += 1
            if count > PROJECT_ORIGINAL_MEMBERS:
                raise ValueError("original member population exceeds protocol")
            total += len(raw)
            digest.update(len(raw).to_bytes(4, "big"))
            digest.update(raw)
    current = (source, count, total, digest.digest())
    previous = fingerprints.setdefault(origin, current)
    if previous != current:
        raise ValueError("original member changed between rounds")


def _record_fingerprint_budgeted(fingerprints, origin, source, files, budget):
    workspace = budget.reserve_workspace(32_768)
    try:
        _record_fingerprint(fingerprints, origin, source, files)
    except BaseException:
        workspace.promote()
        raise
    else:
        workspace.release()


def _member_pair(raw):
    value = json.loads(raw)
    if type(value) is not list or len(value) != 2 or _member_bytes(*value) != raw:
        raise ValueError("indexed original member differs")
    return value[0], value[1]


def _indexed_members(index, stream):
    with closing(index.rows(stream)) as rows:
        for raw in rows:
            yield _member_pair(raw)


def _diagnostic_members(files, diagnostics):
    with closing(files):
        yield from files
    yield from diagnostics.items()


def _project_file_size(path):
    from scripts.execution_capacity.proof_copy import _regular

    with directory(path.parent) as owner:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=owner)
        try:
            return _regular(descriptor).st_size
        finally:
            os.close(descriptor)


def _project_source_preflight(location, budget, *, originals):
    """Refuse oversized whole-model compatibility work before ledger/view open.

    This is a resource rejection only. Later replay independently checks every
    byte and semantic predicate, including any source changed after preflight.
    """
    from scripts.execution_capacity.guest_seal import read_private
    from scripts.execution_capacity.guest_seal_entry import original_artifacts

    total = 0
    for name in ("plan.json", "attempt.jsonl"):
        size = _project_file_size(location / name)
        total += size
        if total > PROJECT_ORIGINAL_BYTES:
            raise ValueError("whole safe projection source exceeds local limit")
    if not originals:
        return
    manifest_path = location / "c2c-originals/manifest.json"
    manifest_bytes = _project_file_size(manifest_path)
    if manifest_bytes > 1024 * 1024:
        raise ValueError("whole safe projection manifest exceeds local limit")
    total += manifest_bytes
    manifest = read_private(manifest_path, budget=budget, max_bytes=1024 * 1024)
    for count, (_, descriptor) in enumerate(original_artifacts(manifest), start=1):
        if type(descriptor["size_bytes"]) is not int or descriptor["size_bytes"] < 0:
            raise ValueError("whole safe projection member size differs")
        total += descriptor["size_bytes"]
        if count > PROJECT_ORIGINAL_MEMBERS or total > PROJECT_ORIGINAL_BYTES:
            raise ValueError("whole safe projection source exceeds local limit")
    if manifest["schema"] == 1 and (
        total > PROJECT_LEGACY_BYTES
        or type(manifest.get("nodes")) is not int
        or manifest["nodes"] > 16_384
    ):
        raise ValueError("legacy whole original graph exceeds local limit")


def verify_round_export(child, view, base, binding, roots, *, budget):
    """Check actual exact final ledger event and its original byte prefix."""
    events = child.records("c2c-private-final")
    intents = child.records("c2c-round-collection-intent")
    if (
        len(events) != 1
        or len(intents) != 1
        or events[0] != child.rows[-1]
        or intents[0]["sequence"] >= events[0]["sequence"]
    ):
        raise ValueError("original round closeout absent or duplicated")
    event = events[0]
    final = event["body"]
    context = view.manifest["binding"]
    if (
        set(final) != {"binding", "manifest"}
        or final["binding"] != context
        or final["manifest"] != _receipt(view.root / "manifest.json", budget)
    ):
        raise ValueError("original round final manifest differs")
    cleanup = roots["cleanup"]
    expected = {
        "schema": 1,
        "kind": "round",
        "state": "complete",
        "phase": "after-exit",
        "protocol_id": base.base.seal.protocol_id,
        "round": binding.model_dump(),
        "base": base.descriptor(),
        "prior": context.get("prior"),
        "commitments": {
            "full_quiescence": json_digest(
                cleanup, budget=budget, owner=getattr(view, "journal", None)
            ),
            "source_safe": json_digest(
                _source_safe(
                    cleanup["final"]["source"], owner=getattr(view, "journal", None), budget=budget
                ),
                budget=budget,
                owner=getattr(view, "journal", None),
            ),
        },
        "observer_closed_ns": context.get("observer_closed_ns"),
    }
    if (
        context != expected
        or type(context["observer_closed_ns"]) is not int
        or context["observer_closed_ns"] < cleanup["ended_ns"]
    ):
        raise ValueError("original round closure/base commitments differ")
    if intents[0]["body"] != {
        "round": binding.model_dump(),
        "base_artifact": base.base.artifact_digest,
    }:
        raise ValueError("original round collection intent differs")
    prior = context["prior"]
    if (
        type(prior) is not dict
        or set(prior) != {"origin", "sequence", "digest", "bytes"}
        or prior["origin"] != str(child.root)
        or type(prior["sequence"]) is not int
        or prior["sequence"] + 1 != event["sequence"]
        or prior["digest"] != event["previous"]
        or type(prior["bytes"]) is not int
    ):
        raise ValueError("original round prior boundary differs")
    # The readonly ledger has already checked each original line. Reopen its
    # exact descriptor solely to prove the retained byte boundary, budget first.
    from scripts.execution_capacity.ownership import _open_private

    offset = 0
    with os.fdopen(_open_private(child.location / "attempt.jsonl", os.O_RDONLY), "rb") as stream:
        for _ in range(prior["sequence"]):
            budget.reserve(budget.row_limit + 1, rows=0)
            offset += len(stream.readline(budget.row_limit + 1))
    if offset != prior["bytes"]:
        raise ValueError("original round prior bytes differ")


def _source_safe(raw, *, owner=None, budget=None):
    from scripts.execution_capacity.inventory import SourceInventory

    return SourceInventory(**raw).safe(owner=owner, budget=budget)


def validate_round_origins(protocol, origins, loaded_origins):
    """Join every private physical round to its fixed ordered sample ledger.

    Baselines have real private physical origins but no loaded workload Window.
    This is a relational check after original replay, never original authority.
    Only the fixed 1,200-entry ledger can occupy Python identity sets here.
    """
    if len(protocol.samples) > 1200:
        raise ValueError("private round ledger exceeds fixed population")
    origins, loaded_origins = iter(origins), iter(loaded_origins)
    missing = object()
    samples, rounds, physical_windows = set(), set(), set()
    for plan in protocol.samples:
        origin = next(origins, missing)
        if (
            origin is missing
            or plan.sample_id in samples
            or plan.physical_window_id in physical_windows
            or origin.round_id in rounds
            or origin.parent_attempt_id != protocol.attempt_id
            or origin.sample_id != plan.sample_id
            or origin.window_id != plan.physical_window_id
        ):
            raise ValueError("complete original round/sample binding differs")
        samples.add(plan.sample_id)
        rounds.add(origin.round_id)
        physical_windows.add(plan.physical_window_id)
        if plan.mode == "baseline":
            if plan.window_id is not None:
                raise ValueError("baseline round claims loaded window")
        elif plan.window_id != plan.physical_window_id or next(loaded_origins, missing) != origin:
            raise ValueError("complete loaded round/window binding differs")
    if next(origins, missing) is not missing or next(loaded_origins, missing) is not missing:
        raise ValueError("extra original round/window binding")


class OfflineProofContext:
    def __init__(self, *, bases, rounds, signing_secret, cursor_secret, budget, index_bytes=None):
        if (
            type(budget) is not EvidenceBudget
            or any(type(row) is not BaseLocation for row in bases)
            or any(type(row) is not RoundLocation for row in rounds)
        ):
            raise TypeError("concrete trusted private proof configuration required")
        if (
            type(signing_secret) is not str
            or not signing_secret
            or type(cursor_secret) is not bytes
            or not cursor_secret
        ):
            raise ValueError("original in-memory verification material required")
        if (
            len(bases) != 1
            or len(rounds) > 1200
            or len({row.origin for row in bases}) != len(bases)
            or len({row.child_origin for row in rounds}) != len(rounds)
        ):
            raise ValueError("unique original base and round locations required")
        for row in bases:
            for path in (row.origin, row.retained, row.image):
                _absolute(path)
        known = {row.origin for row in bases}
        origins = {row.origin: row.retained for row in bases}
        for row in rounds:
            for path in (
                row.parent_origin,
                row.parent_retained,
                row.child_origin,
                row.child_retained,
                row.base_origin,
            ):
                _absolute(path)
            if row.base_origin not in known or row.child_origin in origins:
                raise ValueError("foreign or overlapping private round location")
            for origin, location in (
                (row.parent_origin, row.parent_retained),
                (row.child_origin, row.child_retained),
            ):
                if origin in origins and origins[origin] != location:
                    raise ValueError("ambiguous original retained location")
                origins[origin] = location
        if len(set(origins.values())) != len(origins):
            raise ValueError("aliased original retained location")
        if len(origins) > 2401:
            raise ValueError("private origin population exceeds protocol")
        self._bases, self._rounds = tuple(bases), tuple(rounds)
        self._origin_count = len(origins)
        self._signing_secret, self._cursor_secret = signing_secret, cursor_secret
        self._budget, self._index_bytes = budget, index_bytes

    async def _replay(self, comparator=None, copy_destination=None, projection_sink=None):
        with ExitStack() as owners:
            return await self._replay_open(owners, comparator, copy_destination, projection_sink)

    async def _replay_open(
        self, owners, comparator=None, copy_destination=None, projection_sink=None
    ):
        budget = self._budget
        if sum(value is not None for value in (comparator, copy_destination, projection_sink)) > 1:
            raise ValueError("private replay has one consumer")
        projecting = comparator is None and copy_destination is None and projection_sink is None
        if projecting and (
            (self._index_bytes is not None and self._index_bytes > PROJECT_INDEX_BYTES)
            or budget.row_limit > PROJECT_ROW_BYTES
        ):
            raise ValueError("whole safe projection resources exceed local limit")
        budget.reserve(
            (len(self._bases) + len(self._rounds)) * 4096, rows=len(self._bases) + len(self._rounds)
        )
        bases = {}
        units = []
        members = {}
        fingerprints = {}
        # Each source is the already-held location Path, not a copied path;
        # the extra per-origin tuple/digest is fixed. A member name is at most
        # 1,024 codepoints (12 ASCII escape bytes each), size at most signed
        # 63-bit decimal and digest 64 ASCII bytes: one encoded row < 13KiB.
        # The 32KiB workspace covers it and its hashing/iteration copies.
        budget.reserve(256 * self._origin_count, rows=self._origin_count)
        index = None
        if copy_destination is not None:
            if comparator is not None:
                raise ValueError("indexed private copy configuration required")
            # A round has at most one parent and one child location; the one
            # base plus 1,200 original rounds cap every trusted map entry.
            if self._origin_count > 2401:
                raise ValueError("private copy origin population exceeds protocol")
            copy_index_bytes = self._index_bytes or LEGACY_COPY_INDEX_BYTES
            budget.reserve(copy_index_bytes + 4096 * self._origin_count)
            index = CapacityIndex(quota_bytes=copy_index_bytes, row_bytes=4096)
            owners.callback(index.close)

        def record(origin, source, files):
            if index is None:
                _record_fingerprint_budgeted(fingerprints, origin, source, files, budget)
                return
            with closing(files):
                if origin in members:
                    previous, stream = members[origin]
                    if previous != source:
                        raise ValueError("ambiguous original retained location")
                    missing = object()
                    with closing(index.rows(stream)) as saved_rows:
                        for actual, saved in zip_longest(files, saved_rows, fillvalue=missing):
                            if (
                                actual is missing
                                or saved is missing
                                or _member_bytes(*actual) != saved
                            ):
                                raise ValueError("original member changed between rounds")
                    return
                stream = f"private-copy:{len(members):06}"
                if len(members) >= self._origin_count:
                    raise ValueError("private copy origin population exceeds protocol")
                members[origin] = (source, stream)
                for name, receipt in files:
                    raw = _member_bytes(name, receipt)
                    budget.reserve(len(raw) * 2 + 256, rows=1)
                    index.append(stream, raw, keys={"member": name})

        rounds = []
        origins = []
        queries = []
        origin_count = 0
        origin_hash = hashlib.sha256()
        projection_working = 0

        def retain_projection(value):
            nonlocal projection_working
            # This explicit compatibility API returns whole public models. It
            # is limited independently of the caller's much larger evidence
            # replay quota; production validation uses the indexed comparator.
            encoded = value.model_dump_json()
            projected = len(encoded.encode()) * 8 + 256
            if projected > PROJECT_WORKING_BYTES - projection_working:
                raise ValueError("whole safe projection exceeds local working limit")
            budget.reserve(projected, rows=1)
            projection_working += projected

        for location in self._bases:
            if projecting:
                _project_source_preflight(location.retained, budget, originals=True)
            ledger = ReadOnlyAttemptLedger.open(
                location.retained, origin=location.origin, budget=budget
            )
            base = owners.enter_context(
                VerifiedBase.open(
                    ledger, image_path=location.image, budget=budget, index_bytes=self._index_bytes
                ).open_evidence(budget=budget)
            )
            if projecting:
                retain_projection(base.base.seal)
            roots = base.roots
            cleanup = roots["cleanup"]
            if cleanup["quiescence"]["diagnostics"]:
                raise ValueError("base diagnostic lacks actual round command owner")
            origin = SourceOrigin(
                kind="base", seal_id=base.base.seal.seal_id, round=None, boot_id=None, clone_id=None
            )
            source = await replay_final(
                roots,
                owner=getattr(base.view, "journal", None),
                origin=origin,
                base=None,
                budget=budget,
                signing_secret=self._signing_secret,
                cursor_secret=self._cursor_secret,
            )
            from scripts.execution_capacity.cohort_inventory import cohorts_equal

            if not cohorts_equal(
                source.cohorts,
                base.base.seal.cohorts,
                budget=budget,
                left_owner=getattr(base.view, "journal", None),
            ):
                raise ValueError("original base public cohorts differ")
            origin_digest = typed_digest(base.descriptor(), budget=budget)
            unit = project_unit(
                roots,
                base.view,
                kind="base",
                origin_sha256=origin_digest,
                base_sha256=None,
                budget=budget,
            )
            bases[location.origin] = (base, cleanup, source, origin_digest)
            if projecting:
                retain_projection(unit)
                units.append(unit)
            elif comparator is not None or projection_sink is not None:
                (comparator or projection_sink).base(base.base.seal, unit)
            record(
                location.origin,
                location.retained,
                _files(location.retained, base.host_ledger, base.view, budget, base=base.base),
            )
        for location in self._rounds:
            if projecting:
                _project_source_preflight(location.parent_retained, budget, originals=False)
                _project_source_preflight(location.child_retained, budget, originals=True)
            parent = ReadOnlyAttemptLedger.open(
                location.parent_retained, origin=location.parent_origin, budget=budget
            )
            child = ReadOnlyAttemptLedger.open(
                location.child_retained, origin=location.child_origin, budget=budget
            )
            base, cleanup, base_source, base_digest = bases[location.base_origin]
            with OriginalView.open(
                location.child_retained / "c2c-originals",
                budget=budget,
                index_bytes=self._index_bytes,
                verified_base=base if self._index_bytes is not None else None,
            ) as view:
                roots = view.materialize()
                final = roots["cleanup"]["final"]
                bundles = roots["operands"]["final-input"]
                if len(bundles) != 1:
                    raise ValueError("unique actual round final inputs required")
                identity = bundles[0]["identity"]
                origin = SourceOrigin.model_validate(identity["origin"])
                binding, _ = verify_round_lineage(
                    parent,
                    child,
                    base,
                    cleanup,
                    inherited=identity["base"],
                    writers=cleanup["writer_base"],
                    origin=origin,
                    budget=budget,
                    owner=getattr(view, "journal", None),
                )
                verify_round_export(child, view, base, binding, roots, budget=budget)
                source = await replay_final(
                    roots,
                    owner=getattr(view, "journal", None),
                    base_owner=getattr(base.view, "journal", None),
                    origin=origin,
                    base={
                        "source_inventory": cleanup["source_inventory"],
                        "source_cohorts": base_source.cohorts,
                        "writer_base": cleanup["writer_base"],
                        "final": cleanup["quiescence"]["final"],
                        "diagnostic_inventory": base_source,
                    },
                    budget=budget,
                    signing_secret=self._signing_secret,
                    cursor_secret=self._cursor_secret,
                )
                from scripts.execution_capacity.retained_diagnostics import round_diagnostics

                round_queries, diagnostic_files = round_diagnostics(
                    parent,
                    child,
                    binding,
                    origin,
                    source,
                    roots["cleanup"]["diagnostics"],
                    budget=budget,
                    base_inventory=base_source,
                )
                if projecting:
                    for query in round_queries:
                        retain_projection(query)
                    queries.extend(round_queries)
                elif comparator is not None or projection_sink is not None:
                    for query in round_queries:
                        (comparator or projection_sink).query(query)
                # Every base history body/receipt and source membership is immutable;
                # new round observations remain independent complete read sequences.
                _inherited_history(
                    cleanup["quiescence"]["final"],
                    final,
                    budget=budget,
                    base_owner=getattr(base.view, "journal", None),
                    owner=getattr(view, "journal", None),
                )
                exported_round = source.round_export(
                    origin,
                    base_source.cohorts,
                    owner=getattr(view, "journal", None),
                    base_owner=getattr(base.view, "journal", None),
                    budget=budget,
                )
                if projecting:
                    retain_projection(exported_round)
                    rounds.append(exported_round)
                elif comparator is not None or projection_sink is not None:
                    (comparator or projection_sink).round(exported_round)
                safe_origin = binding.safe()
                if projecting:
                    retain_projection(safe_origin)
                    origins.append(safe_origin)
                elif comparator is not None:
                    origins.append(safe_origin)
                elif projection_sink is not None:
                    projection_sink.origin(safe_origin)
                    origin_hash.update(
                        json.dumps(
                            safe_origin.model_dump(mode="json"),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        ).encode()
                    )
                    origin_hash.update(b"\n")
                    origin_count += 1
                unit = project_unit(
                    roots,
                    view,
                    kind="round",
                    origin_sha256=typed_digest(binding.model_dump(), budget=budget),
                    base_sha256=base_digest,
                    budget=budget,
                )
                if projecting:
                    retain_projection(unit)
                    units.append(unit)
                elif comparator is not None or projection_sink is not None:
                    (comparator or projection_sink).unit(unit)
                record(
                    location.parent_origin,
                    location.parent_retained,
                    _files(location.parent_retained, parent, None, budget),
                )
                record(
                    location.child_origin,
                    location.child_retained,
                    _diagnostic_members(
                        _files(location.child_retained, child, view, budget),
                        diagnostic_files,
                    ),
                )
        if comparator is not None:
            comparator.finish(origins)
            return None
        if projection_sink is not None:
            if origin_count != len(self._rounds):
                raise ValueError("streamed private round origin count differs")
            projection_sink.finish(origin_count, origin_hash.hexdigest())
            return None
        if copy_destination is not None:
            if len(members) != self._origin_count:
                raise ValueError("private original member closure differs")
            return self._copy_indexed(copy_destination, members, index)
        only_base = next(iter(bases.values()))[0].base
        projection = C2c(
            attempt_id=only_base.seal.attempt_id,
            protocol_id=only_base.seal.protocol_id,
            projection_version=units[0].schema_version,
            units=units,
        )
        return projection, only_base.seal, rounds, origins, queries

    def _checked(self, comparator=None):
        try:
            return asyncio.run(self._replay() if comparator is None else self._replay(comparator))
        except PrivateProofError:
            raise
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            OverflowError,
            RecursionError,
            RuntimeError,
        ):
            raise PrivateProofError("private C2c original replay failed") from None

    def _copy_indexed(self, destination, members, index):
        """Copy only the complete, replay-validated indexed member inventory."""
        with directory(destination.parent) as parent:
            os.mkdir(destination.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        retained = {}
        for ordinal, (origin, (source, stream)) in enumerate(members.items()):
            if not index.count(stream):
                raise ValueError("empty original member inventory")
            target = destination / f"unit-{ordinal:06}"
            copy_private(
                source,
                target,
                _indexed_members(index, stream),
                budget=self._budget,
            )
            retained[origin] = target
        with directory(destination) as copied:
            os.fsync(copied)
        return OfflineProofContext(
            bases=[
                BaseLocation(row.origin, retained[row.origin], row.image) for row in self._bases
            ],
            rounds=[
                RoundLocation(
                    row.parent_origin,
                    retained[row.parent_origin],
                    row.child_origin,
                    retained[row.child_origin],
                    row.base_origin,
                )
                for row in self._rounds
            ],
            signing_secret=self._signing_secret,
            cursor_secret=self._cursor_secret,
            budget=self._budget,
            index_bytes=self._index_bytes,
        )

    def project(self):
        """Return only a strictly bounded compatibility model for small callers."""
        return self._checked()[0]

    def stream_projection(self, sink):
        """Replay originals into an ordered, single-use bounded projection sink.

        Events are base, then per round zero or more queries, round, origin,
        unit. Finish receives exact origin count and ordered canonical-line
        SHA256; an exception leaves any sink prefix uncommitted. The sink must
        not treat an event as authority until finish and public validation.
        """
        try:
            return asyncio.run(self._replay(projection_sink=sink))
        except PrivateProofError:
            raise
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            OverflowError,
            RecursionError,
            RuntimeError,
        ):
            raise PrivateProofError("private C2c original replay failed") from None

    def public_resources(self):
        """Trusted cumulative allowance; never read resource limits from evidence."""
        from scripts.acceptance.capacity_package import PublicConsumerResources

        return PublicConsumerResources(self._budget, self._index_bytes)

    def validate(self, roles, *, package=None):
        if package is not None:
            from scripts.acceptance.capacity_package import PackageSession

            if (
                type(package) is not PackageSession
                or any(not package.owns_role(role, name) for name, role in roles.items())
                or set(roles) != set(package.models)
            ):
                raise PrivateProofError("actual sealed public package required")
        if package is not None:
            self._checked(package.start_private_comparison())
            return
        projection, seal, rounds, origins, queries = self._checked()
        try:
            if (
                (package is None and type(roles["c2c"]) is not C2c)
                or roles["c2c"] != projection
                or roles["seal"] != seal
                or roles["cleanup"].rounds != rounds
            ):
                raise ValueError("complete safe projection differs")
            if roles["diagnostics"].queries != queries:
                raise ValueError("complete original diagnostic Query projection differs")
            expected_cohorts = [
                *seal.cohorts,
                *(cohort for row in rounds for cohort in row.cohorts),
            ]
            if roles["cleanup"].cohorts != expected_cohorts:
                raise ValueError("complete safe cohort projection differs")
            validate_round_origins(
                roles["protocol"],
                origins,
                (window.round_origin for window in roles["workload"].windows),
            )
            if (
                roles["protocol"].attempt_id != projection.attempt_id
                or roles["protocol"].protocol_id != projection.protocol_id
            ):
                raise ValueError("original safe protocol differs")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise PrivateProofError("private C2c complete safe projection differs") from None

    def copy(self, destination):
        """Revalidate source bytes then copy; returned owner has no cached authority."""
        try:
            _absolute(destination)
            return asyncio.run(self._replay(copy_destination=destination))
        except (OSError, ValueError, TypeError, KeyError):
            raise PrivateProofError("private C2c original copy failed") from None


def _inherited_history(base, round_final, *, budget=None, base_owner=None, owner=None):
    from scripts.execution_capacity.evidence_json import chunks, equal_streams

    def equal(left, right):
        if owner is None:
            return left == right
        return equal_streams(
            chunks(left, budget=budget, owner=base_owner), chunks(right, budget=budget, owner=owner)
        )

    for area in ("retained_history", "predicate_journals"):
        left = base[area]["records"] if area == "retained_history" else base[area]
        right = round_final[area]["records"] if area == "retained_history" else round_final[area]
        for family, rows in left.items():
            if any(not equal(value, right[family].get(key)) for key, value in rows.items()):
                raise ValueError("immutable original base history changed")
