"""Authoritative readback of the real live cohort. No facts are inserted here."""

from uuid import UUID

from scripts.execution_capacity.persistence import PersistedFacts
from sqlalchemy import text


def validate_active(rows, runs):
    if len(rows) != 10 or {str(r["run_id"]) for r in rows} != set(runs):
        raise ValueError("ten actual physical model handlers are not active")
    if len({r["call_identity"] for r in rows}) != 10:
        raise ValueError("live dispatch identity is not unique")
    for row in rows:
        if (
            row["status"] != "call_started"
            or not row["lease_live"]
            or row["reservation_state"] != "dispatching"
            or row["settled"]
            or row["configured_model"] != "acceptance-live"
            or row["stream"] is not True
            or row.get("terminal", False) is not False
        ):
            raise ValueError("live handler is waiting, expired, settled or wrong provider profile")
    return {
        (
            str(r["run_id"]),
            str(r["activity_id"]),
            r["generation"],
            r["claim_generation"],
            r["call_identity"],
        )
        for r in rows
    }


class LiveFacts(PersistedFacts):
    async def active(self, runs):
        async with self.session() as session:
            rows = (
                (
                    await session.execute(
                        text("""
                WITH observed AS MATERIALIZED (SELECT clock_timestamp() AS at)
                SELECT d.run_id,d.activity_id,d.generation,d.claim_generation,d.call_identity,
                  a.status,a.claimed_by,a.call_started_at,a.heartbeat_at,a.claim_deadline,a.timeout_at,
                  observed.at AS sql_observed_at,r.call_identity::text AS reservation_id,
                  p.execution_policy_revision_id::text AS policy_id,
                  p.status IN ('completed','failed','cancelled') AS terminal,
                  (a.claim_deadline>observed.at AND a.timeout_at>observed.at) AS lease_live,
                  r.state AS reservation_state,s.call_identity IS NOT NULL AS settled,
                  c.body->>'configured_model' AS configured_model,
                  (d.request_snapshot->'request'->>'stream')::boolean AS stream
                FROM execution_model_dispatches d CROSS JOIN observed
                JOIN execution_run_projection p ON p.run_id=d.run_id
                JOIN execution_activity_tasks a ON a.activity_id=d.activity_id
                  AND a.request_generation=d.generation AND a.claim_generation=d.claim_generation
                JOIN execution_configurations c ON c.scope_key=d.scope_key AND c.id=d.configuration_id
                LEFT JOIN evaluation_budget_reservations r ON r.scope_key=d.scope_key AND r.call_identity::text=d.call_identity
                LEFT JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity
                WHERE d.scope_key=:scope AND d.run_id=ANY(CAST(:runs AS uuid[]))
                  AND a.activity_type='model.call' AND a.run_id=d.run_id::text
            """),
                        {"scope": self.scope_key, "runs": list(runs)},
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def progress(self, rows):
        if not rows:
            return []
        async with self.session() as session:
            records = (
                (
                    await session.execute(
                        text("""
                SELECT o.run_id,o.source_identity,o.observed_order,o.projection_revision,
                       o.public_payload,p.payload,p.event_id
                FROM execution_view_observations o
                LEFT JOIN execution_public_events p ON p.event_id::text=o.source_identity
                  AND p.run_id=o.run_id AND p.owner_user_id=o.owner_user_id AND p.team_id IS NOT DISTINCT FROM o.team_id
                WHERE o.scope_key=:scope AND o.source_kind='progress'
                  AND o.source_identity=ANY(CAST(:ids AS text[]))
            """),
                        {"scope": self.scope_key, "ids": [r["event_id"] for r in rows]},
                    )
                )
                .mappings()
                .all()
            )
        by_id = {r["source_identity"]: r for r in records}
        if len(by_id) != len(records):
            raise ValueError("ambiguous source progress observation")
        result = []
        for row in rows:
            fact = by_id.get(row["event_id"])
            source = {
                key: row[key]
                for key in ("activity_id", "generation", "claim_generation", "sequence")
            }
            effective = bool(
                fact
                and fact["public_payload"].get("source") == source
                and fact["public_payload"].get("applied") is True
            )
            public = bool(
                fact
                and fact["payload"]
                and fact["payload"].get("message") == row["message"]
                and str(fact["run_id"]) == row["run_id"]
                and str(fact["event_id"]) == row["event_id"]
            )
            result.append(
                {
                    **row,
                    "source_identity": fact["source_identity"] if fact else None,
                    "source": fact["public_payload"].get("source") if fact else None,
                    "applied": fact["public_payload"].get("applied") if fact else None,
                    "public_event_id": str(fact["event_id"]) if fact else None,
                    "public_run_id": str(fact["run_id"]) if fact else None,
                    "public_message": fact["payload"].get("message")
                    if fact and fact["payload"]
                    else None,
                    "effective": effective,
                    "public": public,
                    "observed_order": fact["observed_order"] if fact else None,
                    "projection_revision": fact["projection_revision"] if fact else None,
                }
            )
        return result

    async def batch(self, batch_id, *, counts=True):
        async with self.session() as session:
            batch = (
                (
                    await session.execute(
                        text(
                            "SELECT status,settings,suite_version FROM evaluation_batches WHERE scope_key=:scope AND id=:id"
                        ),
                        {"scope": self.scope_key, "id": UUID(str(batch_id))},
                    )
                )
                .mappings()
                .one()
            )
            if not counts:
                return dict(batch)
            counts = (
                (
                    await session.execute(
                        text("""
                SELECT count(*) AS sends,count(s.call_identity) AS settled,
                  coalesce(array_agg(d.call_identity ORDER BY d.call_identity)
                    FILTER (WHERE s.call_identity IS NULL AND r.state='dispatching'), ARRAY[]::text[]) AS active_call_ids
                FROM execution_model_dispatches d
                JOIN evaluation_budget_reservations r ON r.scope_key=d.scope_key AND r.call_identity::text=d.call_identity
                LEFT JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity
                WHERE d.scope_key=:scope AND r.demand->>'batch_id'=:batch
            """),
                        {"scope": self.scope_key, "batch": str(batch_id)},
                    )
                )
                .mappings()
                .one()
            )
        return {**dict(batch), **dict(counts)}

    async def disposition(self, runs):
        async with self.session() as session:
            rows = (
                (
                    await session.execute(
                        text("""
                SELECT d.run_id,d.call_identity,r.state,r.settlement,s.fact
                FROM execution_model_dispatches d
                LEFT JOIN evaluation_budget_reservations r ON r.scope_key=d.scope_key AND r.call_identity::text=d.call_identity
                LEFT JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity
                WHERE d.scope_key=:scope AND d.run_id=ANY(CAST(:runs AS uuid[]))
            """),
                        {"scope": self.scope_key, "runs": list(runs)},
                    )
                )
                .mappings()
                .all()
            )
        return [{**dict(row), "run_id": str(row["run_id"])} for row in rows]

    async def outcomes(self, runs):
        async with self.session() as session:
            rows = (
                (
                    await session.execute(
                        text("""
                SELECT run_id,status FROM execution_run_projection
                WHERE owner_user_id=:owner AND team_id IS NULL AND run_id=ANY(CAST(:runs AS uuid[]))
            """),
                        {"owner": self.scope.user_id, "runs": list(runs)},
                    )
                )
                .mappings()
                .all()
            )
        return {str(r["run_id"]): r["status"] for r in rows}
