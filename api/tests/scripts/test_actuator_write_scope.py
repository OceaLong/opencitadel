"""Exercise the permission gate against both identities and excess grants."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
REAL = "system:serviceaccount:opencitadel:opencitadel-ops-actuator"
DEMO = "opencitadel-patrol-demo"
DUMMY = f"system:serviceaccount:{DEMO}:patrol-actuator"


def run_gate(tmp_path, override=None):
    binary = tmp_path / "kubectl"
    calls = tmp_path / "calls.jsonl"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "verb, resource = args[4:6]\n"
        "identity = next(a.split('=', 1)[1] for a in args if a.startswith('--as='))\n"
        "namespace = args[args.index('-n') + 1]\n"
        "query = [identity, namespace, verb, resource]\n"
        "with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(query) + '\\n')\n"
        "allowed = namespace == 'opencitadel-patrol-demo' and verb == 'patch' and resource in ['deployments', 'statefulsets']\n"
        "override = json.loads(os.environ['OVERRIDE'])\n"
        "if override and query == override[:4]: allowed = override[4]\n"
        "print('yes' if allowed else 'no')\n"
        "sys.exit(0 if allowed else 1)\n"
    )
    binary.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/patrol-demo/scripts/assert-actuator-write-scope.sh")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "PATROL_DEMO_CONTEXT": "kind-opencitadel-patrol-scope-test",
            "CALLS": str(calls),
            "OVERRIDE": json.dumps(override),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, [json.loads(line) for line in calls.read_text().splitlines()]


def test_both_actuator_identities_and_namespace_boundaries_are_checked(tmp_path):
    result, calls = run_gate(tmp_path)
    assert result.returncode == 0, result.stderr
    for identity in (DUMMY, REAL):
        for resource in ("deployments", "statefulsets"):
            assert [identity, DEMO, "patch", resource] in calls
        for namespace in ("default", "kube-system"):
            for resource in ("deployments", "statefulsets", "secrets"):
                assert [identity, namespace, "patch", resource] in calls
            for verb in ("get", "list", "watch"):
                assert [identity, namespace, verb, "secrets"] in calls


@pytest.mark.parametrize(
    "override",
    [
        [REAL, DEMO, "patch", "deployments", False],
        [REAL, DEMO, "delete", "deployments", True],
        [REAL, DEMO, "get", "secrets", True],
        [REAL, "default", "patch", "statefulsets", True],
        [REAL, "kube-system", "patch", "deployments", True],
        [REAL, "default", "list", "secrets", True],
        [REAL, "kube-system", "patch", "secrets", True],
        [REAL, "kube-system", "watch", "secrets", True],
    ],
)
def test_real_actuator_missing_or_excess_permissions_fail_gate(tmp_path, override):
    result, _ = run_gate(tmp_path, override)
    assert result.returncode != 0
