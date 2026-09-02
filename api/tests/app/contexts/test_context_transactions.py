"""Context boundaries replace the global repository inventory transaction."""

from __future__ import annotations

from inspect import getmembers, isfunction

from app.contexts.identity.transactions import IdentityTransaction
from app.contexts.inference.transactions import InferenceTransaction
from app.contexts.knowledge.transactions import KnowledgeTransaction


def _public_methods(protocol: type) -> set[str]:
    return {name for name, value in getmembers(protocol, isfunction) if not name.startswith("_")}


def test_each_context_transaction_has_a_closed_narrow_surface() -> None:
    assert _public_methods(IdentityTransaction) == {
        "get_principal",
        "get_team_role",
        "get_quota",
        "set_quota",
    }
    assert _public_methods(InferenceTransaction) == {
        "resolve_model",
        "record_usage",
    }
    assert _public_methods(KnowledgeTransaction) == {
        "get_published_version",
        "publish_candidate",
    }


def test_context_transactions_do_not_expose_foreign_repositories() -> None:
    joined = " ".join(
        sorted(
            _public_methods(IdentityTransaction)
            | _public_methods(InferenceTransaction)
            | _public_methods(KnowledgeTransaction)
        )
    )
    assert "event" not in joined
    assert "session" not in joined
    assert "patrol" not in joined
    assert "scheduled" not in joined
