"""Read a deployment-owned inventory. This is not an HTTP model capability hint."""

from pathlib import Path

from app.domain.evaluation.budget_capabilities import BudgetInventory


def load_budget_inventory(path: str, *, allow_acceptance=False) -> BudgetInventory | None:
    if not path:
        return None
    inventory = BudgetInventory.model_validate_json(Path(path).read_text(encoding="utf-8"))
    if inventory.acceptance_fixture and not allow_acceptance:
        raise ValueError("acceptance_profile_disabled")
    return inventory
