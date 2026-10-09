"""Private complete Minio inventory through the version-bound signed SDK transport.

No mutation or receipt recovery. Actual writer exits must precede this reader;
empty multipart alone says nothing about a single PUT still running elsewhere.
"""

import base64
import time
from dataclasses import dataclass, field
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import minio.api
import minio.datatypes
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.evidence_bounds import PHASE_BYTES, EvidenceBudget

_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _text(node, name, *, empty=False):
    rows = node.findall(_NS + name)
    if len(rows) != 1 or (not empty and not rows[0].text):
        raise ValueError("missing/duplicate S3 " + name)
    return rows[0].text or ""


def _key(value):
    import re

    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("malformed encoded object key")
    return unquote(value, encoding="utf-8", errors="strict")


def _integer(node, name):
    value = _text(node, name)
    if not value.isascii() or not value.isdigit():
        raise ValueError("invalid S3 integer")
    return int(value)


def _truncated(node):
    value = _text(node, "IsTruncated")
    if value not in {"true", "false"} or node.findall(_NS + "CommonPrefixes"):
        raise ValueError("invalid/truncated delimiter inventory")
    return value == "true"


@dataclass
class StorageInventory:
    objects: list = field(default_factory=list)
    uploads: list = field(default_factory=list)
    pages: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    complete: bool = False

    def require_quiescent(self, expected):
        if not self.complete or self.errors:
            raise ValueError("storage inventory incomplete")
        if self.uploads:
            raise ValueError("multipart uploads remain pending")
        actual = {r["key"]: {"sha256": r["sha256"], "size": r["size"]} for r in self.objects}
        if actual != expected:
            raise ValueError("exact object inventory/content differs")

    def safe(self):
        return {
            "complete": self.complete,
            "objects": len(self.objects),
            "uploads": len(self.uploads),
            "pages": len(self.pages),
            "digest": canonical_digest(
                {"objects": self.objects, "uploads": self.uploads, "pages": self.pages}
            ),
            "errors": self.errors,
        }


class MinioInventory:
    def __init__(
        self,
        client,
        bucket,
        *,
        page_bytes=4 * 1024 * 1024,
        max_pages=1_000_000,
        retained_bytes=PHASE_BYTES,
        budget=None,
    ):
        from scripts.execution_capacity.evidence_files import stream_file

        self.unit_budget = (
            budget
            if budget is not None
            else EvidenceBudget(bytes_limit=32 * 1024 * 1024, row_limit=4 * 1024 * 1024)
        )
        hashed = sha256()
        for filename in (minio.api.__file__, minio.datatypes.__file__):
            stream_file(Path(filename), budget=self.unit_budget, consumer=hashed.update)
        sdk_digest = hashed.hexdigest()
        if (
            version("minio") != "7.2.20"
            or sdk_digest != "dc234dd5402dfae14a7774ac3899c9a31f31c98bf7e60d8307e51f2b1e82f0aa"
        ):
            raise ValueError("unsupported Minio SDK for private signed transport")
        if (
            not bucket
            or not 0 < page_bytes <= 4 * 1024 * 1024
            or not 0 < max_pages <= 1_000_000
            or not 0 < retained_bytes <= PHASE_BYTES
        ):
            raise ValueError("invalid bounded storage reader")
        self.client, self.bucket = client, bucket
        self.page_bytes, self.max_pages = page_bytes, max_pages
        self.result = StorageInventory()
        self.retention = self.unit_budget.child(
            bytes_limit=retained_bytes, rows_limit=max_pages, row_limit=page_bytes
        )

    def _page(self, query, kind):
        if len(self.result.pages) >= self.max_pages:
            raise ValueError("storage page limit exceeded, inventory incomplete")
        self.unit_budget.check(4 * (self.page_bytes + 1), rows=1)
        started_ns = time.monotonic_ns()
        response = self.client._execute(
            "GET", self.bucket, query_params=query, preload_content=False
        )
        try:
            raw = response.read(self.page_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        self.retention.charge_bytes(len(raw))
        self.unit_budget.reserve(3 * len(raw), rows=0)
        self.result.pages.append(
            {
                "request": dict(query),
                "kind": kind,
                "raw_base64": base64.b64encode(raw).decode(),
                "start_ns": started_ns,
                "end_ns": time.monotonic_ns(),
                "sha256": sha256(raw).hexdigest(),
                "bytes": len(raw),
                "ordinal": len(self.result.pages),
            }
        )
        if len(raw) > self.page_bytes or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise ValueError("invalid bounded S3 XML")
        self.retention.reserve(64 * len(raw), rows=0)
        root = ET.fromstring(raw)
        if root.tag != _NS + kind:
            raise ValueError("wrong S3 result")
        return root

    def _get(self, key, size):
        if type(size) is not int or size < 0:
            raise ValueError("invalid listed object size")
        # Reserve the complete transfer plus two simultaneously live bounded
        # chunks and an EOF sentinel before even acquiring the provider body.
        window = min(1024 * 1024, size + 1)
        self.unit_budget.reserve(size + 2 * window + 1, rows=1)
        response = self.client.get_object(self.bucket, key)
        digest, length = sha256(), 0
        try:
            while True:
                request = min(window, size - length + 1)
                data = response.read(request)
                if not data:
                    break
                if len(data) > request:
                    raise ValueError("object transport exceeded requested chunk")
                length += len(data)
                digest.update(data)
                if length > size:
                    raise ValueError("object changed during inventory GET")
        finally:
            try:
                response.close()
            finally:
                response.release_conn()
        if length != size:
            raise ValueError("object length differs from list")
        return digest.hexdigest()

    def _objects(self):
        token, seen_tokens, previous = None, set(), None
        while True:
            query = {"list-type": "2", "max-keys": "1000", "encoding-type": "url"}
            if token is not None:
                query["continuation-token"] = token
            root = self._page(query, "ListBucketResult")
            if _text(root, "Name") != self.bucket or _text(root, "EncodingType") != "url":
                raise ValueError("foreign object bucket/encoding")
            rows = root.findall(_NS + "Contents")
            if _integer(root, "KeyCount") != len(rows) or len(rows) > 1000:
                raise ValueError("object page count differs")
            for row in rows:
                key = _key(_text(row, "Key"))
                if previous is not None and key.encode() <= previous.encode():
                    raise ValueError("duplicate/nonadvancing object key")
                size = _integer(row, "Size")
                entry = {"key": key, "size": size, "sha256": None, "etag": _text(row, "ETag")}
                self.result.objects.append(entry)
                entry["sha256"] = self._get(key, size)
                previous = key
            if not _truncated(root):
                return
            token = _text(root, "NextContinuationToken")
            if not rows or token in seen_tokens:
                raise ValueError("nonadvancing object pagination")
            seen_tokens.add(token)

    def _parts(self, key, upload, parts):
        marker = 0
        while True:
            root = self._parts_page(key, upload, marker)
            if (
                _text(root, "Bucket") != self.bucket
                or _text(root, "Key") != key
                or _text(root, "UploadId") != upload
                or _integer(root, "PartNumberMarker") != marker
            ):
                raise ValueError("foreign multipart parts identity")
            rows = root.findall(_NS + "Part")
            if len(rows) > 1000:
                raise ValueError("parts page too large")
            for row in rows:
                number = _integer(row, "PartNumber")
                if number <= marker:
                    raise ValueError("nonadvancing multipart part")
                parts.append(
                    {"number": number, "size": _integer(row, "Size"), "etag": _text(row, "ETag")}
                )
                marker = number
            if not _truncated(root):
                return parts
            if not rows or _integer(root, "NextPartNumberMarker") != marker:
                raise ValueError("invalid multipart part continuation")

    def _parts_page(self, key, upload, marker):
        # Unlike bucket lists, ListParts addresses the exact object path. The SDK
        # retains its ordinary credential provider and SigV4 implementation.
        query = {"uploadId": upload, "part-number-marker": str(marker), "max-parts": "1000"}
        if len(self.result.pages) >= self.max_pages:
            raise ValueError("storage page limit exceeded")
        self.unit_budget.check(4 * (self.page_bytes + 1), rows=1)
        started_ns = time.monotonic_ns()
        response = self.client._execute(
            "GET", self.bucket, object_name=key, query_params=query, preload_content=False
        )
        try:
            raw = response.read(self.page_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        self.retention.charge_bytes(len(raw))
        self.unit_budget.reserve(3 * len(raw), rows=0)
        self.result.pages.append(
            {
                "request": query,
                "kind": "ListPartsResult",
                "raw_base64": base64.b64encode(raw).decode(),
                "start_ns": started_ns,
                "end_ns": time.monotonic_ns(),
                "key": key,
                "sha256": sha256(raw).hexdigest(),
                "bytes": len(raw),
                "ordinal": len(self.result.pages),
            }
        )
        if len(raw) > self.page_bytes or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise ValueError("invalid bounded S3 XML")
        self.retention.reserve(64 * len(raw), rows=0)
        root = ET.fromstring(raw)
        if root.tag != _NS + "ListPartsResult":
            raise ValueError("wrong parts result")
        return root

    def _uploads(self):
        marker, seen, previous = ("", ""), set(), None
        while True:
            query = {"uploads": "", "max-uploads": "1000", "encoding-type": "url"}
            if marker != ("", ""):
                query.update({"key-marker": marker[0], "upload-id-marker": marker[1]})
            root = self._page(query, "ListMultipartUploadsResult")
            if (
                _text(root, "Bucket") != self.bucket
                or _text(root, "EncodingType") != "url"
                or (
                    _key(_text(root, "KeyMarker", empty=True)),
                    _text(root, "UploadIdMarker", empty=True),
                )
                != marker
            ):
                raise ValueError("foreign multipart bucket/cursor")
            rows = root.findall(_NS + "Upload")
            if len(rows) > 1000:
                raise ValueError("multipart page too large")
            for row in rows:
                key, uid = _key(_text(row, "Key")), _text(row, "UploadId")
                pair = (key, uid)
                # Upload IDs are opaque; only key ordering and exact pair uniqueness.
                if pair in seen or (previous is not None and key.encode() < previous.encode()):
                    raise ValueError("duplicate/nonadvancing multipart identity")
                entry = {
                    "key": key,
                    "upload_id": uid,
                    "initiated": _text(row, "Initiated"),
                    "parts": [],
                }
                self.result.uploads.append(entry)
                self._parts(key, uid, entry["parts"])
                seen.add(pair)
                previous = key
            if not _truncated(root):
                return
            next_marker = (_key(_text(root, "NextKeyMarker")), _text(root, "NextUploadIdMarker"))
            if not rows or next_marker == marker or next_marker != (key, uid):
                raise ValueError("invalid multipart dual continuation")
            marker = next_marker

    def _versioning(self):
        root = self._page({"versioning": ""}, "VersioningConfiguration")
        if len(root) or (root.text or "").strip():
            raise ValueError("capacity bucket must be observed never-versioned")

    def read(self):
        if self.result.pages:
            raise ValueError("storage inventory is single-use")
        try:
            self._versioning()
            self._objects()
            self._uploads()
            self._versioning()
            self.result.complete = True
        except Exception as error:  # noqa: BLE001 - retain failed acquisition and continue safe cleanup
            self.result.errors.append({"stage": "storage-read", "type": type(error).__name__})
        return self.result


class RetainedStorage(MinioInventory):
    """Offline transport for the exact same parsing/pagination authority.

    GET content digests are retained original observations, not reconstructed
    bytes or an independent cryptographic attestation of the object store.
    """

    def __init__(self, original, bucket, *, budget=None):
        self.original, self.bucket = original, bucket
        self.result = StorageInventory()
        self.pages = iter(original.pages)
        self.contents = iter(original.objects)
        self.retention = (
            budget.child(bytes_limit=PHASE_BYTES, rows_limit=1_000_000, row_limit=4 * 1024 * 1024)
            if budget is not None
            else EvidenceBudget(
                bytes_limit=PHASE_BYTES, rows_limit=1_000_000, row_limit=4 * 1024 * 1024
            )
        )

    def _retained_page(self, query, kind, key=None):
        page = next(self.pages)
        if page["request"] != query or page["kind"] != kind or page.get("key") != key:
            raise ValueError("original storage request/control differs")
        # Bound base64 input before allocating decoded bytes.
        encoded_size = len(page["raw_base64"])
        if encoded_size > (4 * 1024 * 1024 + 2) // 3 * 4:
            raise ValueError("original storage page exceeds bound")
        # ASCII input conversion and maximum decoded buffer must both fit
        # the existing shared unit and inherited per-record bound before decode.
        decoded_bound = 3 * ((encoded_size + 3) // 4)
        self.retention.reserve(
            encoded_size + decoded_bound + 256,
            rows=0,
            largest=max(encoded_size, decoded_bound),
        )
        raw = base64.b64decode(page["raw_base64"], validate=True)
        self.retention.charge_bytes(len(raw))
        if (
            page["ordinal"] != len(self.result.pages)
            or page["bytes"] != len(raw)
            or page["sha256"] != sha256(raw).hexdigest()
            or not 0 < page["start_ns"] <= page["end_ns"]
            or b"<!DOCTYPE" in raw
            or b"<!ENTITY" in raw
        ):
            raise ValueError("original storage page identity differs")
        self.retention.reserve(64 * len(raw), rows=0)
        root = ET.fromstring(raw)
        if root.tag != _NS + kind:
            raise ValueError("wrong original storage result")
        self.result.pages.append(page)
        return root

    def _page(self, query, kind):
        return self._retained_page(query, kind)

    def _parts_page(self, key, upload, marker):
        return self._retained_page(
            {"uploadId": upload, "part-number-marker": str(marker), "max-parts": "1000"},
            "ListPartsResult",
            key,
        )

    def _get(self, key, size):
        row = next(self.contents)
        if row["key"] != key or row["size"] != size:
            raise ValueError("original object content observation differs")
        digest = row["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("original object content digest missing")
        return digest


def replay_storage(original, bucket, *, budget=None):
    replay = RetainedStorage(original, bucket, budget=budget)
    result = replay.read()
    if (
        not original.complete
        or original.errors
        or not result.complete
        or result.errors
        or next(replay.pages, None) is not None
        or next(replay.contents, None) is not None
        or result.objects != original.objects
        or result.uploads != original.uploads
    ):
        raise ValueError("storage replay incomplete or changed")
    return result
