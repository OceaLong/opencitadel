"""Content-addressed input/result objects for durable Activities."""

import hashlib
import json
from uuid import UUID

from app.domain.execution.commands import JsonValue
from app.domain.external.object_storage import ObjectStoragePort


class ActivityObjectStore:
    def __init__(self, object_storage: ObjectStoragePort) -> None:
        self._storage = object_storage

    async def put_input(
        self,
        run_id: UUID,
        payload: dict[str, JsonValue],
    ) -> tuple[str, str]:
        encoded = self._encode(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        key = f"execution/inputs/{run_id}/{digest}.json"
        await self._storage.put_bytes(key, encoded)
        return key, digest

    async def load_input(
        self,
        *,
        key: str,
        expected_digest: str,
    ) -> dict[str, JsonValue]:
        encoded = await self._storage.get_bytes(key)
        actual = hashlib.sha256(encoded).hexdigest()
        if actual != expected_digest:
            raise ValueError("Activity input digest mismatch")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise TypeError("Activity input must be a JSON object")
        return payload

    async def put_result(
        self,
        activity_id: UUID,
        payload: dict[str, JsonValue],
    ) -> str:
        encoded = self._encode(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        key = f"execution/results/{activity_id}/{digest}.json"
        await self._storage.put_bytes(key, encoded)
        return key

    async def load_result(self, key: str) -> dict[str, JsonValue]:
        prefix = "execution/results/"
        if not key.startswith(prefix) or not key.endswith(".json"):
            raise ValueError("invalid Activity result reference")
        expected = key.rsplit("/", 1)[-1].removesuffix(".json")
        encoded = await self._storage.get_bytes(key)
        actual = hashlib.sha256(encoded).hexdigest()
        if actual != expected:
            raise ValueError("Activity result digest mismatch")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise TypeError("Activity result must be a JSON object")
        return payload

    @staticmethod
    def _encode(payload: dict[str, JsonValue]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


__all__ = ["ActivityObjectStore"]
