"""Real import grammar and BatchService paging over substituted persistence."""

import asyncio
import io
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.evaluation.batch_service import BatchService
from app.application.evaluation.import_parser import parse_import
from app.domain.models.scope import OwnerScope, Principal


def test_capacity_import_is_valid_thousand_actual_cases_without_manufactured_ids():
    from scripts.execution_capacity.batch import dataset_bytes

    raw = dataset_bytes()
    parsed = parse_import(io.BytesIO(raw), content_type="application/json")
    assert not parsed.errors
    assert len(parsed.cases) == len({c.case_key for c in parsed.cases}) == 1000
    assert all(
        c.reference_confirmed and c.rules and c.applicable_dimensions == ("correctness",)
        for c in parsed.cases
    )
    assert all("id" not in c and "input_confirmed" not in c for c in json.loads(raw)["cases"])


def test_full_persisted_matrix_readback_uses_real_service_cursors_and_rejects_missing_slot():
    from scripts.execution_capacity.batch import result_inventory

    cases, configs = [uuid4() for _ in range(1000)], [uuid4() for _ in range(5)]
    rows = [
        {
            "id": uuid4(),
            "ordinal": i * 5 + j,
            "case_revision_id": c,
            "config_version_id": g,
            "repetition": 0,
            "run_id": uuid4(),
            "execution_status": "succeeded",
            "scoring_status": "complete",
            "attempt": 0,
            "revision": 3,
        }
        for i, c in enumerate(cases)
        for j, g in enumerate(configs)
    ]

    class Repo:
        async def authorize(self, *args, **kwargs):
            pass

        async def get(self, *args):
            return {}

        async def results(self, *args, after, limit):
            return [row for row in rows if row["ordinal"] > after][:limit]

    repo = Repo()

    @asynccontextmanager
    async def work(*args):
        yield SimpleNamespace(evaluation_dataset=repo, evaluation_batch=repo)

    service = BatchService(
        SimpleNamespace(uow_factory=work, cursor_secret=b"unit-cursor-secret"),
        preflight_factory=None,
    )
    args = (
        service,
        OwnerScope.personal("unit"),
        Principal(user_id="unit"),
        uuid4(),
        cases,
        configs,
    )
    result = asyncio.run(result_inventory(*args))
    assert result["results"] == result["runs"] == 5000
    assert result["execution_counts"] == {"succeeded": 5000}
    rows.pop()
    with pytest.raises(ValueError, match="matrix"):
        asyncio.run(result_inventory(*args))


def test_durable_operation_records_before_effect_and_replays_original_input(tmp_path):
    from scripts.execution_capacity.batch import Operations
    from scripts.execution_capacity.observers import RecoveryJournal

    (tmp_path / "private").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "private") as journal:
        operations = Operations(journal, uuid4(), "user:unit", "unit")
        count = 0

        async def effect(request_id):
            nonlocal count
            assert journal.parent("evaluation_operation", request_id)["payload"] == {"name": "one"}
            count += 1
            return SimpleNamespace(id="actual-id")

        result = asyncio.run(operations.call("create", {"name": "one"}, effect))
        assert result.id == "actual-id"
        assert count == 1
        with pytest.raises(ValueError, match="intent differs"):
            asyncio.run(operations.call("create", {"name": "changed"}, effect))
        assert count == 1


def test_corpus_subject_and_judge_match_actual_provider_pure_contract():
    import subprocess
    from pathlib import Path

    from scripts.execution_capacity.batch import dataset_bytes

    script = """
import {completeChat} from "./e2e/fixtures/inference-provider/lib/chat.mjs";
let raw = ''; for await (const piece of process.stdin) raw += piece;
for (const c of JSON.parse(raw).cases) {
 const request = {model:'acceptance-capacity',messages:[{role:'user',content:c.input}],max_tokens:256,temperature:0};
 const subject = completeChat(request).choices[0].message.content;
 if (subject !== c.reference_answer) throw Error('reference differs');
 const material = {task:c.input,subject,reference:c.reference_answer,rubric:[{id:'correctness',name:'Correctness',anchors:['one','two','three','four','five'],evidence_required:false}],evidence:{},unavailable:{},resources:[],recording:null};
 const result = completeChat({...request,messages:[{role:'system',content:'Evaluation judge protocol v1.'},{role:'user',content:JSON.stringify(material)}]});
 if (JSON.parse(result.choices[0].message.content).status !== 'complete') throw Error('judge rejected');
}
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=dataset_bytes(),
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
