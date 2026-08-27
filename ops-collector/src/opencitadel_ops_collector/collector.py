"""Bounded probe implementations behind the fixed contract."""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import CollectorSettings
from .contracts import CollectorEnvelope, CollectorErrorCode, evidence_for
from .k8s_client import KubernetesReader
from .limits import bound
from .redaction import redact
from .security import (
    ProbeDenied,
    require_namespace,
    require_registered,
    require_workloads,
    validate_redirect,
)

_TRANSIENT = (httpx.TimeoutException, httpx.NetworkError, ConnectionError, TimeoutError)


class _UpstreamFailure(Exception):
    def __init__(self, code: CollectorErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _raise_for_upstream(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise _UpstreamFailure(CollectorErrorCode.RATE_LIMITED, "upstream rate limited")
    if response.status_code in {401, 403}:
        raise _UpstreamFailure(CollectorErrorCode.AUTH_FAILED, "upstream authentication failed")
    if response.status_code == 404:
        raise _UpstreamFailure(CollectorErrorCode.TARGET_NOT_FOUND, "registered target not found")
    if response.status_code >= 500:
        raise _UpstreamFailure(CollectorErrorCode.UPSTREAM_UNAVAILABLE, "upstream unavailable")
    response.raise_for_status()


class OpsCollector:
    def __init__(
        self, settings: CollectorSettings, *, k8s_factory: Callable[[], Any] = KubernetesReader
    ) -> None:
        self.settings = settings
        self._k8s_factory = k8s_factory
        self._semaphore = asyncio.Semaphore(settings.concurrency)

    async def _execute(
        self, operation: Callable[[], Any], evidence_types: list[str]
    ) -> dict[str, Any]:
        start = time.monotonic()
        try:
            async with self._semaphore:
                for attempt in range(2):
                    try:
                        value = operation()
                        if hasattr(value, "__await__"):
                            value = await value
                        break
                    except (*_TRANSIENT, _UpstreamFailure) as exc:
                        if isinstance(exc, _UpstreamFailure) and exc.code not in {
                            CollectorErrorCode.RATE_LIMITED,
                            CollectorErrorCode.UPSTREAM_UNAVAILABLE,
                        }:
                            raise
                        if attempt:
                            raise
                        await asyncio.sleep(0)
            safe = redact(value)
            data, warnings = bound(safe, self.settings)
            result = CollectorEnvelope(
                target_ref=self.settings.target_ref,
                duration_ms=int((time.monotonic() - start) * 1000),
                status="ok",
                data=data,
                evidence=evidence_for(self.settings.target_ref, data, evidence_types),
                warnings=warnings,
            )
        except ProbeDenied as exc:
            code = (
                CollectorErrorCode.NAMESPACE_DENIED
                if str(exc) == "NAMESPACE_DENIED"
                else CollectorErrorCode.PROBE_DENIED
            )
            result = CollectorEnvelope(
                target_ref=self.settings.target_ref,
                duration_ms=int((time.monotonic() - start) * 1000),
                status="denied",
                error_code=code,
                error_message=str(exc),
            )
        except ValueError as exc:
            code = (
                CollectorErrorCode.OUTPUT_TOO_LARGE
                if str(exc) == "OUTPUT_TOO_LARGE"
                else CollectorErrorCode.SCHEMA_MISMATCH
            )
            result = CollectorEnvelope(
                target_ref=self.settings.target_ref,
                duration_ms=int((time.monotonic() - start) * 1000),
                status="error",
                error_code=code,
                error_message=str(exc),
            )
        except _UpstreamFailure as exc:
            result = CollectorEnvelope(
                target_ref=self.settings.target_ref,
                duration_ms=int((time.monotonic() - start) * 1000),
                status="unavailable",
                error_code=exc.code,
                error_message=str(exc)[:2000],
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            result = CollectorEnvelope(
                target_ref=self.settings.target_ref,
                duration_ms=int((time.monotonic() - start) * 1000),
                status="unavailable",
                error_code=CollectorErrorCode.UPSTREAM_TIMEOUT,
                error_message=str(exc)[:2000],
            )
        except (httpx.NetworkError, ConnectionError) as exc:
            result = CollectorEnvelope(
                target_ref=self.settings.target_ref,
                duration_ms=int((time.monotonic() - start) * 1000),
                status="unavailable",
                error_code=CollectorErrorCode.UPSTREAM_UNAVAILABLE,
                error_message=str(exc)[:2000],
            )
        except (OSError, RuntimeError) as exc:
            result = CollectorEnvelope(
                target_ref=self.settings.target_ref,
                duration_ms=int((time.monotonic() - start) * 1000),
                status="error",
                error_code=CollectorErrorCode.INTERNAL_ERROR,
                error_message=redact(str(exc))[:2000],
            )
        return result.model_dump(mode="json")

    async def k8s_workload_summary(
        self,
        namespace: str,
        workload_ids: list[str],
        window_seconds: int,
        pvc_query_id: str | None = None,
    ) -> dict[str, Any]:
        async def call():
            require_workloads(self.settings, namespace, workload_ids)
            data = self._k8s_factory().workload_summary(namespace, workload_ids, window_seconds)
            if pvc_query_id:
                require_registered(self.settings.prometheus_queries, pvc_query_id)
                target = self.settings.prometheus_queries[pvc_query_id]
                async with httpx.AsyncClient(
                    timeout=target.timeout_seconds, follow_redirects=False
                ) as client:
                    response = await client.get(
                        target.base_url.rstrip("/") + "/api/v1/query",
                        params={"query": target.promql},
                    )
                    _raise_for_upstream(response)
                    values = response.json().get("data", {}).get("result", [])
                numeric: list[float] = []
                for item in values[: self.settings.max_rows]:
                    value = item.get("value")
                    try:
                        numeric.append(float(value[1]))
                    except (IndexError, TypeError, ValueError):
                        continue
                data["max_pvc_utilization_percent"] = max(numeric) if numeric else None
            return data

        evidence_types = ["summary", "resource_refs"]
        if pvc_query_id:
            evidence_types.append("metric_sample")
        return await self._execute(call, evidence_types)

    async def k8s_recent_events(
        self, namespace: str, since_seconds: int, limit: int
    ) -> dict[str, Any]:
        def call():
            require_namespace(self.settings, namespace)
            return self._k8s_factory().recent_events(namespace, since_seconds, limit)

        return await self._execute(call, ["summary", "resource_refs"])

    async def k8s_pod_logs(
        self, namespace: str, pod: str, container: str | None, tail_lines: int, since_seconds: int
    ) -> dict[str, Any]:
        def call():
            require_namespace(self.settings, namespace)
            return self._k8s_factory().pod_logs(
                namespace, pod, container, tail_lines, since_seconds
            )

        return await self._execute(call, ["log_excerpt"])

    async def prom_query(self, query_id: str, window_seconds: int) -> dict[str, Any]:
        async def call():
            require_registered(self.settings.prometheus_queries, query_id)
            target = self.settings.prometheus_queries[query_id]
            async with httpx.AsyncClient(
                timeout=target.timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.get(
                    target.base_url.rstrip("/") + "/api/v1/query", params={"query": target.promql}
                )
                _raise_for_upstream(response)
                payload = response.json()
                values = payload.get("data", {}).get("result", [])
                samples = [
                    {"metric": item.get("metric", {}), "value": item.get("value")}
                    for item in values[: self.settings.max_rows]
                ]
                numeric = None
                if (
                    samples
                    and isinstance(samples[0].get("value"), list)
                    and len(samples[0]["value"]) > 1
                ):
                    try:
                        numeric = float(samples[0]["value"][1])
                    except (TypeError, ValueError):
                        numeric = None
                return {
                    "query_id": query_id,
                    "value": numeric,
                    "samples": samples,
                    "window_seconds": window_seconds,
                }

        return await self._execute(call, ["metric_sample"])

    async def http_probe(self, probe_id: str) -> dict[str, Any]:
        async def call():
            require_registered(self.settings.http_probes, probe_id)
            target = self.settings.http_probes[probe_id]
            async with httpx.AsyncClient(
                timeout=target.timeout_seconds, follow_redirects=False
            ) as client:
                start = time.monotonic()
                response = await client.get(
                    target.url, headers={"User-Agent": "OpenCitadel-Ops-Collector/0.1"}
                )
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    validate_redirect(target.url, str(response.url.join(location)))
                    raise ProbeDenied("PROBE_DENIED")
                elapsed = int((time.monotonic() - start) * 1000)
                body = redact(response.text[:4096])
                return {
                    "probe_id": probe_id,
                    "status_code": response.status_code,
                    "latency_ms": elapsed,
                    "healthy": response.status_code in target.expected_statuses,
                    "body_fingerprint": __import__("hashlib").sha256(body.encode()).hexdigest(),
                }

        return await self._execute(call, ["summary"])

    async def certificate_status(self, probe_id: str) -> dict[str, Any]:
        def call():
            require_registered(self.settings.certificate_probes, probe_id)
            target = self.settings.certificate_probes[probe_id]
            parsed = urlparse(target.url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("registered certificate probe must use https")
            context = ssl.create_default_context()
            with (
                socket.create_connection(
                    (parsed.hostname, parsed.port or 443), timeout=target.timeout_seconds
                ) as raw,
                context.wrap_socket(raw, server_hostname=parsed.hostname) as tls,
            ):
                cert = tls.getpeercert()
            not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=UTC
            )
            return {
                "probe_id": probe_id,
                "subject": cert.get("subject"),
                "issuer": cert.get("issuer"),
                "not_after": not_after.isoformat(),
                "days_remaining": max(
                    0, int((not_after - datetime.now(UTC)).total_seconds() // 86400)
                ),
            }

        return await self._execute(call, ["tls_chain"])

    async def backup_status(self, backup_id: str) -> dict[str, Any]:
        async def call():
            require_registered(self.settings.backups, backup_id)
            target = self.settings.backups[backup_id]
            async with httpx.AsyncClient(
                timeout=target.timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.get(target.status_url)
                _raise_for_upstream(response)
                payload = response.json()
            last_success = datetime.fromisoformat(str(payload["last_success_at"]))
            age_hours = max(
                0.0, (datetime.now(UTC) - last_success.astimezone(UTC)).total_seconds() / 3600
            )
            return {
                "backup_id": backup_id,
                "last_success_at": last_success.isoformat(),
                "age_hours": age_hours,
                "state": payload.get("state", "unknown"),
            }

        return await self._execute(call, ["backup_metadata"])

    async def dependency_status(self, dependency_id: str) -> dict[str, Any]:
        def call():
            require_registered(self.settings.dependencies, dependency_id)
            configured = self.settings.dependencies[dependency_id]
            targets = configured if isinstance(configured, list) else [configured]
            rows = []
            for target in targets:
                start = time.monotonic()
                try:
                    with socket.create_connection(
                        (target.host, target.port), timeout=target.timeout_seconds
                    ):
                        pass
                    healthy = True
                    error = None
                except OSError as exc:
                    healthy = False
                    error = type(exc).__name__
                rows.append(
                    {
                        "kind": target.kind,
                        "healthy": healthy,
                        "latency_ms": int((time.monotonic() - start) * 1000),
                        "error": error,
                    }
                )
            return {
                "dependency_id": dependency_id,
                "healthy": all(item["healthy"] for item in rows),
                "dependencies": rows,
            }

        return await self._execute(call, ["summary"])
