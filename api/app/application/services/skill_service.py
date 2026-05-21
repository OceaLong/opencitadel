#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import re
from typing import Callable, List, Optional

from app.application.errors.exceptions import NotFoundError, BadRequestError
from app.domain.models.skill import Skill, SkillSummary
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

BUILTIN_SKILLS = [
    Skill(
        name="编程助手",
        slug="coding",
        description="专注代码编写、调试与重构",
        icon="💻",
        category="development",
        system_prompt="你是一位专业的编程助手。优先使用文件和Shell工具完成代码任务，注重代码质量与最佳实践。",
        allowed_tools=["read_file", "write_file", "replace_in_file", "shell_execute", "message_notify_user", "message_ask_user"],
        examples=["帮我写一个Python爬虫", "重构这段代码", "修复这个bug"],
        is_builtin=True,
    ),
    Skill(
        name="研究分析",
        slug="research",
        description="深度信息检索与综合分析",
        icon="🔍",
        category="research",
        system_prompt="你是一位研究分析专家。优先使用搜索和浏览器工具收集信息，提供有据可查的分析报告。",
        allowed_tools=["search_web", "browser_navigate", "browser_view", "write_file", "message_notify_user"],
        examples=["调研AI Agent最新进展", "对比三家云服务商", "分析市场趋势"],
        is_builtin=True,
    ),
    Skill(
        name="数据分析",
        slug="data-analysis",
        description="数据处理、可视化与洞察",
        icon="📊",
        category="analysis",
        system_prompt="你是一位数据分析专家。擅长处理结构化数据，生成清晰的分析结论和可视化建议。",
        allowed_tools=["read_file", "write_file", "shell_execute", "search_web", "message_notify_user"],
        examples=["分析这份CSV数据", "生成数据统计报告", "找出数据异常点"],
        is_builtin=True,
    ),
    Skill(
        name="内容写作",
        slug="writing",
        description="高质量文档与内容创作",
        icon="✍️",
        category="writing",
        system_prompt="你是一位专业内容创作者。注重文字质量、结构清晰，根据需求调整文风。",
        allowed_tools=["read_file", "write_file", "search_web", "message_notify_user", "message_ask_user"],
        examples=["写一份产品需求文档", "润色这篇文章", "生成营销文案"],
        is_builtin=True,
    ),
]


class SkillService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^\w\s-]", "", name.lower())
        return re.sub(r"[-\s]+", "-", slug).strip("-") or "skill"

    async def list_skills(self, enabled_only: bool = False) -> List[Skill]:
        async with self._uow_factory() as uow:
            return await uow.skill.get_all(enabled_only=enabled_only)

    async def get_skill(self, skill_id: str) -> Skill:
        async with self._uow_factory() as uow:
            skill = await uow.skill.get_by_id(skill_id)
        if not skill:
            raise NotFoundError(f"Skill[{skill_id}]不存在")
        return skill

    async def get_summary(self, skill_id: Optional[str]) -> Optional[SkillSummary]:
        if not skill_id:
            return None
        skill = await self.get_skill(skill_id)
        return SkillSummary(id=skill.id, name=skill.name, icon=skill.icon, examples=skill.examples)

    async def create_skill(self, skill: Skill) -> Skill:
        if not skill.slug:
            skill.slug = self._slugify(skill.name)
        async with self._uow_factory() as uow:
            existing = await uow.skill.get_by_slug(skill.slug)
            if existing:
                raise BadRequestError(f"Slug[{skill.slug}]已存在")
            await uow.skill.save(skill)
        return skill

    async def update_skill(self, skill_id: str, updates: Skill) -> Skill:
        async with self._uow_factory() as uow:
            existing = await uow.skill.get_by_id(skill_id)
            if not existing:
                raise NotFoundError(f"Skill[{skill_id}]不存在")
            updates.id = skill_id
            updates.is_builtin = existing.is_builtin
            if updates.slug != existing.slug:
                dup = await uow.skill.get_by_slug(updates.slug)
                if dup and dup.id != skill_id:
                    raise BadRequestError(f"Slug[{updates.slug}]已存在")
            await uow.skill.save(updates)
        return updates

    async def delete_skill(self, skill_id: str) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.skill.get_by_id(skill_id)
            if not existing:
                raise NotFoundError(f"Skill[{skill_id}]不存在")
            if existing.is_builtin:
                raise BadRequestError("内置Skill模板不可删除，可将其禁用")
            await uow.skill.delete_by_id(skill_id)

    async def seed_builtin_skills(self) -> None:
        async with self._uow_factory() as uow:
            count = await uow.skill.count()
            if count == 0:
                for skill in BUILTIN_SKILLS:
                    await uow.skill.save(skill)
                logger.info("已种子化4个内置Skill模板")
                return

            builtin_by_slug = {skill.slug: skill for skill in BUILTIN_SKILLS}
            existing = await uow.skill.get_all()
            updated = 0
            for skill in existing:
                if not skill.is_builtin:
                    continue
                template = builtin_by_slug.get(skill.slug)
                if not template:
                    continue
                if skill.allowed_tools != template.allowed_tools:
                    skill.allowed_tools = template.allowed_tools
                    await uow.skill.save(skill)
                    updated += 1
            if updated:
                logger.info("已同步 %d 个内置Skill的工具白名单", updated)
