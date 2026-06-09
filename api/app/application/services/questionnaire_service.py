#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import secrets
import string
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from app.domain.models.questionnaire import (
    Questionnaire,
    QuestionnaireResponse,
    QuestionnaireStatus,
)
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


_MAX_SLUG_ATTEMPTS = 8


def _slug(length: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


class QuestionnaireService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def _allocate_slug(self) -> str:
        for _ in range(_MAX_SLUG_ATTEMPTS):
            candidate = _slug()
            async with self._uow_factory() as uow:
                existing = await uow.questionnaire.get_by_slug(candidate)
            if not existing:
                return candidate
        raise ValueError("问卷链接生成失败，请稍后重试")

    @staticmethod
    def _validate_answers(questions: List[Dict[str, Any]], answers: Dict[str, Any]) -> None:
        for question in questions:
            qid = question.get("id", "")
            qtype = question.get("type", "text")
            required = question.get("required", True)
            value = answers.get(qid)

            if required:
                if value is None or value == "":
                    raise ValueError(f"请回答：{question.get('text', qid)}")
                if qtype == "multiple" and isinstance(value, list) and len(value) == 0:
                    raise ValueError(f"请回答：{question.get('text', qid)}")

            if value is None or value == "":
                continue

            if qtype in ("single", "multiple"):
                option_ids = {opt.get("id") for opt in question.get("options", [])}
                if qtype == "single":
                    if not isinstance(value, str) or value not in option_ids:
                        raise ValueError(f"题目「{question.get('text', qid)}」选项无效")
                elif not isinstance(value, list) or any(v not in option_ids for v in value):
                    raise ValueError(f"题目「{question.get('text', qid)}」选项无效")
            elif qtype == "rating":
                max_rating = int(question.get("rating_max", 5))
                if not isinstance(value, (int, float)) or not 1 <= float(value) <= max_rating:
                    raise ValueError(f"题目「{question.get('text', qid)}」评分无效")

    async def create(
            self,
            title: str,
            description: str = "",
            questions: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        now = datetime.utcnow()
        q = Questionnaire(
            id=str(uuid4()),
            title=title.strip() or "未命名问卷",
            description=description.strip(),
            questions=questions or [],
            status=QuestionnaireStatus.DRAFT,
            slug=await self._allocate_slug(),
            manage_token=_token(),
            created_at=now,
            updated_at=now,
        )
        async with self._uow_factory() as uow:
            await uow.questionnaire.save(q)
        return self._to_dict(q, include_token=True)

    async def update(
            self,
            questionnaire_id: str,
            manage_token: str,
            *,
            title: Optional[str] = None,
            description: Optional[str] = None,
            questions: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        async with self._uow_factory() as uow:
            q = await uow.questionnaire.get_by_id(questionnaire_id)
            if not q:
                raise ValueError("问卷不存在")
            if q.manage_token != manage_token:
                raise ValueError("无权限修改此问卷")
            if title is not None:
                q.title = title.strip() or q.title
            if description is not None:
                q.description = description.strip()
            if questions is not None:
                q.questions = questions
            q.updated_at = datetime.utcnow()
            await uow.questionnaire.save(q)
        return self._to_dict(q, include_token=True)

    async def publish(self, questionnaire_id: str, manage_token: str) -> dict:
        async with self._uow_factory() as uow:
            q = await uow.questionnaire.get_by_id(questionnaire_id)
            if not q:
                raise ValueError("问卷不存在")
            if q.manage_token != manage_token:
                raise ValueError("无权限发布此问卷")
            if not q.questions:
                raise ValueError("请至少添加一道题目")
            q.status = QuestionnaireStatus.PUBLISHED
            q.updated_at = datetime.utcnow()
            await uow.questionnaire.save(q)
        return self._to_dict(q, include_token=True)

    async def close(self, questionnaire_id: str, manage_token: str) -> dict:
        async with self._uow_factory() as uow:
            q = await uow.questionnaire.get_by_id(questionnaire_id)
            if not q:
                raise ValueError("问卷不存在")
            if q.manage_token != manage_token:
                raise ValueError("无权限关闭此问卷")
            q.status = QuestionnaireStatus.CLOSED
            q.updated_at = datetime.utcnow()
            await uow.questionnaire.save(q)
        return self._to_dict(q, include_token=True)

    async def get_by_id(self, questionnaire_id: str, manage_token: str) -> dict:
        async with self._uow_factory() as uow:
            q = await uow.questionnaire.get_by_id(questionnaire_id)
            if not q:
                raise ValueError("问卷不存在")
            if q.manage_token != manage_token:
                raise ValueError("无权限查看此问卷")
        return self._to_dict(q, include_token=True)

    async def get_public(self, slug: str) -> dict:
        async with self._uow_factory() as uow:
            q = await uow.questionnaire.get_by_slug(slug)
            if not q:
                raise ValueError("问卷不存在")
            if q.status != QuestionnaireStatus.PUBLISHED:
                raise ValueError("问卷未发布或已关闭")
        return self._to_dict(q, include_token=False)

    async def submit_response(
            self,
            slug: str,
            answers: Dict[str, Any],
            respondent_name: Optional[str] = None,
    ) -> dict:
        async with self._uow_factory() as uow:
            q = await uow.questionnaire.get_by_slug(slug)
            if not q:
                raise ValueError("问卷不存在")
            if q.status != QuestionnaireStatus.PUBLISHED:
                raise ValueError("问卷未发布或已关闭")
            self._validate_answers(q.questions, answers)
            resp = QuestionnaireResponse(
                id=str(uuid4()),
                questionnaire_id=q.id,
                answers=answers,
                respondent_name=(respondent_name or "").strip() or None,
                created_at=datetime.utcnow(),
            )
            await uow.questionnaire.save_response(resp)
        return {"id": resp.id, "message": "提交成功，感谢参与！"}

    async def get_stats(self, questionnaire_id: str, manage_token: str) -> dict:
        async with self._uow_factory() as uow:
            q = await uow.questionnaire.get_by_id(questionnaire_id)
            if not q:
                raise ValueError("问卷不存在")
            if q.manage_token != manage_token:
                raise ValueError("无权限查看统计")
            total = await uow.questionnaire.count_responses(questionnaire_id)
            responses = await uow.questionnaire.list_responses(questionnaire_id, limit=5000)

        total = total or len(responses)
        per_question: Dict[str, Any] = {}

        for question in q.questions:
            qid = question.get("id", "")
            qtype = question.get("type", "text")
            qtext = question.get("text", "")

            if qtype in ("single", "multiple"):
                options = question.get("options", [])
                counts: Dict[str, int] = {opt.get("id", ""): 0 for opt in options}
                for resp in responses:
                    val = resp.answers.get(qid)
                    if qtype == "single" and isinstance(val, str):
                        counts[val] = counts.get(val, 0) + 1
                    elif qtype == "multiple" and isinstance(val, list):
                        for v in val:
                            counts[v] = counts.get(v, 0) + 1
                per_question[qid] = {
                    "text": qtext,
                    "type": qtype,
                    "counts": counts,
                    "labels": {opt.get("id", ""): opt.get("text", "") for opt in options},
                }
            elif qtype == "rating":
                values: List[float] = []
                for resp in responses:
                    val = resp.answers.get(qid)
                    if isinstance(val, (int, float)):
                        values.append(float(val))
                per_question[qid] = {
                    "text": qtext,
                    "type": qtype,
                    "average": round(sum(values) / len(values), 2) if values else 0,
                    "count": len(values),
                    "distribution": self._rating_dist(values, question.get("rating_max", 5)),
                }
            else:
                texts = []
                for resp in responses:
                    val = resp.answers.get(qid)
                    if val:
                        texts.append({"text": str(val), "name": resp.respondent_name})
                per_question[qid] = {
                    "text": qtext,
                    "type": qtype,
                    "responses": texts,
                }

        return {
            "questionnaire_id": q.id,
            "title": q.title,
            "status": q.status.value,
            "slug": q.slug,
            "total_responses": total,
            "per_question": per_question,
        }

    @staticmethod
    def _rating_dist(values: List[float], max_rating: int) -> Dict[str, int]:
        dist = {str(i): 0 for i in range(1, max_rating + 1)}
        for v in values:
            key = str(int(v))
            if key in dist:
                dist[key] += 1
        return dist

    @staticmethod
    def _to_dict(q: Questionnaire, *, include_token: bool) -> dict:
        data = {
            "id": q.id,
            "title": q.title,
            "description": q.description,
            "questions": q.questions,
            "status": q.status.value,
            "slug": q.slug,
            "created_at": q.created_at.isoformat() if q.created_at else None,
            "updated_at": q.updated_at.isoformat() if q.updated_at else None,
        }
        if include_token:
            data["manage_token"] = q.manage_token
        return data
