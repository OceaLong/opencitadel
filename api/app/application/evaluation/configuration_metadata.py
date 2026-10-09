"""Server-owned static metadata only. No tool instances, discovery or credentials."""

import hashlib
import inspect
import json
from copy import deepcopy
from typing import Literal, Protocol

from pydantic import Field

from app.domain.evaluation.configuration import ExternalContractReference, digest
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.session_mode import SessionMode
from app.domain.models.tool_policy import ToolExecutionPolicy
from app.domain.services.tools.artifact import ArtifactTool
from app.domain.services.tools.ask_user import AskUserTool
from app.domain.services.tools.browser import BrowserTool
from app.domain.services.tools.capability_policy import CapabilityPolicy
from app.domain.services.tools.file import FileTool
from app.domain.services.tools.image_generation import ImageGenerationTool
from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool
from app.domain.services.tools.memory import MemoryTool
from app.domain.services.tools.search import SearchTool
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.vision import VisionTool
from app.domain.services.tools.vision_grounding import VisionGroundingTool

BUILTINS = (
    ArtifactTool,
    AskUserTool,
    BrowserTool,
    FileTool,
    ImageGenerationTool,
    KnowledgeBaseTool,
    MemoryTool,
    SearchTool,
    ShellTool,
    VisionTool,
    VisionGroundingTool,
)


class ExternalToolContract(ImmutableModel):
    name: str = Field(min_length=1)
    pack: Literal["mcp", "a2a"]
    schema_body: dict
    policy: ToolExecutionPolicy
    connector_id: str = Field(min_length=1)
    binding_revision: str = Field(min_length=1)
    authority_revision: str = Field(min_length=1)


class ExternalContractAuthority(Protocol):
    async def contracts(
        self,
        scope: OwnerScope,
        principal: Principal,
        names: tuple[str, ...],
        *,
        reference: ExternalContractReference,
    ) -> tuple[ExternalToolContract, ...]:
        """Current scoped metadata authority only; fixed recording/environment reference.

        E03/E04 must verify exact connector identity/binding revision, full unchanged
        schemas/policies and current permission. This grants no result-body access.
        Discovery, target initialization and provider calls are forbidden here.
        """
        ...


class UnavailableExternalContracts:
    async def contracts(self, scope, principal, names, *, reference):
        if names:
            raise ValueError("contract_unavailable")
        return ()


def builtin_contracts(names, *, mode, allowed_tools):
    # Inspect the production declarative assembly table; never call a builder.
    from app.application.execution.agent_tool_catalog import _TOOL_ASSEMBLY

    specs = {entry.spec.name: entry.spec for entry in _TOOL_ASSEMBLY}
    policy = CapabilityPolicy(mode=SessionMode(mode), allowed_tool_names=allowed_tools)
    contracts = {}
    for cls in BUILTINS:
        for _, method in inspect.getmembers_static(cls, inspect.isfunction):
            name = getattr(method, "_tool_name", None)
            if not name or name not in names:
                continue
            pack = "vision" if cls.name == "vision_grounding" else cls.name
            if pack not in specs or SessionMode(mode) not in specs[pack].modes:
                raise ValueError("tool_policy_denied")
            execution = method._tool_policy
            if not policy.allows(execution, tool_name=name):
                raise ValueError("tool_policy_denied")
            contracts[name] = {
                "name": name,
                "pack": cls.name,
                "schema": deepcopy(method._tool_schema),
                "policy": execution.model_dump(mode="json"),
            }
    if set(names) - contracts.keys():
        raise ValueError("contract_unavailable")
    return tuple(contracts[name] for name in sorted(contracts))


def contract_fingerprints(contracts, *, mode, skill):
    # Exact legacy F07 algorithm, intentionally excludes schema. Never redefined.
    entries = sorted(
        ({k: c[k] for k in ("name", "pack", "policy")} for c in contracts),
        key=lambda entry: (entry["name"], entry["pack"]),
    )
    legacy = hashlib.sha256(
        json.dumps(
            {"mode": mode, "skill": skill, "tools": entries}, sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()
    return legacy, digest({"schema_version": 1, "contracts": list(contracts)})
