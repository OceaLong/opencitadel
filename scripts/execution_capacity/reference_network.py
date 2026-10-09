"""Owned Linux client link. Explicit calls have effects; import has none.

Only new per-attempt veth/netns objects are changed. Uncertain creates remain
consumed. This is private operational evidence, not a capacity report producer.
"""

import ipaddress
import json
import os
import platform
import select
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from scripts.execution_capacity.guest_bridge import process_snapshot

IP = "/usr/sbin/ip"
TC = "/usr/sbin/tc"
ETHTOOL = "/usr/sbin/ethtool"


def run(argv):
    result = subprocess.run(argv, check=True, capture_output=True, timeout=10)
    if len(result.stdout) + len(result.stderr) > 4 * 1024 * 1024:
        raise ValueError("network readback exceeds bound")
    return result.stdout


def read_json(argv):
    return json.loads(run(argv))


@dataclass(frozen=True)
class NetworkPlan:
    attempt_id: str
    subnet: str

    def __post_init__(self):
        UUID(self.attempt_id)
        network = ipaddress.IPv4Network(self.subnet)
        if network.prefixlen != 30 or not network.is_private or network.is_loopback:
            raise ValueError("unused private IPv4 /30 required")

    @property
    def namespace(self):
        return "occ-" + UUID(self.attempt_id).hex

    @property
    def host_link(self):
        return "och" + UUID(self.attempt_id).hex[:11]

    @property
    def client_link(self):
        return "occ" + UUID(self.attempt_id).hex[:11]

    @property
    def alias(self):
        return "opencitadel-capacity:" + self.attempt_id

    @property
    def host_address(self):
        return str(ipaddress.IPv4Network(self.subnet)[1])

    @property
    def client_address(self):
        return str(ipaddress.IPv4Network(self.subnet)[2])

    def record(self):
        return asdict(self)


def verify_links(plan, host, client, *, configured=True):
    for row, name, peer, address in [
        (host, plan.host_link, client, plan.host_address),
        (client, plan.client_link, host, plan.client_address),
    ]:
        if (
            row["ifname"] != name
            or row["ifalias"] != plan.alias
            or row["linkinfo"]["info_kind"] != "veth"
            or row["ifindex"] <= 0
            or row["link_index"] != peer["ifindex"]
        ):
            raise ValueError("owned veth peer identity differs")
        if configured:
            addresses = {(a["family"], a["local"], a["prefixlen"]) for a in row["addr_info"]}
            ipv4 = {a for a in addresses if a[0] == "inet"}
            if ipv4 != {("inet", address, 30)} or "UP" not in row["flags"]:
                raise ValueError("owned veth address/state differs")
    return {"host_ifindex": host["ifindex"], "client_ifindex": client["ifindex"]}


def verify_qdisc(rows):
    if len(rows) != 1:
        raise ValueError("one exact root netem required")
    row = rows[0]
    expected = {
        "limit": 1000,
        "ecn": False,
        "gap": 0,
        "delay": {"delay": 0.025, "jitter": 0, "correlation": 0},
        "rate": {"rate": 2500000, "packetoverhead": 0, "cellsize": 0, "celloverhead": 0},
    }
    # Supported iproute2 v6.8.0 printer shape: neutral ecn/gap are explicit,
    # delay is seconds and rate is bytes/sec. Unknown features remain rejected.
    if row["options"].get("ecn") is not False or type(row["options"].get("gap")) is not int:
        raise ValueError("actual netem neutral fields differ")
    if (row["kind"], row["handle"], row.get("root"), row["options"]) != (
        "netem",
        "1:",
        True,
        expected,
    ):
        raise ValueError("actual netem options differ or unsupported iproute2 JSON")
    if any(type(row.get(k)) is not int or row[k] < 0 for k in ("bytes", "packets", "drops")):
        raise ValueError("actual qdisc counters required")
    return row


class OwnedNetwork:
    def __init__(self, plan, ledger):
        if ledger.plan.get("network") != plan.record():
            raise ValueError("immutable network plan required")
        self.plan, self.ledger = plan, ledger
        self.namespace_fd = None
        self.consumers = {}

    def _ip(self, *args, client=False):
        return [IP, *(["-n", self.plan.namespace] if client else []), *args]

    def _tc(self, *args, client=False):
        command = [TC, *args]
        return [IP, "netns", "exec", self.plan.namespace, *command] if client else command

    def _rows(self, kind):
        return [
            r["body"]
            for r in self.ledger.records(kind)
            if r["body"].get("attempt_id") == self.plan.attempt_id
        ]

    def _save(self, kind, **body):
        return self.ledger.append(kind, {"attempt_id": self.plan.attempt_id, **body})

    def _effect(self, name, argv):
        with self.ledger.control_lock:
            if name not in {"veth-delete", "namespace-delete"}:
                self._require_active()
            if any(r["operation"] == name for r in self._rows("network-effect-intent")):
                raise ValueError("network operation already consumed; reconcile ownership")
            self._save(
                "network-effect-intent", operation=name, argv=argv, host_ns=time.monotonic_ns()
            )
            try:
                run(argv)
            except BaseException as exc:
                self._save("network-effect-uncertain", operation=name, error=type(exc).__name__)
                raise
            self._save("network-effect-returned", operation=name, host_ns=time.monotonic_ns())

    def preflight(self):
        if platform.system() != "Linux":
            raise ValueError("Linux network namespace prerequisite unavailable")
        for binary in (IP, TC, ETHTOOL):
            if not os.access(binary, os.X_OK):
                raise ValueError("required fixed network binary unavailable: " + binary)
        # No install/sudo/global capability mutation fallback. Actual creation also
        # fails explicitly if the predelegated namespace privileges are insufficient.
        p = self.plan
        if os.path.lexists("/run/netns/" + p.namespace):
            raise ValueError("namespace name occupied")
        rows = read_json(self._ip("-j", "address", "show"))
        net = ipaddress.IPv4Network(p.subnet)
        for row in rows:
            if row["ifname"] in {p.host_link, p.client_link}:
                raise ValueError("link name occupied")
            for address in row.get("addr_info", []):
                if address["family"] == "inet" and net.overlaps(
                    ipaddress.IPv4Network(
                        f"{address['local']}/{address['prefixlen']}", strict=False
                    )
                ):
                    raise ValueError("subnet address conflict")
        for route in read_json(self._ip("-j", "-4", "route", "show", "table", "all")):
            if route.get("dst", "default") != "default" and net.overlaps(
                ipaddress.IPv4Network(route["dst"], strict=False)
            ):
                raise ValueError("subnet route conflict")
        return {
            "kernel": platform.release(),
            "ip": run([IP, "-Version"]).decode(),
            "tc": run([TC, "-Version"]).decode(),
        }

    def _namespace_identity(self):
        path = Path("/run/netns") / self.plan.namespace
        info = path.stat(follow_symlinks=False)
        if path.is_symlink():
            raise ValueError("namespace symlink rejected")
        return {"device": info.st_dev, "inode": info.st_ino}

    def create(self):
        with self.ledger.control_lock:
            if self._rows("network-effect-intent"):
                raise ValueError(
                    "network create already consumed; ownership reconciliation required"
                )
            self._save("network-prerequisites", observed=self.preflight())
            p = self.plan
            self._effect("namespace-create", [IP, "netns", "add", p.namespace])
            # A lost create response before this binding cannot prove ownership.
            observed = self._namespace_identity()
            self.namespace_fd = os.open("/run/netns/" + p.namespace, os.O_RDONLY | os.O_NOFOLLOW)
            info = os.fstat(self.namespace_fd)
            if observed != {"device": info.st_dev, "inode": info.st_ino}:
                raise ValueError("namespace changed during descriptor binding")
            self._save("network-namespace", identity=observed)
            self._effect(
                "veth-create",
                self._ip(
                    "link",
                    "add",
                    p.host_link,
                    "alias",
                    p.alias,
                    "type",
                    "veth",
                    "peer",
                    "name",
                    p.client_link,
                    "alias",
                    p.alias,
                    "netns",
                    p.namespace,
                ),
            )
            host, client = self._links()
            pair = verify_links(p, host, client, configured=False)
            self._save("network-pair", identity=pair)
            for is_client, link, address in [
                (False, p.host_link, p.host_address),
                (True, p.client_link, p.client_address),
            ]:
                self._effect(
                    link + "-address",
                    self._ip("address", "add", address + "/30", "dev", link, client=is_client),
                )
                self._effect(
                    link + "-up", self._ip("link", "set", "dev", link, "up", client=is_client)
                )
            self._effect("loopback-up", self._ip("link", "set", "dev", "lo", "up", client=True))
            return self.observe(shaped=False)

    def _links(self):
        result = []
        for client, link in [(False, self.plan.host_link), (True, self.plan.client_link)]:
            rows = read_json(self._ip("-j", "-d", "address", "show", "dev", link, client=client))
            if len(rows) != 1:
                raise ValueError("missing/ambiguous owned link")
            result.append(rows[0])
        return result

    def reconcile(self):
        """Read only. Never invent ownership from names after a lost create."""
        namespaces, pairs = self._rows("network-namespace"), self._rows("network-pair")
        if len(namespaces) != 1 or len(pairs) != 1:
            raise ValueError(
                "incomplete network ownership; retain objects for operator reconciliation"
            )
        observed = self._namespace_identity()
        if observed != namespaces[0]["identity"]:
            raise ValueError("namespace ownership differs; retained")
        host, client = self._links()
        if verify_links(self.plan, host, client, configured=False) != pairs[0]["identity"]:
            raise ValueError("veth ownership differs; retained")
        return {"namespace": observed, "host": host, "client": client}

    def observe(self, *, shaped=True):
        observation = self.reconcile()
        p = self.plan
        verify_links(p, observation["host"], observation["client"])
        routes = read_json(self._ip("-j", "-4", "route", "show", "table", "main", client=True))
        if len(routes) != 1 or any(
            routes[0].get(k) != v
            for k, v in {
                "dst": p.subnet,
                "dev": p.client_link,
                "protocol": "kernel",
                "scope": "link",
                "prefsrc": p.client_address,
            }.items()
        ):
            raise ValueError("client connected-only route differs")
        all_routes = read_json(self._ip("-j", "-4", "route", "show", "table", "all", client=True))
        if any(
            r.get("dst", "default") == "default"
            or r.get("gateway")
            or r.get("dev") not in {"lo", p.client_link}
            for r in all_routes
        ):
            raise ValueError("unexpected client route/default gateway")
        rules = read_json(self._ip("-j", "-4", "rule", "show", client=True))
        if [(r.get("priority"), r.get("table")) for r in rules] != [
            (0, "local"),
            (32766, "main"),
            (32767, "default"),
        ]:
            raise ValueError("unexpected client policy routing")
        host_routes = read_json(self._ip("-j", "-4", "route", "show", "dev", p.host_link))
        if (
            len(host_routes) != 1
            or host_routes[0].get("dst") != p.subnet
            or host_routes[0].get("prefsrc") != p.host_address
        ):
            raise ValueError("owned host connected route differs")
        route6 = read_json(self._ip("-j", "-6", "route", "show", "table", "all", client=True))
        if any(r.get("dst", "default") == "default" for r in route6):
            raise ValueError("unexpected client IPv6 default route")
        observation.update(
            routes=routes,
            all_routes=all_routes,
            rules=rules,
            host_routes=host_routes,
            routes6=route6,
            host_ns=time.monotonic_ns(),
        )
        for client, link, label in [(False, p.host_link, "host"), (True, p.client_link, "client")]:
            rows = read_json(
                self._tc("-j", "-s", "-d", "qdisc", "show", "dev", link, client=client)
            )
            if not shaped and (len(rows) != 1 or rows[0].get("kind") != "noqueue"):
                raise ValueError("unshaped native baseline requires actual noqueue")
            observation[label + "_qdisc"] = verify_qdisc(rows) if shaped else rows
            command = [ETHTOOL, "--show-features", link]
            if client:
                command = [IP, "netns", "exec", p.namespace, *command]
            observation[label + "_offloads"] = run(command).decode()
        self._save("network-observation", shaped=shaped, observed=observation)
        return observation

    def shape(self):
        self.reconcile()
        for client, link in [(False, self.plan.host_link), (True, self.plan.client_link)]:
            self._effect(
                link + "-shape",
                self._tc(
                    "qdisc",
                    "add",
                    "dev",
                    link,
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
                    client=client,
                ),
            )
        return self.observe()

    def _require_active(self):
        if (
            self._rows("network-teardown-intent")
            or self._rows("network-released")
            or any(
                row["operation"] in {"veth-delete", "namespace-delete"}
                for row in self._rows("network-effect-intent")
            )
        ):
            raise ValueError("network teardown consumed; consumers and mutations fenced, retained")

    def reserve_consumer(self, consumer_id, *, client):
        with self.ledger.control_lock:
            self._require_active()
            if not isinstance(consumer_id, str) or not 1 <= len(consumer_id) <= 200:
                raise ValueError("bounded consumer identity required")
            if any(r["consumer_id"] == consumer_id for r in self._rows("network-consumer-intent")):
                raise ValueError("network consumer already reserved; retained")
            self._save("network-consumer-intent", consumer_id=consumer_id, client=client)

    def register_consumer(self, pid, expected, *, client, consumer_id):
        """Register independently created exact process before it may use the link.

        QEMU also holds host-veth listeners and must register as client=False.
        C3's native launcher must register all initial namespace consumers.
        """
        with self.ledger.control_lock:
            self._require_active()
            self.reconcile()
            intents = [
                r
                for r in self._rows("network-consumer-intent")
                if r["consumer_id"] == consumer_id and r["client"] is client
            ]
            if len(intents) != 1 or any(
                r["consumer_id"] == consumer_id for r in self._rows("network-consumer")
            ):
                raise ValueError("unique preregistered consumer required")
            fd = os.pidfd_open(pid)
            try:
                observed = process_snapshot(pid)
                if observed != expected:
                    raise ValueError("consumer process identity differs")
                ns = (Path("/proc") / str(pid) / "ns/net").stat()
                if (
                    client
                    and {"device": ns.st_dev, "inode": ns.st_ino} != self._namespace_identity()
                ):
                    raise ValueError("client process outside owned namespace")
                self._save(
                    "network-consumer", identity=observed, client=client, consumer_id=consumer_id
                )
                self.consumers[consumer_id] = (fd, observed)
            except BaseException:
                os.close(fd)
                raise

    def cleanup(self):
        """Freeze reservation/exit authority through every teardown effect/outcome."""
        with self.ledger.control_lock:
            self._require_active()
            try:
                self._cleanup()
            except BaseException as error:
                if self._rows("network-teardown-intent"):
                    self._save(
                        "network-teardown-error",
                        error=type(error).__name__,
                        disposition="retained",
                        host_ns=time.monotonic_ns(),
                    )
                raise

    def _cleanup(self):
        # Caller holds the shared lock. A reserved but unregistered/spawning
        # consumer cannot satisfy this proof and prevents starting teardown.
        self.reconcile()
        records = self._rows("network-consumer")
        if (
            not records
            or len(records) != len(self.consumers)
            or {r["consumer_id"] for r in records}
            != {r["consumer_id"] for r in self._rows("network-consumer-intent")}
        ):
            raise ValueError("consumer exit authority unavailable; retained")
        for record in records:
            fd, identity = self.consumers[record["consumer_id"]]
            poll = select.poll()
            poll.register(fd, select.POLLIN)
            if identity != record["identity"] or not poll.poll(0):
                raise ValueError("network consumer not observed exited; retained")
        self._save(
            "network-teardown-intent",
            reservations=self._rows("network-consumer-intent"),
            consumers=records,
            host_ns=time.monotonic_ns(),
        )
        if run([IP, "netns", "pids", self.plan.namespace]).strip():
            raise ValueError("namespace still contains consumers; retained")
        self._save("network-consumers-exited", identities=[r["identity"] for r in records])
        self.reconcile()
        self._effect("veth-delete", self._ip("link", "delete", "dev", self.plan.host_link))
        remaining = read_json(self._ip("-j", "link", "show", client=True))
        host = read_json(self._ip("-j", "link", "show"))
        if any(r["ifname"] != "lo" for r in remaining) or any(
            r["ifname"] == self.plan.host_link for r in host
        ):
            raise ValueError("veth absence unproved; retained")
        if self._namespace_identity() != self._rows("network-namespace")[0]["identity"]:
            raise ValueError("namespace replaced before deletion; retained")
        self._effect("namespace-delete", [IP, "netns", "delete", self.plan.namespace])
        if os.path.lexists("/run/netns/" + self.plan.namespace):
            raise ValueError("namespace deletion not observed; retained")
        if self.namespace_fd is not None:
            os.close(self.namespace_fd)
            self.namespace_fd = None
        for fd, _ in self.consumers.values():
            os.close(fd)
        self.consumers.clear()
        self._save("network-released", host_ns=time.monotonic_ns())
