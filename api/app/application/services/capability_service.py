from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.runtime_policy_reader import PolicyHeadReader
from app.domain.errors import BadRequestError, ConflictError
from app.domain.models.inference import InferencePurpose
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import (
    PatrolAdmissionMode,
    PatrolRemediationMode,
    RuntimePolicyUnavailableError,
)
from app.domain.utils.time_utils import utc_now


@cache
def _default_pdf_probe() -> bool:
    """Report whether the PDF report renderer is actually usable.

    ``weasyprint`` may be pip-installed yet still fail to import when its
    native GObject/Cairo/Pango libraries are missing on the host -- and that
    import raises ``OSError`` rather than ``ImportError``. Probing with
    ``importlib.util.find_spec`` would therefore report a false positive: it
    only checks that the Python package is present, not that it can load. So
    we attempt the same import the renderer performs (see
    ``infrastructure/external/report/pdf_renderer.py``) and treat *any*
    failure as "renderer unavailable". Cached so the heavy native probe runs
    at most once per process.
    """
    try:
        from weasyprint import HTML  # noqa: F401
    except Exception:  # noqa: BLE001 -- ImportError or native-lib OSError both mean unavailable
        return False
    return True


class CapabilityStateValue(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    DENIED = "denied"


class CapabilityState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: CapabilityStateValue
    reason_key: str | None = None
    model_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: datetime = Field(default_factory=utc_now)
    items: dict[str, CapabilityState]


class CapabilityService:
    def __init__(
        self,
        bindings: InferenceBindingService,
        *,
        policy_heads: PolicyHeadReader,
        pdf_probe: Callable[[], bool] | None = None,
        search_provider: str = "bing_html",
    ) -> None:
        self._bindings = bindings
        self._policy_heads = policy_heads
        self._pdf_probe = pdf_probe or _default_pdf_probe
        self._search_provider = (search_provider or "none").strip().lower()

    async def get_capabilities(
        self,
        scope: OwnerScope | None,
    ) -> CapabilitySnapshot:
        names = (
            "chat",
            "embeddings",
            "rerank",
            "a2a",
            "ops_patrol",
            "ops_patrol_remediation",
            "report_pdf",
            "web_search",
        )
        if scope is None:
            denied = CapabilityState(
                state=CapabilityStateValue.DENIED,
                reason_key="capabilities.reason.ownerScopeRequired",
            )
            return CapabilitySnapshot(items=dict.fromkeys(names, denied))

        now = utc_now()
        execution_active = await self._policy_heads.active_execution(
            require_fresh=False,
            now=now,
        )
        operations_active = await self._policy_heads.active_operations(
            require_fresh=False,
            now=now,
        )
        if execution_active.head != operations_active.head:
            raise RuntimePolicyUnavailableError(
                "Runtime Policy head changed while projecting capabilities"
            )
        execution = execution_active.revision.policy
        operations = operations_active.revision.policy
        chat = await self._inference_state(InferencePurpose.CHAT, scope)
        embedding = await self._inference_state(InferencePurpose.EMBEDDING, scope)
        if not (execution.memory.vector_enabled or execution.knowledge_base.vector_enabled):
            embedding = CapabilityState(
                state=CapabilityStateValue.DISABLED,
                reason_key="capabilities.reason.allVectorConsumersDisabled",
            )
        rerank = (
            await self._inference_state(InferencePurpose.RERANK, scope)
            if execution.knowledge_base.rerank.enabled
            else CapabilityState(
                state=CapabilityStateValue.DISABLED,
                reason_key="capabilities.reason.rerankDisabled",
            )
        )
        a2a = CapabilityState(
            state=chat.state,
            reason_key=chat.reason_key,
            model_id=chat.model_id,
            details={"route": "stable"},
        )
        patrol = (
            CapabilityState(
                state=CapabilityStateValue.AVAILABLE,
                details={"admission": PatrolAdmissionMode.ACCEPTING.value},
            )
            if operations.patrol.admission is PatrolAdmissionMode.ACCEPTING
            else CapabilityState(
                state=CapabilityStateValue.DISABLED,
                reason_key="capabilities.reason.newRunAdmissionPaused",
                details={"admission": PatrolAdmissionMode.PAUSED.value},
            )
        )
        remediation_mode = operations.patrol.remediation
        remediation = {
            PatrolRemediationMode.DISABLED: CapabilityState(
                state=CapabilityStateValue.DISABLED,
                reason_key="capabilities.reason.remediationDisabled",
                details={"mode": remediation_mode.value},
            ),
            PatrolRemediationMode.PROPOSE_ONLY: CapabilityState(
                state=CapabilityStateValue.DEGRADED,
                reason_key="capabilities.reason.remediationExecutionDisabled",
                details={"mode": remediation_mode.value},
            ),
            PatrolRemediationMode.ENABLED: CapabilityState(
                state=CapabilityStateValue.AVAILABLE,
                details={"mode": remediation_mode.value},
            ),
        }[remediation_mode]
        report_pdf = (
            CapabilityState(
                state=CapabilityStateValue.AVAILABLE,
                details={"engine": "weasyprint"},
            )
            if self._pdf_probe()
            else CapabilityState(
                state=CapabilityStateValue.NOT_CONFIGURED,
                reason_key="capabilities.reason.pdfRendererUnavailable",
                details={"engine": "weasyprint"},
            )
        )
        if self._search_provider == "none":
            web_search = CapabilityState(
                state=CapabilityStateValue.NOT_CONFIGURED,
                reason_key="capabilities.reason.searchProviderNotConfigured",
            )
        elif self._search_provider == "bing_html":
            # HTML scraping works until the target changes markup or challenges
            # the bot; report it as degraded so operators know to configure an
            # API-based provider.
            web_search = CapabilityState(
                state=CapabilityStateValue.DEGRADED,
                reason_key="capabilities.reason.searchHtmlScrapingFragile",
                details={"provider": self._search_provider},
            )
        else:
            web_search = CapabilityState(
                state=CapabilityStateValue.AVAILABLE,
                details={"provider": self._search_provider},
            )
        return CapabilitySnapshot(
            items={
                "chat": chat,
                "embeddings": embedding,
                "rerank": rerank,
                "a2a": a2a,
                "ops_patrol": patrol,
                "ops_patrol_remediation": remediation,
                "report_pdf": report_pdf,
                "web_search": web_search,
            }
        )

    async def _inference_state(
        self,
        purpose: InferencePurpose,
        scope: OwnerScope,
    ) -> CapabilityState:
        try:
            resolved = await self._bindings.resolve(purpose, scope=scope)
        except ConflictError as exc:
            return CapabilityState(
                state=CapabilityStateValue.NOT_CONFIGURED,
                reason_key=(exc.error_key or "capabilities.reason.bindingNotConfigured"),
            )
        except BadRequestError as exc:
            return CapabilityState(
                state=CapabilityStateValue.DEGRADED,
                reason_key=exc.error_key or "capabilities.reason.inferenceUnavailable",
            )
        return CapabilityState(
            state=CapabilityStateValue.AVAILABLE,
            model_id=resolved.id,
        )
