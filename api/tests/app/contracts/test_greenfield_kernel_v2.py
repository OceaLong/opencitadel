"""Executable boundaries for the focused greenfield product and kernel."""

from __future__ import annotations

from pathlib import Path

API_ROOT = Path(__file__).parents[3]

RETIRED_ROOTS = (
    "app/domain/execution",
    "app/application/execution",
    "app/infrastructure/execution",
    "app/application/services/patrol_run_service.py",
    "app/application/services/patrol_remediation_service.py",
    "app/application/services/scheduled_job_service.py",
    "app/interfaces/endpoints/patrol_routes.py",
    "app/interfaces/endpoints/compliance_routes.py",
    "app/interfaces/endpoints/a2a_routes.py",
)


def test_old_kernel_and_retired_product_roots_are_absent() -> None:
    """A retired subsystem must not remain as a callable second authority."""

    present = [path for path in RETIRED_ROOTS if (API_ROOT / path).exists()]

    assert present == []


def test_new_kernel_has_only_approved_layers() -> None:
    """The new kernel exposes four explicit dependency layers."""

    root = API_ROOT / "app/kernel"

    assert root.is_dir()
    assert {
        path.name for path in root.iterdir() if path.is_dir() and path.name != "__pycache__"
    } == {
        "application",
        "domain",
        "infrastructure",
        "interfaces",
    }
