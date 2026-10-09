"""Owned bounded stdout/stderr transport for fixed broker and physical reads."""

import os
import selectors
import subprocess
import time

from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError


class EvidenceTransport:
    def __init__(
        self,
        budget,
        *,
        process_factory=subprocess.Popen,
        frame_limit=1024 * 1024,
        output_limit=16 * 1024 * 1024,
        timeout=60,
        evidence=None,
    ):
        if any(type(n) is not int or n < 1 for n in (frame_limit, output_limit, timeout)):
            raise ValueError("positive evidence transport limits required")
        self.budget, self.process_factory = budget, process_factory
        self.frame_limit, self.output_limit, self.timeout = frame_limit, output_limit, timeout
        self.journal = None if evidence is None else evidence.journal
        self.originals = [] if self.journal is None else self.journal.sequence("transports")

    def __call__(self, *args):
        self.budget.charge(args)
        record = {
            "request": args,
            "start_ns": time.monotonic_ns(),
            "stdout": bytearray(),
            "stderr": bytearray(),
            "returncode": None,
            "error": None,
        }
        token = None
        if self.journal is None:
            self.originals.append(record)
        else:
            token = self.journal.begin(
                "transports", {"request": args, "start_ns": record["start_ns"]}
            )
        process, primary, output = None, None, None
        cleanup_errors = []
        try:
            process = self.process_factory(
                ["/usr/bin/docker", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
            )
            deadline = time.monotonic() + self.timeout
            frames, total = {"stdout": 0, "stderr": 0}, 0
            with selectors.DefaultSelector() as selector:
                for name in ("stdout", "stderr"):
                    selector.register(getattr(process, name), selectors.EVENT_READ, name)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("private transport timeout")
                    for key, _ in selector.select(min(remaining, 1)):
                        name = key.data
                        size = min(4096, self.output_limit - total + 1)
                        # Input + mutable prefix + final immutable representation.
                        self.budget.reserve(size * 3, rows=0)
                        chunk = os.read(key.fileobj.fileno(), size)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if self.journal is not None:
                            self.journal.note(
                                token, {"stream": name, "offset": len(record[name]), "data": chunk}
                            )
                        record[name].extend(chunk)
                        total += len(chunk)
                        if total > self.output_limit:
                            raise EvidenceQuotaError("private transport output quota exceeded")
                        for byte in chunk:
                            frames[name] = 0 if byte == 10 else frames[name] + 1
                            if frames[name] > self.frame_limit:
                                raise EvidenceQuotaError("private transport frame quota exceeded")
            record["returncode"] = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if record["returncode"] != 0 or record["stderr"]:
                raise ValueError("private transport nonzero exit or stderr")
            output = bytes(record["stdout"])
        except BaseException as error:  # noqa: BLE001 - preserve independent acquisition and cleanup failures
            record["error"] = type(error).__name__
            primary = error
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                    if record["returncode"] is None:
                        record["returncode"] = process.wait(timeout=5)
                except BaseException as error:  # noqa: BLE001 - preserve independent acquisition and cleanup failures
                    cleanup_errors.append(error)
                for name in ("stdout", "stderr"):
                    try:
                        getattr(process, name).close()
                    except BaseException as error:  # noqa: BLE001 - preserve independent acquisition and cleanup failures
                        cleanup_errors.append(error)
            record["stdout"] = bytes(record["stdout"])
            record["stderr"] = bytes(record["stderr"])
            record["cleanup_errors"] = [type(error).__name__ for error in cleanup_errors]
            record["end_ns"] = time.monotonic_ns()
            if self.journal is not None:
                try:
                    self.journal.complete(token, record)
                except BaseException as error:  # noqa: BLE001 - retain acquisition and durability errors together
                    cleanup_errors.append(error)
        if cleanup_errors:
            raise BaseExceptionGroup(
                "private transport acquisition/cleanup failures",
                ([primary] if primary is not None else []) + cleanup_errors,
            )
        if primary is not None:
            raise primary
        return output
