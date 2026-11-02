"""Capture actual authorized catalog descriptors before their originating model dispatch."""

import hashlib

from app.application.execution.content_sanitization import sanitize_content
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import RecordedContract, canonical
from app.domain.execution.tool_contract import execution_contract
from app.domain.utils.integration_runtime_builder import (
    a2a_records_to_runtime,
    mcp_records_to_runtime,
)


class ContractCapture:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def capture_disabled(self, context):
        """Record that this model dispatch had no tool catalog available to it."""
        if context.activity_id is None:
            return
        fingerprint = hashlib.sha256(canonical({"tools": [], "available": False})).hexdigest()
        async with self.uow_factory() as uow:
            await uow.evaluation_recording.capture(
                context.run.owner_scope,
                context.run.run_id,
                context.activity_id,
                {"fingerprint": fingerprint, "contracts": []},
            )
            await uow.commit()

    async def capture(self, context, built, *, contracts=None):
        try:
            await self._capture(context, built, contracts=contracts)
        except ReplayMismatch as error:
            # Deterministic inability to record is not a production execution failure.
            # Operational storage errors deliberately propagate.
            async with self.uow_factory() as uow:
                await uow.evaluation_recording.capture(
                    context.run.owner_scope,
                    context.run.run_id,
                    context.activity_id,
                    {"unavailable": error.reason},
                )
                await uow.commit()

    async def _capture(self, context, built, *, contracts=None):
        if context.activity_id is None:
            return
        if contracts is None:
            contracts = await self.describe(context, built)
        async with self.uow_factory() as uow:
            await uow.evaluation_recording.capture(
                context.run.owner_scope,
                context.run.run_id,
                context.activity_id,
                {
                    "fingerprint": built.fingerprint,
                    "contracts": [c.model_dump(mode="json") for c in contracts],
                },
            )
            await uow.commit()

    async def describe(self, context, built, *, names=None):
        """Same authorized descriptors for production and optional recording."""
        scope = context.run.owner_scope
        contracts = []
        async with self.uow_factory() as uow:
            for pack in built.packs:
                if names is not None and not any(
                    d.name in names for d in pack.get_tool_descriptors()
                ):
                    continue
                bindings = {}
                runtime = getattr(pack, "recording_runtime", None)
                if pack.name in {"mcp", "a2a"}:
                    if runtime is None:
                        raise ReplayMismatch("capture_binding_unavailable")
                    servers = (
                        list(runtime.servers.values())
                        if pack.name == "mcp"
                        else list(runtime.servers)
                    )
                    expected_runtime = runtime
                    if names is not None and pack.name == "mcp":
                        sources = [
                            pack.recording_source(d.name)
                            for d in pack.get_tool_descriptors()
                            if d.name in names
                        ]
                        if any(source is None for source in sources):
                            raise ReplayMismatch("capture_source_unavailable")
                        selected_ids = {source[0] for source in sources}
                        servers = [server for server in servers if server.id in selected_ids]
                        expected_runtime = runtime.model_copy(
                            update={"servers": {server.id: server for server in servers}}
                        )
                    records = []
                    for server in sorted(servers, key=lambda s: s.id):
                        bindings[server.id] = await uow.evaluation_recording.connector_binding(
                            scope, pack.name, server.id, lock=True, execution=True
                        )
                        records.append(
                            await getattr(uow, pack.name + "_server").get_by_id(
                                server.id, scope=scope
                            )
                        )
                    if any(record is None for record in records):
                        raise ReplayMismatch("capture_binding_unavailable")
                    current = (
                        mcp_records_to_runtime(records)
                        if pack.name == "mcp"
                        else a2a_records_to_runtime(records)
                    )
                    if current != expected_runtime:
                        raise ReplayMismatch("capture_binding_changed")
                for descriptor in pack.get_tool_descriptors():
                    if names is not None and descriptor.name not in names:
                        continue
                    if sanitize_content(descriptor.schema) != descriptor.schema:
                        raise ReplayMismatch("capture_schema_redacted")
                    selected, connector, source_name = {}, None, None
                    if pack.name == "mcp":
                        source = pack.recording_source(descriptor.name)
                        if source is None or source[0] not in bindings:
                            raise ReplayMismatch("capture_source_unavailable")
                        connector, source_name = source
                        selected = {
                            connector: await uow.evaluation_recording.connector_binding(
                                scope,
                                pack.name,
                                connector,
                                lock=True,
                                execution=True,
                                tool_name=source_name,
                            )
                        }
                    elif pack.name == "a2a":
                        selected = {
                            identity: await uow.evaluation_recording.connector_binding(
                                scope,
                                pack.name,
                                identity,
                                lock=True,
                                execution=True,
                                tool_name=descriptor.name,
                            )
                            for identity in bindings
                        }
                        connector = (
                            "a2a-set:" + hashlib.sha256(canonical(sorted(bindings))).hexdigest()
                        )
                        source_name = descriptor.name
                    binding_material = selected or {
                        "schema": descriptor.schema,
                        "policy": descriptor.policy.model_dump(mode="json"),
                    }
                    if pack.name == "a2a":
                        # Discovery URL and actual invocation URL may differ.
                        # Freeze the initialized card's route/transport as part
                        # of this same contract, without persisting raw endpoints.
                        cards = getattr(getattr(pack, "manager", None), "agent_cards", {})
                        binding_material = {
                            "connectors": selected,
                            "routes": {
                                identity: {
                                    "url": cards.get(identity, {}).get("url"),
                                    "enabled": cards.get(identity, {}).get("enabled", True),
                                    "streaming": cards.get(identity, {})
                                    .get("capabilities", {})
                                    .get("streaming", False),
                                }
                                for identity in selected
                            },
                        }
                    revision = hashlib.sha256(canonical(binding_material)).hexdigest()
                    contracts.append(
                        execution_contract(
                            RecordedContract(
                                name=descriptor.name,
                                pack=descriptor.tool_pack,
                                schema_body=descriptor.schema,
                                policy=descriptor.policy,
                                connector_id=connector,
                                source_name=source_name,
                                connector_bindings=selected,
                                binding_revision=revision,
                                authority_revision=revision,
                            )
                        )
                    )
        return tuple(contracts)
