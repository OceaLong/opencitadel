#!/usr/bin/env python3
"""Verify an OpenCitadel evidence package ZIP offline.

The platform signs each evidence package's ``manifest.json`` with
HMAC-SHA256(AUDIT_SIGNING_KEY, manifest.json bytes) and records the per-file
SHA-256 digests inside the manifest. Auditors previously had to reproduce this
by hand; this script does it in one step, without a running platform.

Usage:
    AUDIT_SIGNING_KEY=<key> python scripts/verify_evidence_package.py evidence.zip

Exit code 0 = verified, 1 = verification failed, 2 = usage/config error.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import zipfile

_SIG_RE = re.compile(r"manifest HMAC-SHA256:\s*([0-9a-f]+)")


def verify(zip_path: str, signing_key: str) -> tuple[bool, str]:
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names or "chain-signature.txt" not in names:
            return False, "package missing manifest.json or chain-signature.txt"
        manifest_bytes = zf.read("manifest.json")
        sig_text = zf.read("chain-signature.txt").decode("utf-8", "replace")

        match = _SIG_RE.search(sig_text)
        if not match:
            return False, "chain-signature.txt has no HMAC-SHA256 signature"
        expected = hmac.new(signing_key.encode("utf-8"), manifest_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, match.group(1)):
            return False, "manifest signature mismatch (wrong key or tampered manifest)"

        manifest = json.loads(manifest_bytes)
        file_hashes = manifest.get("file_hashes", {})
        if not isinstance(file_hashes, dict):
            return False, "manifest.file_hashes missing or malformed"
        for name, digest in file_hashes.items():
            if name not in names:
                return False, f"manifest references missing file: {name}"
            actual = hashlib.sha256(zf.read(name)).hexdigest()
            if not hmac.compare_digest(actual, str(digest)):
                return False, f"file hash mismatch: {name}"

    return True, f"verified manifest signature + {len(file_hashes)} file hashes"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: AUDIT_SIGNING_KEY=<key> "
            "python scripts/verify_evidence_package.py <evidence.zip>",
            file=sys.stderr,
        )
        return 2
    signing_key = os.environ.get("AUDIT_SIGNING_KEY", "")
    if not signing_key:
        print("error: AUDIT_SIGNING_KEY environment variable is required", file=sys.stderr)
        return 2
    try:
        ok, message = verify(argv[1], signing_key)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(("OK: " if ok else "FAIL: ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
