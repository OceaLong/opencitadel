"""Pure joins for the safe physical-role exports. No machine IO/launch here."""

from collections import Counter

from scripts.acceptance.capacity_io import canonical_digest
from scripts.acceptance.capacity_package import PublicRows, PublicUnique
from scripts.acceptance.capacity_public_ids import PublicIDs
from scripts.acceptance.capacity_public_lookup import PublicLookup

GIB = 1024**3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(records, key):
    from scripts.acceptance.capacity_package import PublicRows, public_unique

    if type(records) is PublicRows:
        return public_unique(records, key)
    result = {getattr(row, key): row for row in records}
    require(len(result) == len(records), f"duplicate {key}")
    return result


def validate_images(rows):
    """Only the implemented single-root/four-node graph has physical authority."""
    from pathlib import PurePosixPath

    images = unique(rows, "image_id")
    require(len(images) == 1, "unsupported split physical image graph")
    image = next(iter(images.values()))
    require(image.kind == "root", "single raw root image required")
    require(image.stopped_ns <= image.sealed_ns, "image sealed before stores stopped")
    mappings = unique(image.persistence, "role")
    require(
        set(mappings) == {"os", "datastore", "objects", "redis"}, "persistence coverage incomplete"
    )
    require(len({m.filesystem_id for m in mappings.values()}) == 1, "external filesystem mapping")
    for mapping in mappings.values():
        path = PurePosixPath(mapping.root_relative_path)
        require(
            not mapping.external and not path.is_absolute() and ".." not in path.parts,
            "external persistence mapping",
        )
    return images


def cohort_runs(cohorts, *, owner=None):
    from scripts.acceptance.capacity_package import PublicRows
    from scripts.acceptance.capacity_public_cohorts import CohortJoin
    from scripts.acceptance.capacity_public_ids import PublicIDs

    if type(cohorts) is PublicRows:
        owner = cohorts._owner
    rows = cohorts.finish() if type(cohorts) is CohortJoin else unique(cohorts, "cohort_id")
    runs = set() if owner is None else PublicIDs(owner, "cohort-runs")
    for cohort in rows.values():
        require(cohort.source == cohort.view, "source/view parity mismatch")
        require(cohort.source.runs > 0 and cohort.source.formal_events > 0, "empty source cohort")
        ids = (
            set(cohort.run_ids)
            if owner is None
            else PublicIDs(owner, "cohort-runs", cohort.run_ids)
        )
        require(
            len(ids) == len(cohort.run_ids) == cohort.source.runs,
            "cohort exact Run inventory mismatch",
        )
        require(
            not (runs.intersection(ids) if owner is None else runs.intersects(ids)),
            "duplicate cohort Run",
        )
        runs.update(ids)
    return runs


def join_inventories(seal_id, base, rounds, retained, attempt_id):
    """Count immutable history once; compare actual complete sets on every clone."""
    from scripts.acceptance.capacity_package import PublicRows
    from scripts.acceptance.capacity_public_cohorts import CohortJoin, base_cohort_digest
    from scripts.acceptance.capacity_public_ids import PublicIDs

    owner = base._owner if type(base) is PublicRows else None
    if owner is not None and any(
        type(rows) is not PublicRows or rows._owner is not owner for rows in (rounds, retained)
    ):
        raise ValueError("foreign public cohort relationship")
    base_runs = cohort_runs(base)
    require(
        {c.kind for c in base}
        == {"standard", "step_capacity", "evaluation_subject", "evaluation_judge"},
        "base source cohort inventory incomplete or contains later work",
    )
    require(
        all(c.origin.kind == "base" and c.origin.seal_id == seal_id for c in base),
        "immutable base origin mismatch",
    )
    base_digest = (
        canonical_digest([c.model_dump() for c in base])
        if owner is None
        else base_cohort_digest(base)
    )
    increments = [] if owner is None else CohortJoin(owner)
    if owner is not None:
        increments.extend(base)
    children, boots, clones = (
        (set(), set(), set())
        if owner is None
        else tuple(
            PublicIDs(owner, name)
            for name in ("physical-children", "physical-boots", "physical-clones")
        )
    )
    for row in rounds:
        origin = row.origin
        require(
            origin.kind == "round" and origin.seal_id == seal_id and not row.errors,
            "invalid round inventory origin or retained errors",
        )
        binding = origin.round
        require(
            binding.parent_attempt_id == attempt_id and origin.clone_id == binding.round_id,
            "round parent/clone mismatch",
        )
        require(
            binding.round_id not in children
            and origin.boot_id not in boots
            and origin.clone_id not in clones,
            "duplicate physical round inventory",
        )
        children.add(binding.round_id)
        boots.add(origin.boot_id)
        clones.add(origin.clone_id)
        require(row.base_inventory_digest == base_digest, "immutable base inventory changed")
        require(
            all(
                c.origin == origin and c.kind not in {"standard", "step_capacity"}
                for c in row.cohorts
            ),
            "round cohort origin mismatch",
        )
        additions = cohort_runs(row.cohorts, owner=owner)
        require(
            not (
                additions.intersection(base_runs)
                if owner is None
                else additions.intersects(base_runs)
            ),
            "cloned base counted as new Run",
        )
        owned_runs = base_runs | additions
        require(
            row.owned_run_count == len(owned_runs)
            and row.owned_run_digest
            == (
                canonical_digest(sorted(owned_runs))
                if owner is None
                else owned_runs.sorted_digest()
            ),
            "actual clone source set differs",
        )
        increments.extend(row.cohorts)
    expected = unique(base + increments, "cohort_id") if owner is None else increments.finish()
    actual = unique(retained, "cohort_id")
    require(
        actual == expected if owner is None else expected.matches(actual),
        "cleanup hidden/missing source cohort counts",
    )
    cohort_runs(list(expected.values()) if owner is None else expected, owner=owner)
    return {
        field: sum(getattr(c.source, field) for c in expected.values())
        for field in ("runs", "formal_events", "observations", "visible_steps")
    }


def join_round_windows(plans, rounds, windows, attempt_id):
    require(
        len({p.physical_window_id for p in plans.values()}) == len(plans),
        "baseline/loaded physical window reused across samples",
    )
    require(
        len(rounds) == len(plans) and {r.origin.round.sample_id for r in rounds} == set(plans),
        "baseline/loaded physical round must cover every sample exactly once",
    )
    baseline = {p.physical_window_id: p for p in plans.values() if p.mode == "baseline"}
    require(
        len(baseline) == sum(p.mode == "baseline" for p in plans.values())
        and not set(baseline).intersection(windows),
        "baseline physical identity missing/duplicate/shared with load",
    )
    rows = (
        PublicLookup(rounds, "round-window")
        if type(rounds) is PublicRows
        else {r.origin.round.window_id: r for r in rounds}
    )
    require(
        len(rows) == len(rounds) and set(rows) == set(windows) | set(baseline),
        "baseline/loaded physical round inventory incomplete",
    )
    for key, row in rows.items():
        binding = row.origin.round
        require(
            binding.sample_id in plans
            and binding.parent_attempt_id == attempt_id
            and plans[binding.sample_id].physical_window_id == key,
            "actual physical round/preregistered sample mismatch",
        )
        if key in windows:
            window = windows[key]
            require(
                binding == window.round_origin and row.origin.boot_id == window.boot_id,
                "actual child/boot inventory mismatch",
            )
        else:
            require(
                baseline[key].sample_id == binding.sample_id,
                "baseline physical identity bound to another sample",
            )
        plan = plans[binding.sample_id]
        expected_live = {claim.run_id for claim in windows[key].claims} if key in windows else set()
        actual_live = {
            run for cohort in row.cohorts if cohort.kind == "live" for run in cohort.run_ids
        }
        require(actual_live == expected_live, "round live source inventory/claim mismatch")
        expected_admissions = {plan.target.run_id} if plan.operation == "admission" else set()
        actual_admissions = {
            run for cohort in row.cohorts if cohort.kind == "admission" for run in cohort.run_ids
        }
        require(
            actual_admissions == expected_admissions,
            "round admission source inventory/sample mismatch",
        )


def summarize_cohort(cohort):
    return {
        **cohort.model_dump(exclude={"run_ids"}),
        "run_ids_digest": canonical_digest(sorted(cohort.run_ids)),
    }


def physical(roles, plans, samples):
    protocol, seal, env = (roles[k] for k in ("protocol", "seal", "environment"))
    require(seal.seal_id == protocol.seal_id, "seal identity mismatch")
    images = validate_images(seal.images)
    cohort_runs(seal.cohorts)
    standard = [c for c in seal.cohorts if c.kind == "standard"]
    probe = [c for c in seal.cohorts if c.kind == "step_capacity"]
    require(len(standard) == len(probe) == 1, "unique standard/probe required")
    require(
        standard[0].scope_id == roles["fixture"].target.scope_id,
        "standard seal scope differs from fixture",
    )
    require(
        standard[0].source.runs == 100000 and standard[0].source.formal_events == 10000000,
        "standard actual counts mismatch",
    )
    require(
        probe[0].source.runs == 1
        and probe[0].source.visible_steps == 10000
        and probe[0].source.formal_events == 30003,
        "step capacity actual counts mismatch",
    )
    require(probe[0].scope_id != standard[0].scope_id, "step probe must be independently owned")
    for plan in plans.values():
        if plan.dimension == "step_capacity":
            require(
                plan.target.scope_id == probe[0].scope_id
                and plan.target.run_id == probe[0].run_ids[0],
                "step target cohort mismatch",
            )
        elif plan.dimension == "standard" and plan.operation != "live_visible":
            require(plan.target.scope_id == standard[0].scope_id, "standard target scope mismatch")
    require(
        len(env.server_cpu_ids) == len(set(env.server_cpu_ids)) == 16,
        "non-reference hardware server CPU",
    )
    require(
        len(env.client_cpu_ids) == len(set(env.client_cpu_ids)) >= 4,
        "non-reference hardware client CPU",
    )
    require(
        env.server_memory_bytes == 32 * GIB
        and env.postgres_memory_max_bytes == 8 * GIB
        and env.client_memory_bytes >= 16 * GIB,
        "non-reference hardware memory",
    )
    if env.server_host_id == env.client_host_id:
        require(
            not set(env.server_cpu_ids) & set(env.client_cpu_ids),
            "overlapping server/client allocation",
        )
        require(
            env.host_memory_bytes
            >= env.server_memory_bytes + env.client_memory_bytes + env.overhead_memory_bytes,
            "insufficient shared-host memory",
        )
    require(
        not env.disk_rotational and env.disk_direct_io and env.viewport == [1440, 900],
        "non-reference disk/viewport",
    )
    limits = env.runtime_limits
    require(limits.policy_id == seal.policy_id, "runtime policy mismatch")
    headroom = 10 + limits.subject_concurrency + limits.judge_concurrency + 1
    require(
        limits.workers * limits.per_worker >= headroom
        and limits.claim_batch_size <= limits.per_worker <= limits.pool_size + limits.max_overflow,
        "worker/pool headroom unavailable",
    )
    require(
        all(
            cap is None or cap >= headroom
            for cap in (limits.physical_global, limits.physical_user, limits.physical_provider)
        ),
        "physical headroom unavailable",
    )

    resets = unique(roles["resets"].resets, "reset_id")
    cold = {p.reset_id: p for p in plans.values() if p.mode == "cold"}
    require(
        len(cold) == len(resets) == 100 and set(cold) == set(resets),
        "exactly 100 independent cold resets required",
    )
    for key in ("boot_id", "process_uuid", "helper_nonce"):
        unique(list(resets.values()), key)
    process_ids, overlays = set(), set()
    for reset in resets.values():
        plan, sample = cold[reset.reset_id], samples[cold[reset.reset_id].sample_id]
        require(
            (reset.sample_id, reset.window_id, reset.seal_id)
            == (plan.sample_id, plan.window_id, seal.seal_id),
            "reset sample/window/seal mismatch",
        )
        process = (reset.process_id, reset.process_start_ticks)
        require(process not in process_ids, "reused cold process identity")
        process_ids.add(process)
        require(
            reset.socket_peer_pid == reset.process_id and reset.qmp_uuid == reset.process_uuid,
            "QMP peer/process mismatch",
        )
        require(
            reset.executable_sha256 == env.qemu_binary_sha256
            and reset.helper_source_digest == seal.source_digest,
            "reset executable/source mismatch",
        )
        require(
            reset.kvm_enabled and not reset.ram_resumed and not reset.shared_mounts,
            "TCG/RAM resume/shared storage is not cold",
        )
        require(
            {
                "query-uuid",
                "query-kvm",
                "query-cpus-fast",
                "query-memory-size-summary",
                "query-block",
                "query-named-block-nodes",
                "query-qmp-schema",
            }
            <= set(reset.qmp_commands),
            "missing actual QMP capability readback",
        )
        require(
            reset.guest_cpu_count == 16 and reset.guest_memory_bytes == 32 * GIB,
            "guest non-reference allocation",
        )
        require(reset.helper_nonce == reset.helper_response_nonce, "QGA helper nonce mismatch")
        require(
            reset.coordinator_clock_id == protocol.clock_id
            and reset.launched_ns
            <= reset.minimal_ready_ns
            <= reset.load_ready_ns
            <= reset.target_dispatch_ns
            <= reset.stopped_ns,
            "cold clock/startup order",
        )
        require(
            reset.target_dispatch_ns == sample.start_ns
            and reset.target_dispatch_ns
            >= next(
                w.coordinator_start_ns
                for w in roles["workload"].windows
                if w.window_id == reset.window_id
            ),
            "adaptive cold target deadline",
        )
        require(
            reset.load_ready_ns <= reset.target_dispatch_ns,
            "cold load not ready at fixed deadline",
        )
        require(not reset.target_reads_before_dispatch, "cold target prewarming")
        require(bool(reset.incidental_warming), "missing incidental cache warming disclosure")
        nodes = unique(reset.nodes, "node_name")
        require(
            len(nodes) == 4
            and Counter(n.driver for n in nodes.values()) == {"file": 2, "raw": 1, "qcow2": 1},
            "cold four-node backing graph required",
        )
        require(
            all(n.cache_direct and not n.cache_no_flush for n in nodes.values()),
            "cold backing-node direct IO missing",
        )
        top = next(n for n in nodes.values() if n.driver == "qcow2")
        base = next(n for n in nodes.values() if n.driver == "raw")
        require(
            top.backing == base.node_name
            and top.child in nodes
            and base.child in nodes
            and top.child != base.child,
            "invalid backing graph edges",
        )
        require(
            base.backing is None and base.read_only and not top.read_only,
            "invalid base/overlay graph",
        )
        base_file, overlay = nodes[base.child], nodes[top.child]
        require(
            base_file.driver == overlay.driver == "file"
            and base_file.child is None
            and overlay.child is None
            and base_file.backing is None
            and overlay.backing is None,
            "file graph is not flattened",
        )
        require(
            base_file.read_only
            and not overlay.read_only
            and base_file.fd_direct
            and overlay.fd_direct,
            "physical file FD O_DIRECT evidence missing",
        )
        require(
            base_file.image_id in images and images[base_file.image_id].kind == "root",
            "sealed backing image mismatch",
        )
        image = images[base_file.image_id]
        require(
            (base_file.device, base_file.inode) == (image.device, image.inode),
            "backing FD/seal inode mismatch",
        )
        identity = (overlay.device, overlay.inode)
        require(
            all(identity) and identity not in overlays and identity != (image.device, image.inode),
            "overlay inode reused or absent",
        )
        overlays.add(identity)

    network = roles["network"]
    host, client = network.host_link, network.client_link
    require(
        host.ifindex == client.peer_ifindex
        and client.ifindex == host.peer_ifindex
        and host.namespace_inode != client.namespace_inode,
        "network veth peer/namespace mismatch",
    )
    require(
        host.route_ifindex == host.ifindex
        and client.route_ifindex == client.ifindex
        and not host.default_route
        and not client.default_route,
        "network routes invalid",
    )
    require(
        network.forward_bound_address == host.address
        and bool(network.guest_ports)
        and not network.global_ip_forward_changed
        and not network.nat_changed,
        "unowned/global network change",
    )
    windows = unique(roles["workload"].windows, "window_id")
    require(
        set(network.context_ids) == {x for w in windows.values() for x in w.context_ids},
        "shared 20Mbps context link mismatch",
    )
    from scripts.acceptance.capacity_network import validate_calibrations

    validate_calibrations(
        network, windows, protocol.clock_id, unique(protocol.windows, "window_id")
    )

    diagnostics = unique(roles["diagnostics"].queries, "sample_id")
    require(
        len(diagnostics) == len(samples) and set(diagnostics) == set(samples),
        "missing per-sample query diagnostics",
    )
    from scripts.acceptance.capacity_diagnostics import validate_query

    indexed_rounds = type(roles["cleanup"].rounds) is PublicRows
    diagnostic_rounds = (
        PublicLookup(roles["cleanup"].rounds, "round-sample")
        if indexed_rounds
        else {r.origin.round.sample_id: r.origin for r in roles["cleanup"].rounds}
    )
    require(
        len(diagnostic_rounds) == len(samples) and set(diagnostic_rounds) == set(samples),
        "diagnostic physical rounds differ",
    )
    diagnostic_sources = unique(roles["measurements"].sources, "source_id")
    for key, query in diagnostics.items():
        validate_query(
            query,
            plans[key],
            samples[key],
            clock_id=protocol.clock_id,
            origin=diagnostic_rounds[key].origin if indexed_rounds else diagnostic_rounds[key],
            source=diagnostic_sources[samples[key].source_id],
        )

    cleanup = roles["cleanup"]
    require(not cleanup.pending and not cleanup.quarantined, "capacity cleanup unresolved")
    totals = join_inventories(
        seal.seal_id, seal.cohorts, cleanup.rounds, cleanup.cohorts, protocol.attempt_id
    )
    require(cleanup.total.model_dump() == totals, "cleanup total count mismatch")
    join_round_windows(plans, cleanup.rounds, windows, protocol.attempt_id)
    dispositions = unique(cleanup.dispositions, "resource_id")
    proof = {
        "qemu_process": "proc-starttime-absent",
        "qmp_socket": "socket-inode-absent",
        "overlay": "protected-inode-retained",
        "broker": "broker-lookup-absent",
        "upload": "object-readback",
        "claim": "claim-readback",
        "lease": "lease-readback",
        "provider_call": "settlement-readback",
    }
    require(
        set(proof) <= {d.kind for d in dispositions.values()},
        "cleanup physical inventory incomplete",
    )
    for d in dispositions.values():
        require(
            d.state not in ("pending", "quarantined")
            and d.physical_slots == 0
            and d.proof_kind == proof[d.kind],
            "unresolved physical disposition",
        )
        expected = (
            "retained"
            if d.kind in ("overlay", "upload")
            else ("settled" if d.kind in ("claim", "lease", "provider_call") else "absent")
        )
        require(d.state == expected, "cleanup state/proof mismatch")
    for reset in resets.values():
        owned = [d for d in dispositions.values() if d.owner_id == reset.reset_id]
        require(
            Counter(d.kind for d in owned) == {"qemu_process": 1, "qmp_socket": 1, "overlay": 1},
            "reset exact owned cleanup missing",
        )
        require(
            all(d.observed_ns >= reset.stopped_ns for d in owned), "cleanup precedes process stop"
        )
        overlay = next(n for n in reset.nodes if n.driver == "file" and not n.read_only)
        identity = {
            "qemu_process": {
                "pid": reset.process_id,
                "start_ticks": reset.process_start_ticks,
                "uuid": reset.process_uuid,
            },
            "qmp_socket": {
                "device": reset.socket_device,
                "inode": reset.socket_inode,
                "peer_pid": reset.socket_peer_pid,
            },
            "overlay": {"device": overlay.device, "inode": overlay.inode},
        }
        require(
            all(d.identity_digest == canonical_digest(identity[d.kind]) for d in owned),
            "cleanup process/socket/overlay identity mismatch",
        )
    if type(dispositions) is PublicUnique:
        calls = PublicIDs(
            dispositions._rows._owner,
            "provider-calls",
            (c.call_identity for w in windows.values() for c in w.claims),
        )
        calls.update(call for w in windows.values() for t in w.ticks for call in t.active_call_ids)
        call_receipts = PublicLookup(dispositions, "provider-calls")
        complete = len(call_receipts) == len(calls)
        for call in call_receipts:
            if call not in calls:
                complete = False
    else:
        calls = {c.call_identity for w in windows.values() for c in w.claims} | {
            call for w in windows.values() for t in w.ticks for call in t.active_call_ids
        }
        call_receipts = {
            d.resource_id: d for d in dispositions.values() if d.kind == "provider_call"
        }
        complete = set(call_receipts) == calls
    require(complete, "cleanup dispatch identity inventory mismatch")
    require(
        all(
            d.identity_digest == canonical_digest({"call_identity": call})
            for call, d in call_receipts.items()
        ),
        "cleanup settlement identity mismatch",
    )
    return probe[0]
