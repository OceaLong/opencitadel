"""Pure signed-SDK boundary transcripts; never runtime storage proof."""

import hashlib
import io
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
from scripts.execution_capacity import storage_inventory as source

NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def page(kind, body):
    return f"<{kind} {NS}>{body}</{kind}>".encode()


class Response(io.BytesIO):
    def release_conn(self):
        self.released = True


class SDK:
    def __init__(self, pages, contents=None, *, versioning=None):
        empty = page("VersioningConfiguration", "")
        self.pages = iter([empty if versioning is None else versioning, *pages, empty])
        self.contents = contents or {}
        self.responses = []
        self.queries = []

    def _execute(
        self, method, bucket_name, *, query_params, object_name=None, preload_content=True
    ):
        assert preload_content is False
        assert method == "GET"
        assert bucket_name == "owned"
        self.queries.append(query_params)
        value = next(self.pages)
        if isinstance(value, Exception):
            raise value
        response = Response(value)
        self.responses.append(response)
        return response

    def get_object(self, bucket, key):
        assert bucket == "owned"
        response = Response(self.contents[key])
        self.responses.append(response)
        return response


def objects(keys=(), *, truncated=False, token=None):
    body = (
        "<Name>owned</Name><EncodingType>url</EncodingType><KeyCount>"
        + str(len(keys))
        + "</KeyCount>"
    )
    body += "<IsTruncated>" + str(truncated).lower() + "</IsTruncated>"
    for key, size in keys:
        body += f'<Contents><Key>{escape(key)}</Key><Size>{size}</Size><ETag>"not-sha256"</ETag></Contents>'
    if token:
        body += f"<NextContinuationToken>{escape(token)}</NextContinuationToken>"
    return page("ListBucketResult", body)


def uploads(rows=(), *, marker=("", ""), next_marker=None, truncated=False):
    body = "<Bucket>owned</Bucket><EncodingType>url</EncodingType>"
    body += f"<KeyMarker>{escape(marker[0])}</KeyMarker><UploadIdMarker>{escape(marker[1])}</UploadIdMarker>"
    body += "<IsTruncated>" + str(truncated).lower() + "</IsTruncated>"
    for key, uid in rows:
        body += f"<Upload><Key>{escape(key)}</Key><UploadId>{escape(uid)}</UploadId><Initiated>2026-09-20T00:00:00Z</Initiated></Upload>"
    if next_marker:
        body += f"<NextKeyMarker>{escape(next_marker[0])}</NextKeyMarker><NextUploadIdMarker>{escape(next_marker[1])}</NextUploadIdMarker>"
    return page("ListMultipartUploadsResult", body)


def test_complete_bucket_get_digest_and_literal_plus():
    sdk = SDK(
        [
            objects([("a+b%2520", 3)], truncated=True, token="opaque+1"),
            objects([("z", 0)]),
            uploads(),
        ],
        {"a+b%20": b"abc", "z": b""},
    )
    result = source.MinioInventory(sdk, "owned").read()
    assert result.complete
    assert not result.errors
    assert [(r["key"], r["size"], r["sha256"]) for r in result.objects] == [
        ("a+b%20", 3, hashlib.sha256(b"abc").hexdigest()),
        ("z", 0, hashlib.sha256(b"").hexdigest()),
    ]
    assert sdk.queries[2]["continuation-token"] == "opaque+1"
    assert all(r.closed and r.released for r in sdk.responses)
    assert len(result.pages) == 5


@pytest.mark.parametrize(
    "bad",
    [
        objects(truncated=True),
        objects([("a", 3)], truncated=True, token="again"),
        b"<broken",
        objects([("a", 4)]),
        objects([("a", 3)]).replace(b"<Name>owned", b"<Name>foreign"),
    ],
)
def test_failed_inventory_retains_prefix_and_cannot_be_clean(bad):
    sdk = SDK([objects([("a", 3)], truncated=True, token="again"), bad, uploads()], {"a": b"abc"})
    result = source.MinioInventory(sdk, "owned").read()
    assert not result.complete
    assert result.errors
    assert result.objects[0]["key"] == "a"
    assert all(r.closed and r.released for r in sdk.responses)


def test_multipart_dual_marker_and_parts_remain_pending():
    parts = page(
        "ListPartsResult",
        "<Bucket>owned</Bucket><Key>a+b</Key><UploadId>id+1</UploadId><PartNumberMarker>0</PartNumberMarker><IsTruncated>false</IsTruncated><Part><PartNumber>1</PartNumber><ETag>abc</ETag><Size>3</Size></Part>",
    )
    sdk = SDK(
        [
            objects(),
            uploads([("a+b", "id+1")], truncated=True, next_marker=("a+b", "id+1")),
            parts,
            uploads(marker=("a+b", "id+1")),
        ]
    )
    result = source.MinioInventory(sdk, "owned").read()
    assert result.complete
    assert len(result.uploads) == 1
    assert result.uploads[0]["parts"][0]["size"] == 3
    assert sdk.queries[-2]["key-marker"] == "a+b"
    assert sdk.queries[-2]["upload-id-marker"] == "id+1"
    with pytest.raises(ValueError, match="multipart"):
        result.require_quiescent({})


@pytest.mark.parametrize(
    "mutation", ["missing_dual", "wrong_echo", "empty_truncated", "common_prefix"]
)
def test_bad_multipart_pagination_is_not_empty_success(mutation):
    raw = uploads(truncated=True, next_marker=("a", "id"))
    if mutation == "missing_dual":
        raw = raw.replace(b"<NextUploadIdMarker>id</NextUploadIdMarker>", b"")
    elif mutation == "wrong_echo":
        raw = raw.replace(b"<KeyMarker></KeyMarker>", b"<KeyMarker>foreign</KeyMarker>")
    elif mutation == "common_prefix":
        raw = raw.replace(
            b"</ListMultipartUploadsResult>",
            b"<CommonPrefixes><Prefix>a</Prefix></CommonPrefixes></ListMultipartUploadsResult>",
        )
    result = source.MinioInventory(SDK([objects(), raw]), "owned").read()
    assert not result.complete
    assert result.errors


def test_unknown_objects_and_wrong_content_fail_exact_reconciliation():
    result = source.MinioInventory(
        SDK([objects([("extra", 3)]), uploads()], {"extra": b"abc"}), "owned"
    ).read()
    with pytest.raises(ValueError, match="object"):
        result.require_quiescent({})
    with pytest.raises(ValueError, match="object"):
        result.require_quiescent({"extra": {"size": 3, "sha256": "0" * 64}})


def test_sdk_binding_rejects_unknown_implementation(monkeypatch):
    monkeypatch.setattr(source, "version", lambda name: "7.2.21")
    with pytest.raises(ValueError, match="SDK"):
        source.MinioInventory(SimpleNamespace(), "owned")


@pytest.mark.parametrize(
    "body",
    [
        "<Status>Enabled</Status>",
        "<Status>Suspended</Status>",
        "<Status>unknown</Status>",
        "<Status/>",
        "<MfaDelete>Disabled</MfaDelete>",
        "<Status>Enabled</Status><Status>Suspended</Status>",
    ],
)
def test_versioned_or_ambiguous_bucket_cannot_hide_noncurrent_objects(body):
    result = source.MinioInventory(
        SDK([objects(), uploads()], versioning=page("VersioningConfiguration", body)), "owned"
    ).read()
    assert not result.complete
    assert result.errors
    assert result.pages[0]["request"] == {"versioning": ""}


@pytest.mark.parametrize("size", [0, 1, 17, 1024 * 1024 + 3])
@pytest.mark.parametrize("excess", [False, True])
def test_actual_content_get_bounds_each_request_and_preserves_digest(size, excess):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    raw = b"x" * (size + int(excess))
    requests = []

    class BoundedResponse(Response):
        def read(self, count):
            requests.append(count)
            assert 0 < count <= min(1024 * 1024, size - self.tell() + 1)
            assert inventory.unit_budget.bytes >= max(1, min(size + 1, 1024 * 1024))
            return super().read(count)

    response = BoundedResponse(raw)
    inventory = source.MinioInventory(SDK([]), "owned")
    inventory.unit_budget = EvidenceBudget(bytes_limit=max(10, 4 * (size + 1)))
    inventory.client.get_object = lambda *args: response
    if excess:
        with pytest.raises(ValueError, match="changed"):
            inventory._get("key", size)
    else:
        assert inventory._get("key", size) == hashlib.sha256(raw).hexdigest()
    assert requests
    assert response.closed
    assert response.released


def test_actual_content_get_exhausted_unit_refuses_before_provider_acquisition():
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError

    inventory = source.MinioInventory(SDK([]), "owned")
    inventory.unit_budget = EvidenceBudget(bytes_limit=100)
    inventory.unit_budget.reserve(99, rows=0)
    inventory.client.get_object = lambda *args: pytest.fail("unreserved provider acquisition")
    with pytest.raises(EvidenceQuotaError):
        inventory._get("tiny", 1)


@pytest.mark.parametrize("fault", ["overreturn", "read", "close"])
def test_actual_content_get_errors_release_response(fault):
    class BadResponse(Response):
        def read(self, count):
            if fault == "overreturn":
                return b"x" * (count + 1)
            if fault == "read":
                raise OSError("fixture read")
            return super().read(count)

        def close(self):
            super().close()
            if fault == "close":
                raise OSError("fixture close")

    response = BadResponse(b"x")
    inventory = source.MinioInventory(SDK([]), "owned")
    inventory.client.get_object = lambda *args: response
    with pytest.raises((ValueError, OSError)):
        inventory._get("key", 1)
    assert response.closed
    assert response.released


def test_offline_storage_replay_spends_the_caller_unit_before_xml_decode(monkeypatch):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    original = source.MinioInventory(SDK([objects(), uploads()]), "owned").read()
    budget = EvidenceBudget(bytes_limit=1024)
    budget.reserve(1023, rows=0)
    monkeypatch.setattr(source.ET, "fromstring", lambda raw: pytest.fail("unreserved XML decode"))
    with pytest.raises(ValueError, match="storage replay incomplete"):
        source.replay_storage(original, "owned", budget=budget)


def test_offline_storage_replay_charges_success_to_caller_unit():
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    original = source.MinioInventory(SDK([objects(), uploads()]), "owned").read()
    budget = EvidenceBudget()
    replayed = source.replay_storage(original, "owned", budget=budget)
    assert replayed.complete
    assert budget.bytes > sum(row["bytes"] for row in original.pages)


@pytest.mark.parametrize("limit", ["parent", "record"])
def test_retained_page_rejects_before_base64_allocation(monkeypatch, limit):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError

    original = SimpleNamespace(
        pages=[{"request": {}, "kind": "fixture", "raw_base64": "YWJj"}], objects=[]
    )
    budget = EvidenceBudget(bytes_limit=1024, row_limit=1 if limit == "record" else 1024)
    if limit == "parent":
        budget.reserve(1023, rows=0)
    replay = source.RetainedStorage(original, "owned", budget=budget)
    monkeypatch.setattr(
        source.base64,
        "b64decode",
        lambda *args, **kwargs: pytest.fail("unreserved base64 allocation"),
    )
    with pytest.raises(EvidenceQuotaError):
        replay._retained_page({}, "fixture")
    assert not replay.result.complete
    assert not replay.result.pages


def test_retained_page_prepays_decode_working_bytes_and_preserves_replay(monkeypatch):
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    original = source.MinioInventory(SDK([objects(), uploads()]), "owned").read()
    budget = EvidenceBudget()
    decoder = source.base64.b64decode
    entered = []

    def prepaid(value, *, validate):
        minimum = len(value) + 3 * ((len(value) + 3) // 4)
        assert budget.bytes >= sum(entered) + minimum
        entered.append(minimum)
        return decoder(value, validate=validate)

    monkeypatch.setattr(source.base64, "b64decode", prepaid)
    replayed = source.replay_storage(original, "owned", budget=budget)
    assert replayed.complete
    assert replayed == original
    assert len(entered) == len(original.pages)
