"""Published capacity corpus through ordinary dataset/suite/batch services.

All identities returned by services are actual durable records. The private
journal preserves original operation inputs across response loss. It never
supplies execution outcomes or substitutes for authoritative result readback.
"""

import io
import json
from collections import Counter
from hashlib import sha256
from uuid import UUID, uuid5

from app.domain.evaluation.configuration import ConfigSelection, DeploymentLimits, SuiteSettings
from app.domain.evaluation.rubric import RubricDefinition


def dataset_bytes():
    cases = []
    for index in range(1000):
        message = "[acceptance:evaluation:rule-pass]"
        cases.append(
            {
                "case_key": f"capacity-{index:04d}",
                "input": message,
                "reference_answer": "Acceptance response: " + message,
                "reference_confirmed": True,
                "rules": [
                    {
                        "id": "exact",
                        "kind": "text_exact",
                        "required": True,
                        "reference_required": True,
                    }
                ],
                "applicable_dimensions": ["correctness"],
            }
        )
    return json.dumps({"schema_version": 1, "cases": cases}, separators=(",", ":")).encode()


class Operations:
    def __init__(self, journal, cohort_id, scope_key, principal_id):
        self.journal, self.cohort_id = journal, UUID(str(cohort_id))
        self.scope_key, self.principal_id = scope_key, principal_id

    def identity(self, name):
        return str(uuid5(self.cohort_id, "evaluation:" + name))

    async def call(self, name, payload, effect):
        request_id = self.identity(name)
        self.journal.intent(
            "evaluation_operation",
            request_id,
            {
                "scope": self.scope_key,
                "principal_id": self.principal_id,
                "operation": name,
                "payload": payload,
            },
        )
        result = await effect(request_id)
        # Mutable batch revisions/status are re-read from the service. Only the
        # immutable returned identity is acknowledged in this recovery receipt.
        identity = getattr(result, "id", getattr(result, "import_id", None))
        if identity is not None:
            self.journal.acknowledge("evaluation_operation", request_id, {"id": str(identity)})
        return result


def validate_definition(binding, settings):
    subjects = tuple(ConfigSelection.model_validate(value) for value in binding["subjects"])
    judge = ConfigSelection.model_validate(binding["judge"])
    if len(subjects) != 5 or len({s.model_dump_json() for s in subjects}) != 5:
        raise ValueError("five distinct subject selections required")
    if any(
        s.purpose != "evaluation_subject"
        or s.mode != "ask"
        or s.tool_names
        or s.resources
        or s.skill_id
        for s in subjects
    ):
        raise ValueError("capacity rule-pass subjects require ordinary tool-free Ask")
    if judge.purpose != "evaluation_judge":
        raise ValueError("independent judge required")
    environment = UUID(binding["environment_version"])
    if any(
        s.external_contract_ref
        and (
            s.external_contract_ref.kind != "environment"
            or s.external_contract_ref.version_id != environment
        )
        for s in subjects
    ):
        raise ValueError("subject environment binding differs")
    limits = DeploymentLimits(
        **{key: getattr(settings, "evaluation_" + key) for key in DeploymentLimits.model_fields}
    )
    suite_settings = SuiteSettings(
        **{
            **limits.model_dump(),
            **binding["budgets"],
            "repeat": 1,
            "max_results": 5000,
            "seed": binding["seed"],
        }
    )
    suite_settings.validate_limits(limits)
    if any(
        getattr(suite_settings, key) != getattr(limits, key)
        for key in DeploymentLimits.model_fields
    ):
        raise ValueError("capacity requires configured default limits")
    return subjects, judge, environment, suite_settings


async def publish_corpus(
    datasets, suites, environments, scope, principal, operations, binding, settings
):
    subjects, judge, environment_id, suite_settings = validate_definition(binding, settings)
    # Current authorized read of the explicit prerequisite; later real preflight
    # also verifies its registered broker/profile/approval and current authority.
    environment = await environments.version(scope, principal, environment_id)
    from app.domain.evaluation.environment import EnvironmentVersion

    environment = EnvironmentVersion.model_validate(environment)
    if environment.model_dump(mode="json") != binding["environment"]:
        raise ValueError("published environment prerequisite differs")
    raw = dataset_bytes()
    draft = await operations.call(
        "dataset.create",
        {"name": "Capacity 1000 cases"},
        lambda request: datasets.create_draft(
            scope, principal, request_id=request, expected_revision=0, name="Capacity 1000 cases"
        ),
    )
    preview = await operations.call(
        "dataset.validate",
        {
            "dataset_id": str(draft.id),
            "revision": draft.revision,
            "input_sha256": sha256(raw).hexdigest(),
        },
        lambda request: datasets.import_validate(
            scope,
            principal,
            dataset_id=draft.id,
            request_id=request,
            expected_revision=draft.revision,
            stream=io.BytesIO(raw),
            content_type="application/json",
        ),
    )
    if preview.errors or len(preview.added) != 1000 or preview.removed or preview.replaced:
        raise ValueError("capacity import preview differs")
    applied = await operations.call(
        "dataset.apply",
        {
            "dataset_id": str(draft.id),
            "import_id": str(preview.import_id),
            "input_digest": preview.input_digest,
            "revision": draft.revision,
        },
        lambda request: datasets.import_apply(
            scope,
            principal,
            dataset_id=draft.id,
            import_id=preview.import_id,
            input_digest=preview.input_digest,
            request_id=request,
            expected_revision=draft.revision,
        ),
    )
    dataset = await operations.call(
        "dataset.publish",
        {"dataset_id": str(draft.id), "revision": applied.revision},
        lambda request: datasets.publish(
            scope,
            principal,
            dataset_id=draft.id,
            request_id=request,
            expected_revision=applied.revision,
        ),
    )
    dataset = await datasets.get_version(scope, principal, dataset.id)
    if len(dataset.cases) != 1000 or len({case.id for case in dataset.cases}) != 1000:
        raise ValueError("published dataset membership differs")
    expected = json.loads(raw)["cases"]
    actual = {case.case_key: case for case in dataset.cases}
    for case in expected:
        retained = actual.get(case["case_key"])
        if retained is None or any(
            getattr(retained, key) != value
            for key, value in case.items()
            if key not in {"rules", "applicable_dimensions"}
        ):
            raise ValueError("published case input/reference differs")
        if [dict(rule) for rule in retained.rules] != case["rules"] or list(
            retained.applicable_dimensions
        ) != case["applicable_dimensions"]:
            raise ValueError("published case scoring contract differs")

    async def publish(kind, label, definition):
        draft = await operations.call(
            label + ".create",
            {"kind": kind, "definition": definition},
            lambda request: suites.create(
                scope,
                principal,
                kind=kind,
                name="Capacity " + label,
                definition=definition,
                request_id=request,
            ),
        )
        version = await operations.call(
            label + ".publish",
            {"kind": kind, "entity_id": str(draft.id), "revision": draft.revision},
            lambda request: suites.publish(
                scope,
                principal,
                kind=kind,
                entity_id=draft.id,
                expected_revision=draft.revision,
                request_id=request,
            ),
        )
        return await suites.get_version(scope, principal, kind, version.id)

    configurations = [
        await publish("config", f"subject-{index}", selection.model_dump(mode="json"))
        for index, selection in enumerate(subjects)
    ]
    judge_version = await publish("config", "judge", judge.model_dump(mode="json"))
    rubric_definition = RubricDefinition.model_validate(
        {**binding["rubric"], "judge_config_version": str(judge_version.id)}
    )
    if [d.id for d in rubric_definition.dimensions] != ["correctness"] or any(
        c.source != "model" for c in rubric_definition.required_conditions
    ):
        raise ValueError("fixture supports independent model correctness rubric only")
    rubric = await publish("rubric", "rubric", rubric_definition.model_dump(mode="json"))
    suite = await publish(
        "suite",
        "suite",
        {
            "dataset_version": str(dataset.id),
            "config_versions": [str(c.id) for c in configurations],
            "rubric_version": str(rubric.id),
            "mode": "isolated",
            "environment_version": str(environment_id),
            "settings": suite_settings.model_dump(mode="json"),
        },
    )
    if (
        suite.quantity != 5000
        or len(set(suite.config_versions)) != 5
        or judge_version.id in suite.config_versions
    ):
        raise ValueError("published capacity matrix differs")
    return dataset, configurations, judge_version, rubric, suite


async def start_batch(service, scope, principal, operations, suite):
    key = operations.identity("batch.start")
    prior = operations.journal.get("evaluation_operation", key)
    if prior:
        payload = prior["body"]["payload"]
        if payload["suite_version"] != str(suite.id):
            raise ValueError("batch recovery suite differs")
    else:
        check = await service.preflight_factory(principal).check(scope, suite.id)
        if not check.allowed:
            raise ValueError("capacity preflight rejected")
        payload = {"suite_version": str(suite.id), "preflight_revision": check.revision}
    return await operations.call(
        "batch.start", payload, lambda request: service.start(scope, principal, request, payload)
    )


async def result_inventory(
    service, scope, principal, batch_id, cases, configs, *, include_identities=False, evidence=None
):
    if evidence is not None:
        evidence.reserve_state(len(cases) * len(configs) * 4)
        evidence.retain("batch-results", {"batch_id": batch_id, "cases": cases, "configs": configs})
    expected = {(str(case), str(config), 0) for case in cases for config in configs}
    require_standard_matrix(cases, configs, expected)
    seen, identities, runs, cursors = set(), set(), set(), set()
    execution, scoring, attempts = Counter(), Counter(), Counter()
    digest = sha256()
    cursor = None
    while True:
        page = await service.results(scope, principal, batch_id, cursor=cursor, limit=200)
        if evidence is not None:
            evidence.retain("batch-results", {"batch_id": batch_id, "cursor": cursor, "page": page})
            evidence.reserve_state(len(page["items"]) * 4)
        for result in page["items"]:
            slot = result.slot
            key = (str(slot.case_revision_id), str(slot.config_version_id), slot.repetition)
            if (
                key not in expected
                or key in seen
                or result.id in identities
                or result.run_id is None
                or result.run_id in runs
            ):
                raise ValueError("actual result matrix/provenance differs")
            seen.add(key)
            identities.add(result.id)
            runs.add(result.run_id)
            execution[result.execution_status] += 1
            scoring[result.scoring_status] += 1
            attempts[str(result.attempt)] += 1
            digest.update(result.model_dump_json().encode() + b"\n")
        cursor = page["next_cursor"]
        if not cursor:
            break
        if cursor in cursors or len(cursors) >= 25:
            raise ValueError("result pagination did not converge")
        cursors.add(cursor)
    if seen != expected:
        raise ValueError("actual result matrix incomplete")
    return {
        "results": len(identities),
        "runs": len(runs),
        "execution_counts": dict(execution),
        "scoring_counts": dict(scoring),
        "attempt_counts": dict(attempts),
        "result_digest": digest.hexdigest(),
        **(
            {
                "result_ids": sorted(str(x) for x in identities),
                "run_ids": sorted(str(x) for x in runs),
            }
            if include_identities
            else {}
        ),
    }


def require_standard_matrix(cases, configs, expected):
    if len(cases) != 1000 or len(configs) != 5 or len(expected) != 5000:
        raise ValueError("capacity matrix dimensions differ")
