"""Authorized execution exports and spreadsheet-safe text encoding."""


def csv_cell(value: str) -> str:
    """Protect text only; a typed numeric column never passes through this guard."""
    probe = value.lstrip()
    return (
        "'" + value
        if probe.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r"))
        else value
    )


class ExecutionExportService:
    def __init__(self, repository, downloader, *, workspace_timezone=None):
        self.repository, self.downloader = repository, downloader
        self.workspace_timezone = workspace_timezone

    async def create(self, scope, principal, request):
        from app.application.ports.execution_comparison import (
            command_request_id,
            intent_fingerprint,
        )
        from app.domain.analysis.metrics import resolve_timezone

        command_request_id(request.get("request_id"))
        request = {**request, "request_fingerprint": intent_fingerprint(request)}
        if principal.is_auditor:
            raise PermissionError("export_read_only_principal")
        if request["source_kind"] == "filter":
            resolve_timezone(None, request["selection"].get("timezone", "UTC"))
            request = {
                **request,
                "selection": {
                    **request["selection"],
                    "timezone": resolve_timezone(
                        self.workspace_timezone, request["selection"].get("timezone", "UTC")
                    ),
                },
            }
        if request["source_kind"] == "batch":
            resolve_timezone(None, request.get("timezone", "UTC"))
            request = {
                **request,
                "timezone": resolve_timezone(
                    self.workspace_timezone, request.get("timezone", "UTC")
                ),
            }
        accepted = await self.repository.accept(scope, principal, request)
        return await self.repository.get(scope, principal, accepted["id"])

    async def get(self, scope, principal, export_id):
        return await self.repository.get(scope, principal, export_id)

    async def download(self, scope, principal, export_id):
        return await self.downloader.prepare(scope, principal, export_id)
