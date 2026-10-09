"""Derive safe node facts from an actual PostgreSQL JSON ANALYZE BUFFERS plan."""

import math

from scripts.acceptance.capacity_diagnostics import ExecutionPlan, PlanNode, PlanWorker, Quantity
from scripts.acceptance.capacity_io import canonical_digest, strict_json


def unavailable(meaning):
    return Quantity(precision="unavailable", value=None, uncertainty=None, meaning=meaning)


def measured(value, meaning):
    return Quantity(precision="measured", value=value, uncertainty=0, meaning=meaning)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("malformed actual plan quantity")
    return value


def row_quantity(node, key, loops, *, required=False):
    if key not in node:
        if required:
            raise ValueError("missing actual plan rows")
        # Omitted Rows Removed can mean zero in PG output, but do not invent it.
        return unavailable("node field omitted; no value inferred")
    value = number(node[key])
    return Quantity(
        precision="estimated",
        value=value * loops,
        uncertainty=0.5 * loops,
        meaning="rounded per-loop rows multiplied by actual loops; not unique tuples",
    )


def block_quantity(node, key):
    if key not in node:
        return unavailable("node buffer counter absent")
    return measured(
        number(node[key]), "inclusive node block accesses; never sum ancestors and children"
    )


def query_identifier(value):
    if type(value) is not int or not -(2**63) <= value < 2**64 or value == 0:
        raise ValueError("missing actual query identifier")
    return str(value - 2**64 if value >= 2**63 else value)


def node_facts(node):
    loops = number(node.get("Actual Loops"))
    if loops != int(loops):
        raise ValueError("nonintegral actual loops")
    return {
        "loops": loops,
        "rows": row_quantity(node, "Actual Rows", loops, required=True),
        "removed_filter": row_quantity(node, "Rows Removed by Filter", loops),
        "removed_recheck": row_quantity(node, "Rows Removed by Index Recheck", loops),
        "removed_join": row_quantity(node, "Rows Removed by Join Filter", loops),
        "shared_hit_blocks": block_quantity(node, "Shared Hit Blocks"),
        "shared_read_blocks": block_quantity(node, "Shared Read Blocks"),
    }


def read_plan(raw):
    if isinstance(raw, str):
        raw = strict_json(raw.encode())
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError("expected one JSON plan document")
    document = raw[0]
    query_id = document.get("Query Identifier")
    if type(query_id) is not int or query_id == 0:
        raise ValueError("missing actual query identifier")
    root = document.get("Plan")
    if not isinstance(root, dict) or not {"Shared Hit Blocks", "Shared Read Blocks"} <= root.keys():
        raise ValueError("missing actual plan buffers")
    nodes = []

    def walk(node, path):
        if len(nodes) >= 10000 or len(path.split(".")) > 64:
            raise ValueError("plan node/depth limit exceeded")
        workers = None
        if "Workers" in node:
            workers = [
                PlanWorker(worker_number=w["Worker Number"], **node_facts(w))
                for w in node["Workers"]
            ]
        nodes.append(
            PlanNode(
                path=path,
                node_type=node["Node Type"],
                parallel_aware=node.get("Parallel Aware"),
                workers_planned=node.get("Workers Planned"),
                workers_launched=node.get("Workers Launched"),
                workers=workers,
                **node_facts(node),
            )
        )
        for i, child in enumerate(node.get("Plans", [])):
            walk(child, f"{path}.{i}")

    try:
        walk(root, "0")
        duration = Quantity(
            precision="estimated",
            value=number(document["Execution Time"]) * 1_000_000,
            uncertainty=500,
            meaning="server duration rounded to 0.001 ms; includes instrumentation, not isolated observer cost",
        )
    except (KeyError, TypeError) as error:
        raise ValueError("missing actual plan evidence") from error
    return ExecutionPlan(
        query_id=query_identifier(query_id),
        raw_digest=canonical_digest(raw),
        nodes=nodes,
        scan_rows=unavailable(
            "no universal unique scan count; inspect per-node rows, loops and removals"
        ),
        shared_hit_blocks=nodes[0].shared_hit_blocks,
        shared_read_blocks=nodes[0].shared_read_blocks,
        duration_ns=duration,
    )
