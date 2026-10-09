"""Private F07 bridge for E06. Caller supplies its authorized fixed version and actual admission policy."""

from app.domain.evaluation.configuration import ConfigVersion
from app.domain.models.execution_usage import PriceSnapshot, Purpose
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import ActiveExecutionPolicy


def f07_configuration_evidence(
    scope: OwnerScope, version: ConfigVersion, actual: ActiveExecutionPolicy, *, purpose: Purpose
) -> dict:
    """Pure evidence builder; no Run, admission, repository or provider side effects.

    E06 must first use the current gate in its own transaction and pass the actual
    ActiveExecutionPolicy selected by admission, not a stored preflight revision.
    Scope is association evidence; this pure function cannot confer authorization.
    """
    if purpose != version.selection.purpose:
        raise ValueError("configuration_purpose_mismatch")
    snapshot = version.snapshot
    if snapshot["effective_policy"]["execution"] != actual.revision.policy.model_dump(mode="json"):
        raise ValueError("policy_changed")
    price = PriceSnapshot(
        input_per_million=snapshot["price"]["input"],
        output_per_million=snapshot["price"]["output"],
        provenance="legacy_positive",
    )
    return {
        **{
            key: snapshot["identity"][key]
            for key in ("model_id", "configured_model", "provider", "endpoint_id")
        },
        "credential_ref": dict(snapshot["credential_ref"]),
        "settings": dict(snapshot["settings"]),
        "thinking_enabled": False,
        "extra_parameters": {},
        "prompt": {
            "messages": [
                {
                    "role": "system",
                    "content": snapshot["prompt"].get("governance", "")
                    + (
                        "\n\n" + snapshot["prompt"]["skill"]
                        if snapshot["prompt"].get("skill")
                        else ""
                    ),
                },
                {"role": "user", "content": snapshot["prompt"].get("user_instructions", "")},
            ],
            "template_revision": snapshot["prompt"].get(
                "template_revision", "model-call-system-v1"
            ),
            "redacted": False,
        },
        "tools": {
            "definitions": [dict(contract["schema"]) for contract in snapshot["contracts"]],
            "fingerprint": snapshot["legacy_catalog_fingerprint"],
            "redacted": False,
        },
        "skill": snapshot.get("skill"),
        "knowledge_bindings": snapshot.get("resources", []),
        "policy_revision": str(actual.revision.id),
        "version_unpinned": version.version_unpinned,
        "price": price.model_dump(mode="json"),
        "price_revision": price.revision,
        "evaluation_configuration": {
            "id": str(version.id),
            "revision": version.revision,
            "fingerprint": version.fingerprint,
            "contract_digest": snapshot["contract_digest"],
            "scope": scope.model_dump(mode="json"),
            "purpose": purpose,
        },
    }
