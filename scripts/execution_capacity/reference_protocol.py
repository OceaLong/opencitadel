"""Bounded actual QMP/QGA JSON transport; no effects at import.

A lost response poisons the connection. Never resend an uncertain guest-exec or
VM operation: use a new channel and independently reconcile its owned identity.
"""

import base64
import json
import socket
import stat
import struct
import time
from enum import StrEnum
from pathlib import Path


class ProtocolError(RuntimeError):
    pass


class Action(StrEnum):
    START = "cold-window"
    STATUS = "status"
    STAMP = "stamp"
    READY = "client-ready"
    DONE = "client-done"
    ABORT = "abort"
    PROGRESS = "progress"
    RESULT = "result"
    INFRASTRUCTURE = "infrastructure"
    SEAL = "seal"


class JsonChannel:
    def __init__(self, wire, *, timeout=2, max_bytes=4 * 1024 * 1024):
        if not 0 < timeout <= 30 or not 1 <= max_bytes <= 32 * 1024 * 1024:
            raise ValueError("invalid protocol bounds")
        self.wire, self.timeout, self.max_bytes = wire, timeout, max_bytes
        self.buffer, self.events, self.sequence, self.poisoned = b"", [], 0, False

    def _read(self, deadline):
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("protocol deadline")
            self.wire.settimeout(min(self.timeout, remaining))
            data = self.wire.recv(min(65536, self.max_bytes + 1 - len(self.buffer)))
            if not data:
                raise ProtocolError("protocol channel closed")
            self.buffer += data
            if len(self.buffer) > self.max_bytes:
                raise ProtocolError("protocol message exceeds bound")
        line, self.buffer = self.buffer.split(b"\n", 1)
        # QGA sync-delimited response contains the documented 0xff delimiter.
        line = line.removeprefix(b"\xff")
        result = json.loads(
            line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON"))
        )
        if not isinstance(result, dict):
            raise ProtocolError("protocol object required")
        return result

    def negotiate_qmp(self):
        greeting = self._read(time.monotonic() + self.timeout)
        if set(greeting) != {"QMP"}:
            raise ProtocolError("missing QMP greeting")
        self.greeting = greeting["QMP"]
        self.command("qmp_capabilities")

    def command(self, command, arguments=None):
        if self.poisoned:
            raise ProtocolError("uncertain prior operation; reconciliation required")
        self.sequence += 1
        request = {"execute": command, "id": self.sequence}
        if arguments is not None:
            request["arguments"] = arguments
        self.poisoned = True
        deadline = time.monotonic() + self.timeout
        self.wire.settimeout(self.timeout)
        self.wire.sendall(json.dumps(request, allow_nan=False).encode() + b"\n")
        event_bytes = 0
        while True:
            row = self._read(deadline)
            if "event" in row and "id" not in row:
                event_bytes += len(json.dumps(row))
                if event_bytes > self.max_bytes or len(self.events) >= 4096:
                    raise ProtocolError("QMP event bound exceeded")
                self.events.append(row)
                continue
            if row.get("id") != self.sequence:
                raise ProtocolError("protocol response identity differs")
            if "error" in row:
                self.poisoned = False
                raise ProtocolError(
                    "QEMU command error: " + str(row["error"].get("class", "unknown"))
                )
            if "return" not in row:
                raise ProtocolError("missing protocol return")
            self.poisoned = False
            return row["return"]


def connect_owned(path: Path, *, pid, uid, device, inode, timeout=2):
    """Open only an independently recorded socket; peer must be exact QEMU PID."""
    before = path.lstat()
    if (
        not stat.S_ISSOCK(before.st_mode)
        or before.st_uid != uid
        or (before.st_dev, before.st_ino) != (device, inode)
    ):
        raise ProtocolError("socket ownership changed")
    wire = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        wire.settimeout(timeout)
        wire.connect(str(path))
        peer = struct.unpack("3i", wire.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        after = path.lstat()
        if peer[:2] != (pid, uid) or (after.st_dev, after.st_ino) != (device, inode):
            raise ProtocolError("socket peer identity differs")
        return JsonChannel(wire, timeout=timeout)
    except BaseException:
        wire.close()
        raise


class GuestAgent:
    """Fixed provisioned OS helper, which validates/executes the owned container."""

    def __init__(self, channel):
        self.channel = channel

    def synchronize(self, nonce):
        if type(nonce) is not int or not 0 < nonce < 2**53:
            raise ValueError("integer synchronization nonce required")
        # The delimiter also clears any partial request buffered by QGA.
        self.channel.wire.sendall(b"\xff")
        if self.channel.command("guest-sync-delimited", {"id": nonce}) != nonce:
            raise ProtocolError("guest sync nonce differs")
        info = self.channel.command("guest-info")
        enabled = {
            c["name"]
            for c in info["supported_commands"]
            if c["enabled"] and c.get("success-response", True)
        }
        if not {"guest-exec", "guest-exec-status", "guest-sync-delimited"} <= enabled:
            raise ProtocolError("required QGA commands unavailable")
        return info

    def execute(self, action: Action, request):
        action = Action(action)
        raw = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode()) > 32768:
            raise ValueError("fixed helper request exceeds bound")
        result = self.channel.command(
            "guest-exec",
            {
                "path": "/usr/bin/python3",
                "arg": ["-I", "/opt/opencitadel-capacity/guest_bridge.py", action.value, raw],
                "capture-output": action != Action.START,
            },
        )
        pid = result.get("pid")
        if type(pid) is not int or pid <= 0:
            raise ProtocolError("missing exact guest exec PID")
        return pid

    def shutdown(self):
        """Fixed QGA powerdown has no success reply; only exact QEMU exit settles it."""
        if self.channel.poisoned:
            raise ProtocolError("uncertain QGA channel cannot power down")
        info = self.channel.command("guest-info")
        supported = [c for c in info["supported_commands"] if c["name"] == "guest-shutdown"]
        if (
            len(supported) != 1
            or supported[0]["enabled"] is not True
            or supported[0].get("success-response") is not False
        ):
            raise ProtocolError("fixed QGA shutdown capability unavailable")
        self.channel.poisoned = True
        self.channel.wire.settimeout(self.channel.timeout)
        self.channel.wire.sendall(
            b'{"execute":"guest-shutdown","arguments":{"mode":"powerdown"}}\n'
        )

    def status(self, pid):
        if type(pid) is not int or pid <= 0:
            raise ValueError("invalid guest PID")
        result = self.channel.command("guest-exec-status", {"pid": pid})
        if result.get("exited") is False:
            return None
        if (
            result.get("exited") is not True
            or result.get("exitcode") != 0
            or result.get("signal") is not None
        ):
            raise ProtocolError("guest helper failed; retain command identity")
        if result.get("out-truncated") or result.get("err-truncated"):
            raise ProtocolError("guest helper output truncated")
        output = base64.b64decode(result.get("out-data", ""), validate=True)
        error = base64.b64decode(result.get("err-data", ""), validate=True)
        if len(output) > 1024 * 1024 or error:
            raise ProtocolError("guest helper output invalid")
        return output
