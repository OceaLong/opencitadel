"""Pure synthetic protocol tests; no physical measurements or processes."""

import pytest


def test_strict_json_rejects_duplicate_keys():
    from scripts.acceptance.capacity_io import strict_json

    with pytest.raises(ValueError, match="duplicate"):
        strict_json(b'{"schema_version":2,"schema_version":1}')


def test_shared_contract_is_version_three_and_rejects_legacy_absolute_timing():
    from scripts.acceptance.capacity_models import Protocol

    assert Protocol.model_fields["schema_version"].default == 3
    from scripts.acceptance.capacity_models import WindowPlan

    with pytest.raises(ValueError, match="coordinator_start_ns"):
        WindowPlan.model_validate(
            {
                "window_id": "w",
                "seconds": 2,
                "session_ids": [],
                "coordinator_start_ns": 1,
                "coordinator_end_ns": 2,
            }
        )


@pytest.mark.parametrize("data", [b'{"n":NaN}', b'{"n":Infinity}', b'{"n":-Infinity}'])
def test_strict_json_nonfinite(data):
    from scripts.acceptance.capacity_io import strict_json

    with pytest.raises(ValueError, match="non-finite"):
        strict_json(data)


def test_read_cannot_follow_replaced_parent(tmp_path, monkeypatch):
    import os

    from scripts.acceptance.capacity_io import read_relative

    root = tmp_path / "root"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "data").write_bytes(b"owned")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"foreign")
    original = os.open

    def race(path, flags, *args, **kwargs):
        if path == "nested":
            nested.rename(root / "old")
            nested.symlink_to(outside, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", race)
    with pytest.raises(OSError, match=r"directory|symbolic"):
        read_relative(root, "nested/data", 100)


def test_bounded_io_refuses_oversize_and_copy_does_not_truncate_hardlinks(tmp_path):
    import os

    from scripts.acceptance.capacity_io import read_bounded, write_relative

    original = tmp_path / "original"
    original.write_bytes(b"foreign")
    os.link(original, tmp_path / "target")
    with pytest.raises(ValueError, match="bounded"):
        read_bounded(original, 1)
    write_relative(tmp_path, "target", b"owned")
    assert original.read_bytes() == b"foreign"
    assert (tmp_path / "target").read_bytes() == b"owned"


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "a/../b", "a//b", "./b", "report.json", "a\\b"]
)
def test_unsafe_artifact_paths(tmp_path, path):
    from scripts.acceptance.capacity_io import safe_path

    with pytest.raises(ValueError, match="unsafe"):
        safe_path(tmp_path, path)


def test_old_shared_window_transcript_is_not_an_executable_physical_schedule():
    from capacity_synthetic import transcript
    from scripts.acceptance.capacity_models import Protocol

    roles, _ = transcript()
    assert len(roles["protocol"]["samples"]) == 1200
    assert len(roles["cleanup"]["rounds"]) == 201
    with pytest.raises(ValueError, match="physical window"):
        Protocol.model_validate(roles["protocol"])
