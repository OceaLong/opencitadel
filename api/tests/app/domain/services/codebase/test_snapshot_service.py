#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hashlib

import pytest

from app.domain.services.codebase.snapshot_service import CodeSnapshotService


@pytest.mark.asyncio
async def test_snapshot_is_content_addressed_for_identical_source_tree():
    service = CodeSnapshotService()
    source_tree = {
        "src/main.py": "def main(): pass\n",
        "README.md": "# Demo\n",
    }

    a = await service.create("cbv1", source_tree)
    b = await service.create("cbv2", dict(reversed(source_tree.items())))

    assert a.source_digest == b.source_digest
    assert a.snapshot_key == b.snapshot_key
    assert a.snapshot_key == f"codebase-snapshots/sha256/{a.source_digest}.tgz"
    assert a.snapshot_bytes == b.snapshot_bytes


@pytest.mark.asyncio
async def test_snapshot_from_sandbox_uses_content_addressed_key():
    class _Sandbox:
        create_workspace_snapshot_called_with = None

        async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
            self.create_workspace_snapshot_called_with = snapshot_id
            return b"snapshot-bytes"

    sandbox = _Sandbox()
    result = await CodeSnapshotService().create_from_sandbox("cbv1", sandbox)

    digest = hashlib.sha256(b"snapshot-bytes").hexdigest()
    assert result.source_digest == digest
    assert result.snapshot_key == f"codebase-snapshots/sha256/{digest}.tgz"
    assert result.snapshot_bytes == b"snapshot-bytes"
    assert sandbox.create_workspace_snapshot_called_with == "cbv1"
