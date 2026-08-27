from pathlib import Path


def test_image_venv_uses_the_installed_system_python_runtime() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    normalized = " ".join(dockerfile.split())

    assert "python3.12 python3.12-venv python3.12-dev" in normalized
    assert "--python /usr/bin/python3.12 --no-managed-python" in normalized
    assert "python3.10" not in dockerfile
