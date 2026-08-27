from pathlib import Path

import yaml

ROOT = Path(__file__).parents[4]


def test_every_collector_rbac_template_excludes_write_secret_exec_and_impersonate():
    manifests = [
        ROOT / "deploy" / "patrol-demo" / "manifests" / "collector-rbac.yaml",
        ROOT / "deploy" / "kustomize" / "ops-collector" / "rbac.yaml",
        ROOT / "deploy" / "helm" / "opencitadel" / "templates" / "rbac-ops-collector.yaml",
    ]
    forbidden_verbs = {"create", "update", "patch", "delete", "deletecollection", "impersonate"}
    forbidden_resources = {"secrets", "pods/exec", "pods/attach"}
    for path in manifests:
        text = path.read_text()
        assert not any(f'"{value}"' in text for value in forbidden_verbs)
        assert not any(f'"{value}"' in text for value in forbidden_resources)
        if "{{" not in text:
            roles = [
                item
                for item in yaml.safe_load_all(text)
                if item and item.get("kind") in {"Role", "ClusterRole"}
            ]
            assert roles
            assert all(
                set(rule["verbs"]) <= {"get", "list", "watch"}
                for role in roles
                for rule in role["rules"]
            )
