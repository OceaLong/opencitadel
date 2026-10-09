"""Standalone provisioned calibration fixture. No product/DB imports or proxy.

Install exact bytes at /opt/opencitadel-capacity/calibration.py on host and guest.
Guest service reads a root-owned private 32-byte key; host passes the matching
key on stdin only after registering the actual owned client process. No secrets
are returned, logged, or put in argv. Import/help performs no physical operation.
"""

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import resource
import socket
import stat
import struct
import sys
import time
from pathlib import Path

PORT = 43191
TRANSFER_BYTES = 8 * 1024**2
BLOCK = b"\0" * 65536
ACTIONS = {"echo": b"E", "upload": b"U", "download": b"D"}


class DeadlineWire:
    """One absolute per-probe I/O deadline, including every partial read/write."""

    def __init__(self, wire, seconds):
        self.wire, self.deadline = wire, time.monotonic() + seconds

    def settimeout(self, seconds):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("calibration probe deadline")
        self.wire.settimeout(min(seconds, remaining))

    def recv(self, count):
        self.settimeout(15)
        return self.wire.recv(count)

    def sendall(self, value):
        self.settimeout(15)
        return self.wire.sendall(value)


def recv_exact(wire, count):
    result = bytearray()
    while len(result) < count:
        chunk = wire.recv(min(65536, count - len(result)))
        if not chunk:
            raise EOFError("calibration stream truncated")
        result.extend(chunk)
    return bytes(result)


def request(key, nonce, action):
    if action not in ACTIONS or len(key) != 32 or len(nonce) != 16:
        raise ValueError("fixed calibration action/key/nonce required")
    body = b"OCC1" + nonce + ACTIONS[action]
    return body + hmac.digest(key, body, "sha256")


def response_header(key, nonce, identity):
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 4096:
        raise ValueError("calibration identity exceeds bound")
    return struct.pack("!I", len(raw)) + raw + hmac.digest(key, nonce + raw, "sha256")


def serve_connection(wire, key, identity):
    wire.settimeout(15)
    header = recv_exact(wire, 53)
    body, signature = header[:21], header[21:]
    if body[:4] != b"OCC1" or not hmac.compare_digest(hmac.digest(key, body, "sha256"), signature):
        raise ValueError("calibration authentication failed")
    if body[20:] not in ACTIONS.values():
        raise ValueError("fixed calibration action required")
    wire.sendall(response_header(key, body[4:20], identity))
    if body[20:] == b"E":
        wire.sendall(recv_exact(wire, 32))
    elif body[20:] == b"D":
        for _ in range(TRANSFER_BYTES // len(BLOCK)):
            wire.sendall(BLOCK)
    else:
        received = 0
        while received < TRANSFER_BYTES:
            block = recv_exact(wire, min(len(BLOCK), TRANSFER_BYTES - received))
            if block != BLOCK[: len(block)]:
                raise ValueError("fixed upload contents required")
            received += len(block)
        wire.sendall(struct.pack("!Q", received))


def measure(wire, key, nonce, action, expected):
    start = time.monotonic_ns()
    wire.settimeout(15)
    wire.sendall(request(key, nonce, action))
    size = struct.unpack("!I", recv_exact(wire, 4))[0]
    if not 1 <= size <= 4096:
        raise ValueError("calibration response bound")
    raw = recv_exact(wire, size)
    if not hmac.compare_digest(recv_exact(wire, 32), hmac.digest(key, nonce + raw, "sha256")):
        raise ValueError("calibration response authentication failed")
    identity = json.loads(raw)
    if identity != expected:
        raise ValueError("calibration server identity differs")
    received = 0
    if action == "echo":
        payload = nonce * 2
        echo_start = time.monotonic_ns()
        wire.sendall(payload)
        if recv_exact(wire, 32) != payload:
            raise ValueError("echo differs")
        received = 32
    elif action == "download":
        while received < TRANSFER_BYTES:
            block = recv_exact(wire, min(len(BLOCK), TRANSFER_BYTES - received))
            if block != BLOCK[: len(block)]:
                raise ValueError("download contents differ")
            received += len(block)
    elif action == "upload":
        for _ in range(TRANSFER_BYTES // len(BLOCK)):
            wire.sendall(BLOCK)
        received = struct.unpack("!Q", recv_exact(wire, 8))[0]
        if received != TRANSFER_BYTES:
            raise ValueError("upload receiver byte count differs")
    else:
        raise ValueError("fixed calibration action required")
    end = time.monotonic_ns()
    elapsed = end - start
    if elapsed <= 0:
        raise ValueError("nonpositive calibration elapsed time")
    return {
        "action": action,
        "bytes": received,
        "elapsed_ns": elapsed,
        "bits_per_second": received * 8 * 1_000_000_000 / elapsed,
        "server": identity,
        "start_ns": start,
        "end_ns": end,
        "echo_rtt_ns": end - echo_start if action == "echo" else None,
    }


def read_key(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
        ):
            raise ValueError("private calibration key ownership differs")
        key = handle.read(33)
    if len(key) != 32:
        raise ValueError("32-byte calibration key required")
    return key


def service_identity():
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": os.getpid(),
        "start_ticks": int(fields[19]),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "service_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def serve():
    key = read_key("/etc/opencitadel-capacity-calibration.key")
    identity = service_identity()
    # Fixed private slirp guest address; VMPlan forwards this exact port from veth.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("10.0.2.15", PORT))
        listener.listen(4)
        listener.settimeout(15)
        deadline = time.monotonic() + 600
        for _ in range(128):
            if time.monotonic() >= deadline:
                break
            try:
                wire, _ = listener.accept()
            except TimeoutError:
                continue
            with wire:
                # Fail closed and exit on malformed/authentication requests.
                serve_connection(DeadlineWire(wire, 15), key, identity)


def client():
    # Parent authenticates this process, binds resource allocation and records
    # namespace/pidfd ownership before delivering any destination or secret.
    print(json.dumps({"ready_pid": os.getpid()}), flush=True)
    raw = sys.stdin.buffer.readline(32769)
    if len(raw) > 32768:
        raise ValueError("calibration request exceeds bound")
    config = json.loads(raw)
    if set(config) != {"host_address", "client_address", "key_hex", "server"}:
        raise ValueError("fixed calibration client request required")
    host = ipaddress.IPv4Address(config["host_address"])
    client_address = ipaddress.IPv4Address(config["client_address"])
    if not host.is_private or host not in ipaddress.IPv4Network(
        f"{client_address}/30", strict=False
    ):
        raise ValueError("owned /30 calibration path required")
    key = bytes.fromhex(config["key_hex"])
    start = time.monotonic_ns()
    rows, errors = [], []
    before = resource.getrusage(resource.RUSAGE_SELF)
    for index, action in enumerate(["echo"] * 16 + ["upload", "download"]):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as wire:
                wire.settimeout(5)
                wire.bind((str(client_address), 0))
                connect_start = time.monotonic_ns()
                wire.connect((str(host), PORT))
                connected = time.monotonic_ns()
                row = measure(DeadlineWire(wire, 15), key, os.urandom(16), action, config["server"])
                row.update(
                    index=index,
                    connect_ns=connected - connect_start,
                    local=list(wire.getsockname()),
                    peer=list(wire.getpeername()),
                )
                rows.append(row)
        except (OSError, ValueError, EOFError) as exc:
            errors.append({"index": index, "action": action, "error": type(exc).__name__})
            break  # no retries or replacement observations
    after = resource.getrusage(resource.RUSAGE_SELF)
    print(
        json.dumps(
            {
                "rows": rows,
                "errors": errors,
                "elapsed_ns": time.monotonic_ns() - start,
                "attempted": len(rows) + len(errors),
                "expected": 18,
                "user_cpu_s": after.ru_utime - before.ru_utime,
                "system_cpu_s": after.ru_stime - before.ru_stime,
                "max_rss_kib": after.ru_maxrss,
                "packet_loss": None,
                "packet_loss_reason": "TCP delivery does not measure packet loss",
            }
        ),
        flush=True,
    )
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description="Fixed provisioned capacity calibration fixture")
    parser.add_argument("action", choices=("serve", "client"))
    action = parser.parse_args().action
    return serve() if action == "serve" else client()


if __name__ == "__main__":
    raise SystemExit(main())
