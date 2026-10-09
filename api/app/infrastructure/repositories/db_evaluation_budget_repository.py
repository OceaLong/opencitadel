"""Private signed budget operations inside the caller's transaction."""

import hashlib
import hmac
import json
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.evaluation.budget import BudgetDemand, BudgetSettlement


class DBEvaluationBudgetRepository:
    def __init__(self, db_session, *, signing_secret):
        if not signing_secret:
            raise ValueError("budget_signing_secret_required")
        self.db, self.secret = db_session, signing_secret

    async def _envelope(self, operation, identity, demand, *, settlement=None, expires=None):
        if not isinstance(demand, BudgetDemand):
            raise TypeError("trusted_budget_demand_required")
        demand = BudgetDemand.model_validate(demand.model_dump())
        if operation not in {"reserve", "settle", "unknown", "lock", "complete_direct"}:
            raise ValueError("budget_operation_invalid")
        if settlement is not None and not isinstance(settlement, BudgetSettlement):
            raise TypeError("trusted_budget_settlement_required")
        if settlement is not None:
            settlement = BudgetSettlement.model_validate(settlement.model_dump())
        base_signature = await self.db.scalar(
            text("SELECT current_setting('app.auth_signature',true)")
        )
        body = json.dumps(
            {
                "operation": operation,
                "identity": identity,
                "demand": demand.model_dump(mode="json", exclude_none=True),
                "authorization_signature": base_signature,
                "settlement": settlement.model_dump(mode="json", exclude_none=True)
                if settlement is not None
                else None,
                "expires": expires if expires is not None else time.time() + 30,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        domain = (
            "opencitadel:e05:direct-completion:v1:"
            if operation == "complete_direct"
            else "opencitadel:e05:v1:"
        )
        signature = hmac.new(
            self.secret.encode(), (domain + body).encode(), hashlib.sha256
        ).hexdigest()
        return body, signature

    async def apply(self, encoded, signature):
        try:
            return await self.db.scalar(
                text("SELECT public.opencitadel_e05_operation(:body,:signature)"),
                {"body": encoded, "signature": signature},
            )
        except DBAPIError as exc:
            # Never echo SQL parameters, private envelope, or signing material.
            reason = str(exc.orig).splitlines()[0].rsplit(": ", 1)[-1]
            if reason.startswith("budget_"):
                raise ValueError(reason) from None
            raise RuntimeError("budget_storage_unavailable") from None

    async def lock_capacity(self, identity, demand):
        await self.apply(*await self._envelope("lock", identity, demand))

    async def reserve(self, identity, demand):
        return await self.apply(*await self._envelope("reserve", identity, demand))

    async def settle(self, identity, demand, fact):
        return await self.apply(*await self._envelope("settle", identity, demand, settlement=fact))

    async def mark_unknown(self, identity, demand):
        return await self.apply(*await self._envelope("unknown", identity, demand))

    async def complete_direct(self, identity, demand, fact):
        if demand.direct_request is None or demand.direct_request.request_id != identity:
            raise ValueError("budget_direct_receipt_required")
        return await self.apply(
            *await self._envelope("complete_direct", identity, demand, settlement=fact)
        )
