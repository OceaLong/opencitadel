"""A failed fixture image pull must not leak the newly created kind cluster."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("diagnostics_fail", [False, True])
def test_new_cluster_is_cleaned_when_initial_image_pull_fails(
    tmp_path: Path, diagnostics_fail: bool
) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    calls = tmp_path / "kind-calls"
    for name, script in {
        "kind": (
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$KIND_CALLS"\n'
            'if [ "$1" = export ] && [ "$DIAGNOSTICS_FAIL" = 1 ]; then exit 9; fi\n'
        ),
        "docker": '#!/bin/sh\necho "docker $*" >> "$KIND_CALLS"\nexit 17\n',
        "sleep": "#!/bin/sh\nexit 0\n",
        "kubectl": "#!/bin/sh\nexit 0\n",
        "jq": "#!/bin/sh\nexit 0\n",
        "uv": "#!/bin/sh\nexit 0\n",
    }.items():
        path = binary_dir / name
        path.write_text(script)
        path.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{binary_dir}:{os.environ['PATH']}",
        "KIND_CALLS": str(calls),
        "DIAGNOSTICS_FAIL": "1" if diagnostics_fail else "0",
        "PATROL_DEMO_CLUSTER_NAME": "opencitadel-patrol-cleanup-proof",
    }
    for name in ("PATROL_DEMO_CONTEXT", "PATROL_KEEP_DEMO_CLUSTER"):
        environment.pop(name, None)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run-patrol-fixtures.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 17, result.stderr
    assert "create cluster" in calls.read_text()
    assert "delete cluster --name opencitadel-patrol-cleanup-proof" in calls.read_text()

    assert "export logs" in calls.read_text()
    assert calls.read_text().count("docker pull") == 3
