#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.questionnaire_service import QuestionnaireService
from app.domain.models.questionnaire import Questionnaire, QuestionnaireStatus


class InMemoryQuestionnaireRepo:
    def __init__(self) -> None:
        self.questionnaires: dict[str, Questionnaire] = {}
        self.responses: list = []

    async def save(self, questionnaire: Questionnaire) -> None:
        self.questionnaires[questionnaire.id] = questionnaire

    async def get_by_id(self, questionnaire_id: str):
        return self.questionnaires.get(questionnaire_id)

    async def get_by_slug(self, slug: str):
        for q in self.questionnaires.values():
            if q.slug == slug:
                return q
        return None

    async def save_response(self, response) -> None:
        self.responses.append(response)

    async def count_responses(self, questionnaire_id: str) -> int:
        return sum(1 for r in self.responses if r.questionnaire_id == questionnaire_id)

    async def list_responses(self, questionnaire_id: str, *, limit: int = 5000):
        return [r for r in self.responses if r.questionnaire_id == questionnaire_id][:limit]


class FakeUow:
    def __init__(self, repo: InMemoryQuestionnaireRepo) -> None:
        self.questionnaire = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.fixture
def service():
    repo = InMemoryQuestionnaireRepo()
    return QuestionnaireService(uow_factory=lambda: FakeUow(repo)), repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_create_questionnaire_returns_manage_token(service):
    svc, _ = service
    data = await svc.create("满意度调查", questions=[{"id": "q1", "type": "text", "text": "建议"}])
    assert data["title"] == "满意度调查"
    assert data["manage_token"]
    assert data["slug"]


@pytest.mark.anyio
async def test_submit_response_validates_required_fields(service):
    svc, repo = service
    created = await svc.create(
        "测试问卷",
        questions=[{"id": "q1", "type": "multiple", "text": "选择", "required": True, "options": [{"id": "a", "text": "A"}]}],
    )
    await svc.publish(created["id"], created["manage_token"])
    q = await repo.get_by_id(created["id"])
    with pytest.raises(ValueError, match="请回答"):
        await svc.submit_response(q.slug, {"q1": []})


def test_validate_answers_rejects_invalid_single_option(service):
    svc, _ = service
    questions = [{
        "id": "q1",
        "type": "single",
        "text": "颜色",
        "required": True,
        "options": [{"id": "red", "text": "红"}],
    }]
    with pytest.raises(ValueError, match="选项无效"):
        svc._validate_answers(questions, {"q1": "blue"})
