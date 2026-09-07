"""Log fixtures must wait for a running container before the collector reads it."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("wait_status", [0, 17])
def test_prompt_injection_fixture_waits_and_propagates_readiness_failure(tmp_path, wait_status):
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    calls = tmp_path / "calls"
    script = binary_dir / "kubectl"
    script.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in\n'
        '  *"get namespace"*) echo true ;;\n'
        '  *" wait "*) exit "$WAIT_STATUS" ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/patrol-demo/scripts/apply-fixture.sh"), "20-prompt-injection"],
        env={
            **os.environ,
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "WAIT_STATUS": str(wait_status),
            "PATROL_DEMO_CONTEXT": "kind-opencitadel-patrol-readiness",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    recorded = calls.read_text()
    assert "wait --for=condition=Ready pod/fixture-prompt-injection --timeout=120s" in recorded
    assert recorded.index("apply -f") < recorded.index("wait --for")
    assert result.returncode == wait_status, result.stderr
