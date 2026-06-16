#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import asyncio
import base64
import io
import json
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import UploadFile

from app.application.services.file_service import FileService
from app.domain.external.file_storage import FileUploadPayload
from app.application.services.llm_model_service import LLMModelService
from app.application.services.marketplace.catalog import (
    APP_IDS,
    MARKETPLACE_APPS,
    build_route_prompt,
    examples_for,
)
from app.application.services.marketplace.consumption_service import ConsumptionService
from app.application.services.marketplace.conversion_service import ConversionService
from app.application.services.marketplace.fortune_service import FortuneService
from app.application.services.marketplace.nutrition_service import NutritionService
from app.application.services.marketplace.video_search_service import VideoSearchService
from app.application.services.marketplace.watermark_service import WatermarkService
from app.application.services.marketplace.utils import analyze_image_with_llm, analyze_images_with_llm
from app.domain.models.fortune_prediction import FortunePrediction
from app.domain.services.document_service import document_to_vision_attachments
from app.domain.services.image_generation_service import edit_image
from app.domain.utils.vision import build_image_content_part, is_image_mime
from app.infrastructure.external.llm.factory import LLMFactory
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
logger = logging.getLogger(__name__)

VIDEO_SEARCH_LLM_RANK_TIMEOUT_SECONDS = 15.0

ROUTE_PROMPT = build_route_prompt()

VIDEO_RANK_PROMPT = """请为正版影视搜索结果排序并补充推荐理由。仅返回 JSON：
{{"items": [{{"url": "原 url", "recommendation_reason": "不超过 24 字"}}]}}
偏好：官方来源、可页内播放、trust_score 高、标题和查询更相关。
查询：{query}
结果：{results}"""

NUTRITION_FOLLOWUP_PROMPT = """你是营养教练。基于已结构化的餐食分析回答用户追问，简洁、可执行，不要编造图片外信息。
餐食分析 JSON：
{analysis}

用户问题：{question}"""

DOC_QA_PROMPT = """你是资料问答助手。请基于用户提供的内容回答问题；如果资料不足，明确说明缺失信息。
问题：{question}

资料：
{content}"""

TRANSLATION_PROMPT = """你是专业翻译助手。请自动识别输入语言，并按目标语言和风格翻译。仅返回 JSON：
{{"detected_language": "识别到的语言", "translated_text": "译文", "notes": ["简短说明"]}}
目标语言：{target_language}
风格：{style}
文本：
{text}"""

WATERMARK_DETECT_PROMPT = """请识别图片中的水印区域。仅返回 JSON：
{{"region": {{"x": 0.0, "y": 0.0, "width": 0.2, "height": 0.1}}, "description": "水印描述"}}
坐标为相对图片宽高的比例（0-1）。若无法识别，返回右下角常见水印区域。"""


class MarketplaceService:
    def __init__(
            self,
            llm_model_service: LLMModelService,
            file_service: FileService,
            uow_factory,
    ) -> None:
        self._llm_model_service = llm_model_service
        self._file_service = file_service
        self._uow_factory = uow_factory
        self._video = VideoSearchService()
        self._nutrition = NutritionService()
        self._consumption = ConsumptionService()
        self._conversion = ConversionService()
        self._watermark = WatermarkService()
        self._fortune = FortuneService()
        self._json_parser = RepairJSONParser()

    def list_apps(self) -> list[dict]:
        return MARKETPLACE_APPS

    async def search_videos(self, query: str, *, analyze_content: bool = False, model_id: Optional[str] = None) -> dict:
        started_at = time.perf_counter()
        bing_ms = 0
        llm_ms = 0
        rank_status = "skipped"

        bing_started = time.perf_counter()
        data = await self._video.search(query)
        bing_ms = int((time.perf_counter() - bing_started) * 1000)

        if analyze_content:
            try:
                vision_llm = await self._resolve_vision_llm(model_id)
                content = await self._invoke_text(
                    vision_llm,
                    f"基于查询「{query}」为以下正版影视结果生成一句内容理解摘要（50字内）："
                    f"{json.dumps(data.get('results', [])[:5], ensure_ascii=False)}",
                )
                data["content_summary"] = content.strip()
            except Exception as exc:
                logger.info("视频内容理解跳过: %s", exc)
        try:
            llm = await self._resolve_text_llm(None)
            llm_started = time.perf_counter()
            data = await asyncio.wait_for(
                self._rank_video_results(llm, data),
                timeout=VIDEO_SEARCH_LLM_RANK_TIMEOUT_SECONDS,
            )
            llm_ms = int((time.perf_counter() - llm_started) * 1000)
            rank_status = "ranked"
        except asyncio.TimeoutError:
            llm_ms = int((time.perf_counter() - llm_started) * 1000) if "llm_started" in locals() else 0
            rank_status = "timeout"
            logger.warning(
                "影视结果智能排序超时 query=%s llm_ms=%s budget_s=%s",
                query,
                llm_ms,
                VIDEO_SEARCH_LLM_RANK_TIMEOUT_SECONDS,
            )
            for result in data.get("results", []):
                result.setdefault("recommendation_reason", "正版来源优先推荐")
        except Exception as exc:
            rank_status = "error"
            logger.info("影视结果智能排序跳过: %s", exc)
            for result in data.get("results", []):
                result.setdefault("recommendation_reason", "正版来源优先推荐")

        total_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "影视搜索完成 query=%s bing_ms=%s llm_ms=%s total_ms=%s rank_status=%s result_count=%s",
            query,
            bing_ms,
            llm_ms,
            total_ms,
            rank_status,
            len(data.get("results", [])),
        )
        return data

    async def route_request(self, query: str, *, model_id: Optional[str] = None) -> dict:
        query = (query or "").strip()
        if not query:
            raise ValueError("请输入想完成的任务")

        heuristic = self._route_by_rules(query)
        try:
            llm = await self._resolve_text_llm(model_id)
            apps = json.dumps(MARKETPLACE_APPS, ensure_ascii=False)
            content = await self._invoke_text(
                llm,
                ROUTE_PROMPT.format(apps=apps) + f"\n\n用户输入：{query}",
            )
            parsed = await self._json_parser.invoke(content, default_value={})
            route = self._normalize_route(parsed, fallback=heuristic)
        except Exception as exc:
            logger.info("应用市场 LLM 分发降级为规则匹配: %s", exc)
            route = heuristic
        return route

    async def analyze_nutrition(
            self,
            file_id: str,
            *,
            model_id: Optional[str] = None,
            weight_kg: Optional[float] = None,
            goal: Optional[str] = None,
    ) -> dict:
        image_bytes, file_info = await self._load_image(file_id)
        llm = await self._resolve_vision_llm(model_id)
        return await self._nutrition.analyze(
            llm,
            image_bytes,
            file_info.mime_type,
            weight_kg=weight_kg,
            goal=goal,
        )

    async def answer_nutrition_followup(
            self,
            analysis: dict,
            question: str,
            *,
            model_id: Optional[str] = None,
    ) -> dict:
        llm = await self._resolve_text_llm(model_id)
        prompt = NUTRITION_FOLLOWUP_PROMPT.format(
            analysis=json.dumps(analysis, ensure_ascii=False),
            question=question.strip(),
        )
        answer = await self._invoke_text(llm, prompt)
        return {"answer": answer.strip()}

    async def analyze_consumption(
            self,
            file_id: str,
            serving_grams: float,
            *,
            model_id: Optional[str] = None,
    ) -> dict:
        image_bytes, file_info = await self._load_image(file_id)
        llm = await self._resolve_vision_llm(model_id)
        return await self._consumption.analyze_from_image(
            llm, image_bytes, file_info.mime_type, serving_grams,
        )

    def calculate_consumption_manual(self, total_grams: float, serving_grams: float) -> dict:
        return self._consumption.calculate_manual(total_grams, serving_grams)

    def correct_consumption(self, text: str, serving_grams: float) -> dict:
        total_grams = self._extract_grams(text)
        if total_grams is None:
            raise ValueError("未识别到总量，请输入如 1.2kg、500g")
        return self._consumption.calculate_manual(total_grams, serving_grams)

    async def answer_document_question(
            self,
            file_id: str,
            question: str,
            *,
            model_id: Optional[str] = None,
    ) -> dict:
        file_bytes, file_info = await self._load_file_bytes(file_id)
        mime_type = file_info.mime_type or "application/octet-stream"
        llm = await self._resolve_vision_llm(model_id) if is_image_mime(mime_type) else await self._resolve_text_llm(model_id)

        if is_image_mime(mime_type):
            parsed = await analyze_image_with_llm(
                llm, file_bytes, mime_type,
                f"请阅读这张图片并回答问题：{question}",
            )
            if isinstance(parsed, dict) and parsed.get("answer"):
                return {"answer": str(parsed["answer"]).strip(), "source_summary": "基于上传图片回答"}
            answer = parsed if isinstance(parsed, str) else str(parsed)
            return {"answer": answer.strip(), "source_summary": "基于上传图片回答"}

        if mime_type == "application/pdf" or (file_info.filename or "").lower().endswith(".pdf"):
            text, page_images = await document_to_vision_attachments(
                file_bytes, mime_type, file_info.filename or "",
            )
            if page_images:
                vision_llm = await self._resolve_vision_llm(model_id)
                images = [
                    (base64.b64decode(p["data_base64"]), p["mime_type"])
                    for p in page_images[:5]
                ]
                answer_data = await analyze_images_with_llm(
                    vision_llm,
                    images,
                    f"基于文档页面图像回答问题：{question}\n\n已抽取文本：\n{text[:4000]}",
                )
                answer = answer_data.get("answer") if isinstance(answer_data, dict) else str(answer_data)
                return {"answer": str(answer).strip(), "source_summary": f"PDF {len(page_images)} 页"}

        text = self._decode_text(file_bytes)
        if not text:
            raise ValueError("当前仅支持图片或可读取文本文件问答")
        prompt = DOC_QA_PROMPT.format(question=question.strip(), content=text[:12000])
        answer = await self._invoke_text(llm, prompt)
        return {"answer": answer.strip(), "source_summary": f"已读取约 {min(len(text), 12000)} 个字符"}

    async def translate(
            self,
            *,
            text: Optional[str],
            file_id: Optional[str],
            target_language: str,
            style: str,
            model_id: Optional[str] = None,
    ) -> dict:
        if not text and not file_id:
            raise ValueError("请输入文本或上传图片/文本文件")

        source_text = (text or "").strip()
        llm = await self._resolve_text_llm(model_id)
        if file_id:
            file_bytes, file_info = await self._load_file_bytes(file_id)
            mime_type = file_info.mime_type or "application/octet-stream"
            if is_image_mime(mime_type):
                llm = await self._resolve_vision_llm(model_id)
                messages = [{
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "请识别图片中的文字并翻译。仅返回 JSON："
                                "{\"detected_language\":\"识别到的语言\",\"translated_text\":\"译文\",\"notes\":[\"说明\"]}"
                                f"\n目标语言：{target_language}\n风格：{style}"
                            ),
                        },
                        build_image_content_part(file_bytes, mime_type),
                    ],
                }]
                response = await llm.invoke(messages)
                parsed = await self._json_parser.invoke(
                    response.get("content") or response.get("reasoning_content") or "",
                    default_value={},
                )
                return self._normalize_translation(parsed, target_language)
            decoded = self._decode_text(file_bytes)
            source_text = "\n\n".join(part for part in [source_text, decoded] if part)

        if not source_text:
            raise ValueError("未读取到可翻译文本")

        content = await self._invoke_text(
            llm,
            TRANSLATION_PROMPT.format(
                target_language=target_language,
                style=style,
                text=source_text[:12000],
            ),
        )
        parsed = await self._json_parser.invoke(content, default_value={})
        return self._normalize_translation(parsed, target_language)

    async def convert_document(
            self,
            file_id: str,
            target_format: str,
    ) -> dict:
        file_bytes, file_info = await self._load_file_bytes(file_id)
        source_ext = self._resolve_extension(file_info.filename or "", file_info.mime_type or "")
        out_bytes, out_mime, out_filename = await asyncio.to_thread(
            self._conversion.convert,
            file_bytes,
            source_ext,
            target_format,
            filename=file_info.filename or "",
        )
        stored = await self._store_output_bytes(out_bytes, out_filename, out_mime)
        return {
            "result_file_id": stored.id,
            "result_filename": stored.filename,
            "source_format": source_ext,
            "target_format": target_format.lstrip("."),
            "download_ready": True,
        }

    async def add_watermark(
            self,
            file_id: str,
            *,
            watermark_type: str = "text",
            text: Optional[str] = None,
            watermark_file_id: Optional[str] = None,
            opacity: float = 0.3,
            rotation: float = 45.0,
            tile: bool = True,
    ) -> dict:
        file_bytes, file_info = await self._load_file_bytes(file_id)
        mime_type = file_info.mime_type or "application/octet-stream"
        filename = file_info.filename or "file"

        if is_image_mime(mime_type):
            if watermark_type == "text":
                if not text:
                    raise ValueError("请提供水印文字")
                out_bytes = await asyncio.to_thread(
                    self._watermark.add_image_text_watermark,
                    file_bytes,
                    text,
                    opacity=opacity,
                    rotation=rotation,
                    tile=tile,
                )
                out_filename = f"{Path(filename).stem}_watermarked.png"
                out_mime = "image/png"
            else:
                raise ValueError("图片加水印仅支持文字水印（前端本地处理）")
        elif mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
            if watermark_type == "text":
                if not text:
                    raise ValueError("请提供水印文字")
                out_bytes = await asyncio.to_thread(
                    self._watermark.add_pdf_text_watermark,
                    file_bytes,
                    text,
                    opacity=opacity,
                    rotation=rotation,
                    tile=tile,
                )
            elif watermark_type == "image":
                if not watermark_file_id:
                    raise ValueError("请上传水印图片")
                wm_bytes, _ = await self._load_file_bytes(watermark_file_id)
                out_bytes = await asyncio.to_thread(
                    self._watermark.add_pdf_image_watermark,
                    file_bytes,
                    wm_bytes,
                    opacity=opacity,
                    tile=tile,
                )
            else:
                raise ValueError("不支持的水印类型")
            out_filename = f"{Path(filename).stem}_watermarked.pdf"
            out_mime = "application/pdf"
        else:
            raise ValueError("仅支持图片或 PDF 文件加水印")

        stored = await self._store_output_bytes(out_bytes, out_filename, out_mime)
        return {
            "result_file_id": stored.id,
            "result_filename": stored.filename,
            "download_ready": True,
        }

    async def generate_fortune_prediction(
            self,
            *,
            mode: str,
            question: str,
            input_profile: Optional[Dict[str, Any]] = None,
            model_id: Optional[str] = None,
    ) -> dict:
        llm = await self._resolve_text_llm(model_id)
        result = await self._fortune.generate(
            llm,
            mode=mode,
            question=question,
            input_profile=input_profile,
        )
        prediction = FortunePrediction(
            mode=mode,
            question=question.strip(),
            input_profile=input_profile or {},
            result=result,
        )
        async with self._uow_factory() as uow:
            await uow.fortune_prediction.save(prediction)
        return self._format_fortune_prediction(prediction)

    async def stream_fortune_prediction(
            self,
            *,
            mode: str,
            question: str,
            input_profile: Optional[Dict[str, Any]] = None,
            model_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, str], None]:
        llm = await self._resolve_text_llm(model_id)
        async for event in self._fortune.generate_stream(
                llm,
                mode=mode,
                question=question,
                input_profile=input_profile,
        ):
            if event.get("type") == "delta":
                yield {
                    "event": "delta",
                    "data": json.dumps({"text": event.get("text", "")}, ensure_ascii=False),
                }
                continue

            if event.get("type") == "done":
                result = event.get("result") or {}
                prediction = FortunePrediction(
                    mode=mode,
                    question=question.strip(),
                    input_profile=input_profile or {},
                    result=result,
                )
                async with self._uow_factory() as uow:
                    await uow.fortune_prediction.save(prediction)
                yield {
                    "event": "done",
                    "data": json.dumps(
                        self._format_fortune_prediction(prediction),
                        ensure_ascii=False,
                    ),
                }

    async def get_fortune_prediction_share(self, share_id: str) -> dict:
        share_id = (share_id or "").strip()
        if not share_id:
            raise ValueError("分享 ID 无效")
        async with self._uow_factory() as uow:
            prediction = await uow.fortune_prediction.get_by_share_id(share_id)
        if not prediction:
            raise ValueError("未找到该预测结果")
        return self._format_fortune_prediction(prediction)

    @staticmethod
    def _format_fortune_prediction(prediction: FortunePrediction) -> dict:
        result = prediction.result or {}
        return {
            "share_id": prediction.share_id,
            "mode": prediction.mode,
            "question": prediction.question,
            "input_profile": prediction.input_profile,
            "result": {
                "mode": result.get("mode", prediction.mode),
                "title": result.get("title", ""),
                "summary": result.get("summary", ""),
                "sections": result.get("sections", []),
                "lucky_items": result.get("lucky_items", {}),
                "disclaimer": result.get("disclaimer", "本结果仅供娱乐参考，请理性看待。"),
            },
            "created_at": prediction.created_at.isoformat(),
        }

    async def remove_watermark(
            self,
            file_id: str,
            *,
            watermark_text: Optional[str] = None,
            mode: str = "auto",
            model_id: Optional[str] = None,
    ) -> dict:
        file_bytes, file_info = await self._load_file_bytes(file_id)
        mime_type = file_info.mime_type or "application/octet-stream"
        filename = file_info.filename or "file"
        method = "fallback"

        if is_image_mime(mime_type):
            region: Optional[Dict[str, float]] = None
            try:
                vision_llm = await self._resolve_vision_llm(model_id)
                parsed = await analyze_image_with_llm(
                    vision_llm,
                    file_bytes,
                    mime_type,
                    WATERMARK_DETECT_PROMPT,
                )
                if isinstance(parsed, dict) and isinstance(parsed.get("region"), dict):
                    region = parsed["region"]
            except Exception as exc:
                logger.info("水印区域识别跳过: %s", exc)

            out_bytes: Optional[bytes] = None
            try:
                model = await self._llm_model_service.resolve_model(model_id)
                mask_bytes = await asyncio.to_thread(
                    self._watermark.build_mask_from_region,
                    file_bytes,
                    region or {"x": 0.65, "y": 0.82, "width": 0.3, "height": 0.12},
                )
                edited = await edit_image(
                    file_bytes,
                    mask_bytes,
                    "Remove the watermark and reconstruct the background naturally.",
                    model,
                    self._file_service.file_storage,
                    mime_type=mime_type,
                )
                if edited:
                    out_bytes, _ = edited
                    method = "ai_inpaint"
            except Exception as exc:
                logger.info("AI 去水印跳过: %s", exc)

            if not out_bytes:
                out_bytes = await asyncio.to_thread(
                    self._watermark.remove_image_watermark_fallback,
                    file_bytes,
                    region,
                )
                method = "blur_fallback"
            out_filename = f"{Path(filename).stem}_dewatermarked.png"
            out_mime = "image/png"
        elif mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
            out_bytes = await asyncio.to_thread(
                self._watermark.remove_pdf_watermark,
                file_bytes,
                watermark_text=watermark_text,
                mode=mode,
            )
            out_filename = f"{Path(filename).stem}_dewatermarked.pdf"
            out_mime = "application/pdf"
            method = "pdf_redaction"
        else:
            raise ValueError("仅支持图片或 PDF 文件去水印")

        stored = await self._store_output_bytes(out_bytes, out_filename, out_mime)
        return {
            "result_file_id": stored.id,
            "result_filename": stored.filename,
            "method": method,
            "download_ready": True,
        }

    async def _store_output_bytes(self, data: bytes, filename: str, mime_type: str):
        stream = io.BytesIO(data)
        upload = FileUploadPayload(
            file=stream,
            filename=filename,
            size=len(data),
            content_type=mime_type,
        )
        return await self._file_service.file_storage.upload_file(upload)

    @staticmethod
    def _resolve_extension(filename: str, mime_type: str) -> str:
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext:
            return ext
        mime_map = {
            "application/pdf": "pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "text/markdown": "md",
            "text/plain": "txt",
        }
        return mime_map.get(mime_type, "")

    async def _load_image(self, file_id: str):
        file_bytes, file_info = await self._load_file_bytes(file_id)
        return file_bytes, file_info

    async def _load_file_bytes(self, file_id: str):
        file_data, file_info = await self._file_service.download_file(file_id)
        file_bytes = await asyncio.to_thread(file_data.read)
        from app.application.services.config_provider import get_runtime_config
        max_bytes = get_runtime_config().server.marketplace_max_upload_bytes
        if len(file_bytes) > max_bytes:
            raise ValueError(f"文件过大，请上传不超过 {max_bytes // (1024 * 1024)}MB 的文件")
        return file_bytes, file_info

    async def _resolve_vision_llm(self, model_id: Optional[str]):
        model = await self._llm_model_service.resolve_model(model_id)
        if not model.capabilities.vision and not model.supports_multimodal:
            raise ValueError("请选择支持多模态能力的模型，或在模型设置中开启视觉能力")
        return LLMFactory.create(model)

    async def _resolve_text_llm(self, model_id: Optional[str]):
        model = await self._llm_model_service.resolve_model(model_id)
        return LLMFactory.create(model)

    async def _invoke_text(self, llm, prompt: str) -> str:
        response = await llm.invoke([{"role": "user", "content": prompt}])
        content = response.get("content") or response.get("reasoning_content") or ""
        if not content:
            raise ValueError("模型未返回有效内容")
        return str(content)

    async def _rank_video_results(self, llm, data: dict) -> dict:
        compact_results = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "platform": item.get("platform"),
                "trust_score": item.get("trust_score"),
                "source_type": item.get("source_type"),
            }
            for item in data.get("results", [])[:12]
        ]
        if not compact_results:
            return data
        content = await self._invoke_text(
            llm,
            VIDEO_RANK_PROMPT.format(
                query=data.get("query", ""),
                results=json.dumps(compact_results, ensure_ascii=False),
            ),
        )
        parsed = await self._json_parser.invoke(content, default_value={})
        ranked = parsed.get("items") if isinstance(parsed, dict) else []
        reasons = {
            item.get("url"): item.get("recommendation_reason")
            for item in ranked
            if isinstance(item, dict) and item.get("url")
        }
        order = {item.get("url"): idx for idx, item in enumerate(ranked) if isinstance(item, dict)}
        for item in data.get("results", []):
            item["recommendation_reason"] = reasons.get(item.get("url")) or "正版来源优先推荐"
        data["results"] = sorted(
            data.get("results", []),
            key=lambda item: (order.get(item.get("url"), 999), -float(item.get("trust_score") or 0)),
        )
        return data

    def _route_by_rules(self, query: str) -> dict:
        lowered = query.lower()
        app_id = "prompt-lab"
        params: Dict[str, Any] = {}
        if any(word in lowered for word in ["电影", "电视剧", "影视", "剧名", "播放", "观看", "video", "movie"]):
            app_id = "video-search"
            params["query"] = self._strip_intent_words(query)
        elif any(word in query for word in ["营养", "热量", "蛋白", "减脂", "增肌", "餐食"]):
            app_id = "nutrition-analysis"
            if "减脂" in query:
                params["goal"] = "cut"
            elif "增肌" in query:
                params["goal"] = "bulk"
        elif any(word in query for word in ["净含量", "食用", "能吃", "消耗", "包装"]):
            app_id = "consumption-calculator"
            grams = self._extract_grams(query)
            if grams:
                params["total_grams"] = grams
        elif any(word in query for word in ["翻译", "译成", "英文", "中文", "日文"]):
            app_id = "smart-translation"
            params["text"] = re.sub(r"^(请|帮我)?(翻译|把)", "", query).strip()
        elif any(word in query for word in ["文档", "总结", "问答", "截图", "资料"]):
            app_id = "document-qa"
            params["question"] = query
        elif any(word in query for word in ["二维码", "qr", "QR"]):
            app_id = "qr-generator"
            params["text"] = query
        elif any(word in lowered for word in ["json", "base64", "url编码", "url解码", "格式化"]):
            app_id = "dev-toolbox"
            params["text"] = query
        elif any(word in query for word in ["密码", "uuid", "UUID", "随机串"]):
            app_id = "secret-generator"
            length_match = re.search(r"(\d+)\s*位", query)
            if length_match:
                params["length"] = int(length_match.group(1))
        elif any(word in query for word in ["换算", "单位", "公里", "英里", "华氏", "摄氏", "gb", "mb"]):
            app_id = "unit-converter"
            params["text"] = query
        elif any(word in query for word in ["转pdf", "转word", "转docx", "格式转换", "文档转换", "pdf转", "word转", "md转"]):
            app_id = "document-converter"
            if "pdf" in lowered:
                params["target_format"] = "pdf"
            elif any(word in lowered for word in ["word", "docx"]):
                params["target_format"] = "docx"
            elif "md" in lowered or "markdown" in lowered:
                params["target_format"] = "md"
            elif "txt" in lowered:
                params["target_format"] = "txt"
        elif any(word in query for word in ["水印", "去水印", "加水印"]):
            app_id = "watermark-tool"
            if "去" in query:
                params["mode"] = "remove"
            else:
                params["mode"] = "add"
            if text_match := re.search(r"[「\"'](.+?)[」\"']", query):
                params["text"] = text_match.group(1)
        elif any(word in query for word in ["运势", "抽签", "算命", "星盘", "塔罗", "占卜", "预测", "星座"]):
            app_id = "fortune-teller"
            if any(word in query for word in ["抽签", "签文", "灵签"]):
                params["mode"] = "lottery"
            elif any(word in query for word in ["算命", "八字", "卦", "占卜"]):
                params["mode"] = "divination"
            elif any(word in query for word in ["星盘", "星座", "占星"]):
                params["mode"] = "astrology"
            else:
                params["mode"] = "fortune"
            params["question"] = query
        return {
            "app_id": app_id,
            "confidence": 0.72,
            "reason": "根据关键词为你匹配最合适的小应用",
            "params": params,
            "suggestions": self._app_examples(app_id)[:2],
        }

    def _normalize_route(self, parsed: dict, *, fallback: dict) -> dict:
        if not isinstance(parsed, dict):
            return fallback
        app_id = parsed.get("app_id")
        if app_id not in APP_IDS:
            return fallback
        confidence = parsed.get("confidence")
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = fallback["confidence"]
        params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
        suggestions = parsed.get("suggestions") if isinstance(parsed.get("suggestions"), list) else []
        return {
            "app_id": app_id,
            "confidence": confidence,
            "reason": str(parsed.get("reason") or fallback["reason"]),
            "params": params,
            "suggestions": [str(item) for item in suggestions[:3]] or self._app_examples(app_id)[:2],
        }

    def _app_examples(self, app_id: str) -> list[str]:
        return examples_for(app_id)

    @staticmethod
    def _strip_intent_words(query: str) -> str:
        cleaned = query
        for word in ["搜索", "查找", "帮我找", "免费观看入口", "正版播放", "播放", "观看"]:
            cleaned = cleaned.replace(word, "")
        return cleaned.strip(" ，。") or query

    @staticmethod
    def _extract_grams(text: str) -> Optional[float]:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|千克|g|克|ml|毫升|l|升)", text, re.IGNORECASE)
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2).lower()
        if unit in {"kg", "千克", "l", "升"}:
            return value * 1000
        return value

    @staticmethod
    def _decode_text(file_bytes: bytes) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return file_bytes.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return ""

    @staticmethod
    def _normalize_translation(parsed: dict, target_language: str) -> dict:
        if not isinstance(parsed, dict):
            parsed = {}
        translated = parsed.get("translated_text") or parsed.get("translation") or ""
        return {
            "detected_language": str(parsed.get("detected_language") or "自动识别"),
            "target_language": target_language,
            "translated_text": str(translated).strip(),
            "notes": [str(item) for item in (parsed.get("notes") or [])],
        }
