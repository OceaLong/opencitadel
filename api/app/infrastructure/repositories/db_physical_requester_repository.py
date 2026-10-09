"""Private immutable requester provenance for ordinary physical model calls."""

import hashlib
import hmac
import json

from sqlalchemy import text

from app.domain.models.authorization import AuthorizationMode
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    DBEvaluationDatasetRepository,
)
from app.infrastructure.repositories.db_resource_pin_repository import scope_params


class DBPhysicalRequesterRepository:
    def __init__(self, session, *, signing_secret):
        if not signing_secret:
            raise ValueError("physical_requester_signing_secret_required")
        self.db, self.secret = session, signing_secret

    async def capture(self, scope, authorization, *, run_id):
        claims = (
            (
                await self.db.execute(
                    text("""SELECT
            public.opencitadel_authorization_valid() AS valid,
            current_setting('app.auth_mode',true) AS mode,
            current_setting('app.user_id',true) AS user_id,
            current_setting('app.team_id',true) AS team_id,
            current_setting('app.system_actor',true) AS actor,
            current_setting('app.is_admin',true) AS admin,
            current_setting('app.is_auditor',true) AS auditor""")
                )
            )
            .mappings()
            .one()
        )
        if not claims["valid"] or claims["mode"] != authorization.mode.value:
            raise ValueError("physical_requester_authority_invalid")
        common = {"version": 1, "scope": scope_params(scope)["scope"], "run_id": str(run_id)}
        if authorization.mode == AuthorizationMode.USER:
            principal = authorization.principal
            if (
                principal is None
                or claims["user_id"] != principal.user_id
                or claims["team_id"] != (scope.team_id or "")
                or scope.user_id != principal.user_id
                or claims["admin"] != str(principal.is_admin).lower()
                or claims["auditor"] != str(principal.is_auditor).lower()
            ):
                raise ValueError("physical_requester_authority_invalid")
            await DBEvaluationDatasetRepository(self.db).authorize(scope, principal, write=True)
            return self._seal(
                {**common, "kind": "user", "principal": principal.model_dump(mode="json")}
            )
        if (
            authorization.mode == AuthorizationMode.SYSTEM
            and claims["actor"] == authorization.system_actor
        ):
            return self._seal({**common, "kind": "system", "actor": authorization.system_actor})
        raise ValueError("physical_requester_authority_invalid")

    def _seal(self, proof):
        encoded = json.dumps(
            proof, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        signature = hmac.new(
            self.secret.encode(),
            ("opencitadel:e05:requester:v1:" + encoded).encode(),
            hashlib.sha256,
        ).hexdigest()
        return {"proof": proof, "signature": signature}

    def verify(self, sealed, *, scope, run_id):
        if not isinstance(sealed, dict) or set(sealed) != {"proof", "signature"}:
            raise ValueError("physical_requester_proof_invalid")
        proof = sealed["proof"]
        if (
            not isinstance(proof, dict)
            or proof.get("version") != 1
            or proof.get("scope") != scope_params(scope)["scope"]
            or proof.get("run_id") != str(run_id)
            or not isinstance(sealed["signature"], str)
            or not hmac.compare_digest(self._seal(proof)["signature"], sealed["signature"])
        ):
            raise ValueError("physical_requester_proof_invalid")
        return proof

    async def resolve(self, admitted, *, scope, run_id):
        if admitted is not None and "physical_requester" in admitted["body"]:
            return self.verify(admitted["body"]["physical_requester"], scope=scope, run_id=run_id)
        old = await self.db.scalar(
            text("""SELECT o.created_at <= h.requester_cutover_at
            FROM execution_stream_owners o CROSS JOIN evaluation_physical_policy_head h
            WHERE o.stream_type='run' AND o.stream_id=:run AND o.owner_scope_key=:scope AND h.singleton"""),
            {"run": str(run_id), "scope": scope_params(scope)["scope"]},
        )
        if old is not True:
            raise ValueError("physical_requester_proof_required")
        if scope.team_id:
            return {"kind": "legacy_unknown_requester", "scope": scope_params(scope)["scope"]}
        user = (
            (
                await self.db.execute(
                    text(
                        "SELECT id,token_version,global_role FROM users WHERE id=:user AND status='active'"
                    ),
                    {"user": scope.user_id},
                )
            )
            .mappings()
            .first()
        )
        if user is None:
            raise ValueError("physical_requester_revoked")
        return {
            "kind": "legacy_personal_owner",
            "principal": {
                "user_id": user["id"],
                "token_version": user["token_version"],
                "global_role": user["global_role"],
                "team_roles": {},
            },
            "scope": scope_params(scope)["scope"],
        }
