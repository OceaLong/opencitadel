"""Two reviewed native profiles, installed before configuration/admission freeze."""

from sqlalchemy import text

from app.application.evaluation.budget_service import BudgetAuthority
from app.domain.evaluation.budget_capabilities import BudgetInventory
from tests.app.execution_test_support import execution_admin_session


async def install_smaller_context_fallback(configurations):
    service = configurations[0]
    async with execution_admin_session() as db:
        await db.execute(
            text("""
INSERT INTO inference_endpoints(id,display_name,provider,base_url,credential,credential_encryption)
SELECT 'context-anthropic','Context fallback','anthropic','https://api.anthropic.com',credential,credential_encryption
FROM inference_endpoints WHERE id='e02-endpoint'
""")
        )
        await db.execute(
            text("""
UPDATE inference_models SET endpoint_id='context-anthropic',model_name='claude-haiku-4-5-20251001'
WHERE id='e05-fallback'
""")
        )
        await db.commit()
    inventory = service.budgets.inventory.model_dump(mode="json")
    inventory["revision"] = "context-two-native-windows-v1"
    inventory["endpoints"]["context-anthropic"] = {
        "provider": "anthropic",
        "origin": "https://api.anthropic.com",
        "pool": "context-anthropic-owned",
    }
    inventory["profiles"].append(
        {
            "endpoint_id": "context-anthropic",
            "model": "claude-haiku-4-5-20251001",
            "profile": "anthropic-haiku45-messages-v1",
        }
    )
    service.budgets = BudgetAuthority(BudgetInventory.model_validate(inventory))
