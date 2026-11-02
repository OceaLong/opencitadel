"""Pipe fixtures exercise bounded process output without spawning any process."""

import os

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError


@pytest.mark.parametrize(
    ("data", "stderr", "code", "failure"),
    [
        (b"one\ntwo\n", b"", 0, None),
        (b"0123456789", b"", 0, EvidenceQuotaError),
        (b"one\n", b"error", 4, ValueError),
    ],
)
def test_framed_transport_keeps_prefix_and_owns_failed_process(data, stderr, code, failure):
    from scripts.execution_capacity.evidence_transport import EvidenceTransport

    class Process:
        killed = False
        waited = False

        def __init__(self):
            self.stdout = self.pipe(data)
            self.stderr = self.pipe(stderr)

        @staticmethod
        def pipe(value):
            reader, writer = os.pipe()
            os.write(writer, value)
            os.close(writer)
            return os.fdopen(reader, "rb")

        def poll(self):
            return code

        def wait(self, timeout=None):
            self.waited = True
            return code

        def kill(self):
            self.killed = True

    process = Process()
    transport = EvidenceTransport(
        EvidenceBudget(),
        process_factory=lambda *args, **kwargs: process,
        frame_limit=8,
        output_limit=32,
    )
    if failure is None:
        assert transport("exec", "owned", "read") == b"one\ntwo\n"
    else:
        with pytest.raises(failure):
            transport("exec", "owned", "read")
        assert transport.originals[0]["error"] is not None
        assert transport.originals[0]["stdout"] or transport.originals[0]["stderr"]
    assert process.waited
    assert process.stdout.closed
    assert process.stderr.closed


def test_primary_and_independent_close_failures_keep_prefix():
    from scripts.execution_capacity.evidence_transport import EvidenceTransport

    class Pipe:
        def __init__(self):
            reader, writer = os.pipe()
            os.write(writer, b"overflow-original")
            os.close(writer)
            self.stream = os.fdopen(reader, "rb")

        def fileno(self):
            return self.stream.fileno()

        def close(self):
            self.stream.close()
            raise OSError("fixture-close")

    class Process:
        stdout, stderr = Pipe(), Pipe()

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout):
            return -9

    transport = EvidenceTransport(
        EvidenceBudget(), process_factory=lambda *a, **k: Process(), frame_limit=2
    )
    with pytest.raises(BaseExceptionGroup) as raised:
        transport("exec", "fixture", "read")
    assert isinstance(raised.value.exceptions[0], EvidenceQuotaError)
    assert len(raised.value.exceptions) == 3
    assert transport.originals[0]["stdout"] or transport.originals[0]["stderr"]
    assert transport.originals[0]["cleanup_errors"] == ["OSError", "OSError"]
