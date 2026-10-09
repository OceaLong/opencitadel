"""Offline ownership/parser tests; subprocess, namespace and pidfd edges substituted."""

import copy
from uuid import uuid4

import pytest
from scripts.execution_capacity.attempt import AttemptLedger


def plan():
    from scripts.execution_capacity.reference_network import NetworkPlan

    return NetworkPlan(str(uuid4()), "192.168.201.0/30")


def links(p):
    return [
        {
            "ifname": p.host_link,
            "ifindex": 11,
            "link_index": 12,
            "ifalias": p.alias,
            "flags": ["UP"],
            "linkinfo": {"info_kind": "veth"},
            "addr_info": [{"family": "inet", "local": "192.168.201.1", "prefixlen": 30}],
        },
        {
            "ifname": p.client_link,
            "ifindex": 12,
            "link_index": 11,
            "ifalias": p.alias,
            "flags": ["UP"],
            "linkinfo": {"info_kind": "veth"},
            "addr_info": [{"family": "inet", "local": "192.168.201.2", "prefixlen": 30}],
        },
    ]


def test_observed_peer_identity_rejects_foreign_alias_and_address():
    from scripts.execution_capacity.reference_network import verify_links

    p = plan()
    assert verify_links(p, *links(p)) == {"host_ifindex": 11, "client_ifindex": 12}
    for field, value in [("link_index", 88), ("ifalias", "foreign"), ("addr_info", [])]:
        rows = links(p)
        rows[1][field] = value
        with pytest.raises(ValueError, match="owned veth"):
            verify_links(p, *rows)


def test_real_tc_seconds_and_bytes_units_not_nominal_measurements():
    # Offline shape derived from iproute2 v6.8.0 tc/q_netem.c printer:
    # https://raw.githubusercontent.com/iproute2/iproute2/v6.8.0/tc/q_netem.c
    # ecn=false and gap=0 are unconditionally emitted (lines 810-814).
    from scripts.execution_capacity.reference_network import verify_qdisc

    rows = [
        {
            "kind": "netem",
            "handle": "1:",
            "root": True,
            "options": {
                "limit": 1000,
                "ecn": False,
                "gap": 0,
                "delay": {"delay": 0.025, "jitter": 0, "correlation": 0},
                "rate": {"rate": 2500000, "packetoverhead": 0, "cellsize": 0, "celloverhead": 0},
            },
            "bytes": 100000,
            "packets": 70,
            "drops": 3,
            "overlimits": 0,
        }
    ]
    assert verify_qdisc(rows)["drops"] == 3
    for field, value in [
        ("delay", 25),
        ("rate", 20000000),
        ("loss", 0.01),
        ("ecn", True),
        ("gap", 1),
        ("unknown", 0),
        ("ecn", 0),
        ("gap", False),
    ]:
        changed = copy.deepcopy(rows)
        changed[0]["options"][field] = value
        with pytest.raises(ValueError, match="netem"):
            verify_qdisc(changed)


def test_lost_namespace_create_is_consumed_across_reopen(tmp_path, monkeypatch):
    from scripts.execution_capacity import reference_network as mod

    p = plan()
    immutable = {"network": p.record()}
    with AttemptLedger.create(tmp_path / "attempt", immutable) as ledger:
        network = mod.OwnedNetwork(p, ledger)
        monkeypatch.setattr(network, "preflight", dict)

        def lost(*args, **kwargs):
            assert ledger.count("network-effect-intent") == 1
            raise TimeoutError("response lost")

        monkeypatch.setattr(mod, "run", lost)
        with pytest.raises(TimeoutError):
            network.create()
    with AttemptLedger.open(tmp_path / "attempt", immutable) as ledger:
        network = mod.OwnedNetwork(p, ledger)
        with pytest.raises(ValueError, match="consumed"):
            network.create()
        with pytest.raises(ValueError, match="ownership"):
            network.cleanup()


def test_uncertain_unregistered_consumer_prevents_network_deletion(tmp_path, monkeypatch):
    from scripts.execution_capacity import reference_network as mod

    p = plan()
    with AttemptLedger.create(tmp_path / "attempt", {"network": p.record()}) as ledger:
        network = mod.OwnedNetwork(p, ledger)
        monkeypatch.setattr(network, "reconcile", dict)
        network.reserve_consumer("qemu:owned", client=False)

        def forbidden(*args):
            raise AssertionError("no deletion allowed")

        monkeypatch.setattr(mod, "run", forbidden)
        with pytest.raises(ValueError, match="exit authority"):
            network.cleanup()
        with pytest.raises(ValueError, match="reserved"):
            network.reserve_consumer("qemu:owned", client=False)


def test_shaping_writes_only_owned_egress_and_requires_readback(tmp_path, monkeypatch):
    from scripts.execution_capacity import reference_network as mod

    p = plan()
    with AttemptLedger.create(tmp_path / "attempt", {"network": p.record()}) as ledger:
        network = mod.OwnedNetwork(p, ledger)
        monkeypatch.setattr(network, "reconcile", dict)
        calls = []

        def boundary(argv):
            assert ledger.count("network-effect-intent") == len(calls) + 1
            calls.append(argv)
            return b""

        monkeypatch.setattr(mod, "run", boundary)

        def readback():
            raise ValueError("foreign observed qdisc")

        monkeypatch.setattr(network, "observe", readback)
        with pytest.raises(ValueError, match="foreign observed"):
            network.shape()
        assert calls == [
            [
                "/usr/sbin/tc",
                "qdisc",
                "add",
                "dev",
                p.host_link,
                "root",
                "handle",
                "1:",
                "netem",
                "delay",
                "25ms",
                "rate",
                "20mbit",
                "limit",
                "1000",
            ],
            [
                "/usr/sbin/ip",
                "netns",
                "exec",
                p.namespace,
                "/usr/sbin/tc",
                "qdisc",
                "add",
                "dev",
                p.client_link,
                "root",
                "handle",
                "1:",
                "netem",
                "delay",
                "25ms",
                "rate",
                "20mbit",
                "limit",
                "1000",
            ],
        ]
        with pytest.raises(ValueError, match="consumed"):
            network.shape()


def test_cleanup_observes_exits_and_preserves_foreign_namespace(tmp_path, monkeypatch):
    from scripts.execution_capacity import reference_network as mod

    p = plan()
    with AttemptLedger.create(tmp_path / "attempt", {"network": p.record()}) as ledger:
        network = mod.OwnedNetwork(p, ledger)
        ledger.append(
            "network-namespace",
            {"attempt_id": p.attempt_id, "identity": {"device": 4, "inode": 55}},
        )
        ledger.append(
            "network-pair",
            {"attempt_id": p.attempt_id, "identity": {"host_ifindex": 11, "client_ifindex": 12}},
        )
        monkeypatch.setattr(network, "_namespace_identity", lambda: {"device": 4, "inode": 99})
        monkeypatch.setattr(
            mod, "run", lambda *args: (_ for _ in ()).throw(AssertionError("must not delete"))
        )
        with pytest.raises(ValueError, match="namespace ownership"):
            network.cleanup()


def exited_network(ledger, monkeypatch, *, at_empty_pids=lambda: None, lost_delete=False):
    """Prepare private ownership facts; replace only external reads/syscalls."""
    import json

    from scripts.execution_capacity import reference_network as mod

    p = mod.NetworkPlan(**ledger.plan["network"])
    network = mod.OwnedNetwork(p, ledger)
    network._save("network-namespace", identity={"device": 4, "inode": 55})
    network._save("network-pair", identity={"host_ifindex": 11, "client_ifindex": 12})
    network.reserve_consumer("old", client=False)
    process = {"pid": 23, "start_ticks": 42}
    network._save("network-consumer", identity=process, client=False, consumer_id="old")
    network.consumers["old"] = (91, process)
    monkeypatch.setattr(network, "_namespace_identity", lambda: {"device": 4, "inode": 55})
    boundary = {"deletes": [], "closed": []}

    def run(argv):
        if "address" in argv:
            return json.dumps([links(p)[1 if "-n" in argv else 0]]).encode()
        if argv == [mod.IP, "netns", "pids", p.namespace]:
            at_empty_pids()
            return b""
        if argv == [mod.IP, "link", "delete", "dev", p.host_link]:
            boundary["deletes"].append("veth")
            if lost_delete:
                raise TimeoutError("lost veth delete response")
            return b""
        if argv == [mod.IP, "-n", p.namespace, "-j", "link", "show"]:
            return b'[{"ifname":"lo"}]'
        if argv == [mod.IP, "-j", "link", "show"]:
            return b'[{"ifname":"eth0"}]'
        if argv == [mod.IP, "netns", "delete", p.namespace]:
            boundary["deletes"].append("namespace")
            return b""
        raise AssertionError(argv)

    class Poll:
        def register(self, fd, event):
            assert fd == 91

        def poll(self, timeout):
            return [(91, mod.select.POLLIN)]

    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(mod.select, "poll", Poll)
    monkeypatch.setattr(mod.os.path, "lexists", lambda path: False)
    original_close = mod.os.close

    def close(fd):
        if fd == 91:
            boundary["closed"].append(fd)
        else:
            original_close(fd)

    monkeypatch.setattr(mod.os, "close", close)
    return network, boundary


def test_cleanup_fences_interleaved_consumer_reservation_and_launch(tmp_path, monkeypatch):
    import threading

    p = plan()
    checked, proceed, attempted = (threading.Event() for _ in range(3))

    def pause():
        checked.set()
        assert proceed.wait(3)

    with AttemptLedger.create(tmp_path / "attempt", {"network": p.record()}) as ledger:
        network, boundary = exited_network(ledger, monkeypatch, at_empty_pids=pause)
        failures, launches = [], []

        def cleanup():
            try:
                network.cleanup()
            except (ValueError, OSError, AssertionError) as exc:
                failures.append(exc)

        def start_client():
            attempted.set()
            try:
                network.reserve_consumer("late", client=True)
            except ValueError as exc:
                failures.append(exc)
            else:
                launches.append("late")  # The real caller spawns only after reservation.

        cleaner = threading.Thread(target=cleanup)
        late = threading.Thread(target=start_client)
        cleaner.start()
        try:
            assert checked.wait(3)
            late.start()
            assert attempted.wait(3)
        finally:
            proceed.set()
            cleaner.join(3)
            if late.ident is not None:
                late.join(3)
        assert not cleaner.is_alive()
        assert not late.is_alive()
        assert launches == []
        assert len(failures) == 1
        assert "teardown" in str(failures[0])
        assert boundary["deletes"] == ["veth", "namespace"]
        assert ledger.count("network-released") == 1
        assert [r["body"]["consumer_id"] for r in ledger.records("network-consumer-intent")] == [
            "old"
        ]


@pytest.mark.parametrize("lost_delete", [False, True])
def test_teardown_fence_survives_success_or_lost_response_and_reopen(
    tmp_path, monkeypatch, lost_delete
):
    from scripts.execution_capacity import reference_network as mod

    immutable = {"network": plan().record()}
    with AttemptLedger.create(tmp_path / "attempt", immutable) as ledger:
        network, boundary = exited_network(ledger, monkeypatch, lost_delete=lost_delete)
        if lost_delete:
            with pytest.raises(TimeoutError, match="lost veth"):
                network.cleanup()
        else:
            network.cleanup()
        assert ledger.count("network-teardown-intent") == 1
        assert ledger.count("network-released") == (0 if lost_delete else 1)
    with AttemptLedger.open(tmp_path / "attempt", immutable) as ledger:
        reopened = mod.OwnedNetwork(mod.NetworkPlan(**immutable["network"]), ledger)
        calls_before = list(boundary["deletes"])
        with pytest.raises(ValueError, match="teardown"):
            reopened.reserve_consumer("late", client=True)
        with pytest.raises(ValueError, match="teardown"):
            reopened.register_consumer(24, {}, client=True, consumer_id="late")
        with pytest.raises(ValueError, match="teardown"):
            reopened.cleanup()
        assert boundary["deletes"] == calls_before


def test_reservation_before_spawn_blocks_teardown_transition(tmp_path, monkeypatch):
    immutable = {"network": plan().record()}
    with AttemptLedger.create(tmp_path / "attempt", immutable) as ledger:
        network, boundary = exited_network(ledger, monkeypatch)
        network.reserve_consumer("reserved-not-spawned", client=True)
        with pytest.raises(ValueError, match="exit authority"):
            network.cleanup()
        assert boundary["deletes"] == []
        assert ledger.count("network-teardown-intent") == 0
        assert ledger.count("network-released") == 0
