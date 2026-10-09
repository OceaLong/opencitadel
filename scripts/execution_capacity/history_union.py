"""Closed journal union over durable occurrences and fixed complete identity keys."""

from contextlib import contextmanager

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.attempt import encode
from scripts.execution_capacity.original_dictionaries import key_identity
from scripts.execution_capacity.original_plain import copy_plain_graph
from scripts.execution_capacity.replay_relations import same_value


@contextmanager
def source_rows(journal, kind, key, *, owner, budget):
    from scripts.execution_capacity.final_inventory import CumulativeJournal
    from scripts.execution_capacity.inventory_reader import ReadOnlyParents
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal, RecoveryJournal

    if type(journal) in (RecoveryJournal, ReadOnlyRecoveryJournal):
        with journal.read_scope(budget=budget) as scope:
            if key is None:
                rows = scope.records(kind)
            else:
                row = scope.get(kind, key)
                rows = () if row is None else ((key, row),)
            yield rows, scope
    elif type(journal) in (CumulativeJournal, ReadOnlyParents):
        if journal.evidence is None or journal.evidence.journal is not owner:
            raise ValueError("same-owner finite historical journal required")
        if key is None:
            rows = journal.records(kind)
        else:
            row = journal.get(kind, key)
            rows = () if row is None else ((key, row),)
        yield rows, None
    else:
        raise ValueError("closed historical journal source required")


def union_rows(journals, kind, key, *, owner, parent, budget, cumulative):
    """Preserve source occurrences before any comparison or coalescing."""
    owner._usable(writing=True)
    budget.reserve(256, rows=1, largest=256)
    session = owner.index.append("journal-union-sessions", b"{}")
    prefix = "journal-union:" + str(session) + ":"
    candidates = owner.begin_collection(parent, prefix + "candidates")
    for source_ordinal, journal in enumerate(journals):
        budget.reserve(128, rows=1, largest=128)
        with source_rows(journal, kind, key, owner=owner, budget=budget) as (rows, scope):
            previous = None
            for identity, row in rows:
                if (
                    type(identity) is not str
                    or (previous is not None and identity <= previous)
                    or (key is not None and identity != key)
                    or type(row) is not dict
                    or set(row) != {"body", "receipt"}
                ):
                    raise ValueError("historical source identity order/body/receipt differs")
                previous = identity
                view = None if scope is None else scope.native_view(row["body"])
                if view is not None:
                    row = {"body": owner.import_native(view), "receipt": row["receipt"]}
                copied = copy_plain_graph(row, source=owner, target=owner, parent=parent)
                ordinal = owner.index.count("note:collections:" + str(candidates.token.ordinal))
                candidates.append([source_ordinal, identity, copied])
                identity_key = key_identity(owner, identity)
                budget.reserve(
                    len(identity_key) + len(identity) * 12 + 256,
                    rows=1,
                    largest=len(identity_key) + len(identity) * 12 + 256,
                )
                if owner.index.find(prefix + "identities", "key", identity_key) is None:
                    owner.index.append(
                        prefix + "identities", encode(identity), keys={"key": identity_key}
                    )
                owner.index.group(prefix + "candidates", identity_key, encode(ordinal))
    original = candidates.complete()
    output = owner.begin_collection(parent, prefix + "merged")
    for raw in owner.index.identity_rows(prefix + "identities", "key"):
        identity = strict_json(raw)
        identity_key = key_identity(owner, identity)
        selected = None
        for candidate in owner.index.group_rows(prefix + "candidates", identity_key):
            occurrence = original[strict_json(candidate)]
            if (
                type(occurrence) is not list
                or len(occurrence) != 3
                or type(occurrence[0]) is not int
                or not 0 <= occurrence[0] < len(journals)
                or occurrence[1] != identity
            ):
                raise ValueError("historical candidate identity differs")
            row = occurrence[2]
            if selected is None:
                selected = row
            elif cumulative:
                if not same_value(selected["body"], row["body"], owner=owner, budget=budget):
                    raise ValueError("conflicting cumulative intent")
                if selected["receipt"] is None:
                    selected = {"body": selected["body"], "receipt": row["receipt"]}
                elif row["receipt"] is not None and not same_value(
                    selected["receipt"], row["receipt"], owner=owner, budget=budget
                ):
                    raise ValueError("conflicting cumulative receipt")
            elif not same_value(selected, row, owner=owner, budget=budget):
                raise ValueError("conflicting historical journal parent")
        if selected is None:
            raise ValueError("missing historical source occurrence")
        output.append([identity, selected])
    return output.complete()


def family_records(journal, kind):
    """Keep original dictionary semantics without holding an entire family."""
    from scripts.execution_capacity.final_inventory import CumulativeJournal
    from scripts.execution_capacity.retained_final import RetainedHistory

    if type(journal) is RetainedHistory:
        # Already verified original families; records() consumes the full
        # membership/order relation independently before returning authority.
        for _ in journal.records(kind):
            pass
        return journal.values[kind]
    if (
        type(journal) is not CumulativeJournal
        or journal.evidence is None
        or journal.evidence.journal is None
    ):
        return dict(journal.records(kind))
    owner = journal.evidence.journal
    parent = journal.evidence._cleanup_token
    if parent is None:
        raise ValueError("durable cleanup family owner required")
    rows = journal.records(kind)
    writer = owner.begin_dictionary(parent, "history-family:" + kind)
    for key, row in rows:
        writer.append(key, row)
    return writer.complete()
