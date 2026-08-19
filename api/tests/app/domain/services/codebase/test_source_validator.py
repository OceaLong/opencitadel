#!/usr/bin/env python
# -*- coding: utf-8 -*-
import io
import stat
import zipfile

import pytest

from app.domain.errors import BadRequestError
from app.domain.models.codebase import CodebaseSourceType
from app.domain.services.codebase.source_validator import (
    CodebaseSourceValidator,
    normalize_contained_path,
)


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _symlink_zip() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("link.py")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target.py")
    return stream.getvalue()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ssh://git@example.com/repo",
        "https://user:pass@example.com/repo.git",
        "https://example.com:8443/repo.git",
        "https://127.0.0.1/repo.git",
        "https://10.0.0.5/repo.git",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/repo.git",
    ],
)
def test_unsafe_git_url_is_rejected(url):
    validator = CodebaseSourceValidator(
        resolver=lambda _host, _port: ["93.184.216.34"]
    )

    with pytest.raises(BadRequestError):
        validator.validate_git_url(url)


def test_git_url_rejects_private_resolved_addresses():
    validator = CodebaseSourceValidator(
        resolver=lambda _host, _port: ["93.184.216.34", "10.0.0.2"]
    )

    with pytest.raises(BadRequestError):
        validator.validate_git_url("https://example.com/repo.git")


def test_safe_https_git_url_is_normalized():
    validator = CodebaseSourceValidator(
        resolver=lambda _host, _port: ["93.184.216.34"]
    )

    assert (
        validator.validate_git_url("https://example.com/org/repo.git")
        == "https://example.com/org/repo.git"
    )


@pytest.mark.parametrize(
    "path",
    ["../secret", "src/../../secret", "/etc/passwd", "src//../secret"],
)
def test_source_path_cannot_escape_root(path):
    with pytest.raises(BadRequestError):
        normalize_contained_path("/workspace/codebase", path)


def test_source_path_normalizes_safe_relative_path():
    assert str(
        normalize_contained_path("/workspace/codebase", "src/main.py")
    ) == "/workspace/codebase/src/main.py"


@pytest.mark.parametrize(
    "entries",
    [
        {"/abs.py": "x"},
        {"../escape.py": "x"},
        {"src/../../escape.py": "x"},
    ],
)
def test_zip_member_path_cannot_escape(entries):
    validator = CodebaseSourceValidator()

    with pytest.raises(BadRequestError):
        validator.validate_zip_bytes(_zip_bytes(entries))


def test_zip_symlink_member_is_rejected():
    validator = CodebaseSourceValidator()

    with pytest.raises(BadRequestError):
        validator.validate_zip_bytes(_symlink_zip())


def test_zip_entry_count_size_and_ratio_limits_are_enforced():
    with pytest.raises(BadRequestError):
        CodebaseSourceValidator(max_zip_entries=1).validate_zip_bytes(
            _zip_bytes({"a.py": "x", "b.py": "x"})
        )

    with pytest.raises(BadRequestError):
        CodebaseSourceValidator(max_zip_uncompressed_bytes=3).validate_zip_bytes(
            _zip_bytes({"a.py": "abcd"})
        )

    with pytest.raises(BadRequestError):
        CodebaseSourceValidator(max_zip_compression_ratio=1).validate_zip_bytes(
            _zip_bytes({"a.py": "a" * 1000})
        )


def test_validate_create_rejects_missing_source_payload():
    validator = CodebaseSourceValidator()

    with pytest.raises(BadRequestError):
        validator.validate_source_shape(CodebaseSourceType.ZIP, file_id=None)
    with pytest.raises(BadRequestError):
        validator.validate_source_shape(CodebaseSourceType.FILES, file_ids=[])
    with pytest.raises(BadRequestError):
        validator.validate_source_shape(CodebaseSourceType.GIT, git_url="")
