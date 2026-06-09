#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF/图片水印处理服务。"""
import io
import logging
from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_PAGES = 50


class WatermarkService:
    def add_pdf_text_watermark(
            self,
            pdf_bytes: bytes,
            text: str,
            *,
            opacity: float = 0.3,
            rotation: float = 45.0,
            tile: bool = True,
    ) -> bytes:
        try:
            import fitz
        except ImportError as exc:
            raise ValueError("pymupdf 未安装，无法处理 PDF 水印") from exc

        if not text.strip():
            raise ValueError("水印文字不能为空")
        if len(pdf_bytes) > _MAX_FILE_BYTES:
            raise ValueError("文件大小不能超过 20MB")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            for page in doc:
                rect = page.rect
                font_size = max(18, int(min(rect.width, rect.height) / 18))
                if tile:
                    step_x = font_size * max(len(text), 4) * 0.6
                    step_y = font_size * 3
                    for y in range(0, int(rect.height) + int(step_y), int(step_y)):
                        for x in range(0, int(rect.width) + int(step_x), int(step_x)):
                            page.insert_text(
                                fitz.Point(x, y + font_size),
                                text,
                                fontsize=font_size,
                                rotate=rotation,
                                color=(0.5, 0.5, 0.5),
                                fill_opacity=opacity,
                            )
                else:
                    center = fitz.Point(rect.width / 2, rect.height / 2)
                    page.insert_text(
                        center,
                        text,
                        fontsize=font_size * 2,
                        rotate=rotation,
                        color=(0.5, 0.5, 0.5),
                        fill_opacity=opacity,
                    )
            return doc.tobytes()
        finally:
            doc.close()

    def add_pdf_image_watermark(
            self,
            pdf_bytes: bytes,
            image_bytes: bytes,
            *,
            opacity: float = 0.3,
            tile: bool = False,
    ) -> bytes:
        try:
            import fitz
        except ImportError as exc:
            raise ValueError("pymupdf 未安装，无法处理 PDF 水印") from exc

        if len(pdf_bytes) > _MAX_FILE_BYTES:
            raise ValueError("文件大小不能超过 20MB")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            for page in doc:
                rect = page.rect
                with Image.open(io.BytesIO(image_bytes)) as img:
                    img = img.convert("RGBA")
                    alpha = img.split()[3]
                    alpha = alpha.point(lambda p: int(p * opacity))
                    img.putalpha(alpha)
                    buffer = io.BytesIO()
                    img.save(buffer, format="PNG")
                    wm_bytes = buffer.getvalue()

                if tile:
                    wm_rect = fitz.Rect(0, 0, rect.width / 4, rect.height / 4)
                    cols = 4
                    rows = 4
                    for row in range(rows):
                        for col in range(cols):
                            x0 = col * (rect.width / cols)
                            y0 = row * (rect.height / rows)
                            target = fitz.Rect(x0, y0, x0 + wm_rect.width, y0 + wm_rect.height)
                            page.insert_image(target, stream=wm_bytes, keep_proportion=True)
                else:
                    target = fitz.Rect(
                        rect.width * 0.35,
                        rect.height * 0.35,
                        rect.width * 0.65,
                        rect.height * 0.65,
                    )
                    page.insert_image(target, stream=wm_bytes, keep_proportion=True)
            return doc.tobytes()
        finally:
            doc.close()

    def remove_pdf_watermark(
            self,
            pdf_bytes: bytes,
            *,
            watermark_text: Optional[str] = None,
            mode: str = "auto",
    ) -> bytes:
        try:
            import fitz
        except ImportError as exc:
            raise ValueError("pymupdf 未安装，无法处理 PDF") from exc

        if len(pdf_bytes) > _MAX_FILE_BYTES:
            raise ValueError("文件大小不能超过 20MB")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            for page in doc:
                if watermark_text and mode in {"auto", "text"}:
                    hits = page.search_for(watermark_text)
                    for rect in hits:
                        page.add_redact_annot(rect, fill=(1, 1, 1))
                    page.apply_redactions()

                if mode in {"auto", "images"}:
                    for img_info in page.get_images(full=True):
                        xref = img_info[0]
                        try:
                            img_rects = page.get_image_rects(xref)
                        except Exception:
                            continue
                        for rect in img_rects:
                            if rect.width < page.rect.width * 0.35 and rect.height < page.rect.height * 0.35:
                                continue
                            if rect.width > page.rect.width * 0.5 or rect.height > page.rect.height * 0.5:
                                page.add_redact_annot(rect, fill=(1, 1, 1))
                    page.apply_redactions()
            return doc.tobytes()
        finally:
            doc.close()

    def add_image_text_watermark(
            self,
            image_bytes: bytes,
            text: str,
            *,
            opacity: float = 0.35,
            rotation: float = -30.0,
            tile: bool = True,
    ) -> bytes:
        if not text.strip():
            raise ValueError("水印文字不能为空")
        if len(image_bytes) > _MAX_FILE_BYTES:
            raise ValueError("文件大小不能超过 20MB")

        with Image.open(io.BytesIO(image_bytes)) as base:
            base = base.convert("RGBA")
            overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(overlay)
            font_size = max(20, int(min(base.size) / 16))
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
            except Exception:
                font = ImageFont.load_default()

            alpha = int(255 * max(0.05, min(opacity, 1.0)))
            fill = (128, 128, 128, alpha)

            if tile:
                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                step_x = int(text_w * 1.8) or 120
                step_y = int(text_h * 3) or 80
                for y in range(-step_y, base.height + step_y, step_y):
                    for x in range(-step_x, base.width + step_x, step_x):
                        tile_img = Image.new("RGBA", (step_x, step_y), (0, 0, 0, 0))
                        tile_draw = ImageDraw.Draw(tile_img)
                        tile_draw.text((10, 10), text, font=font, fill=fill)
                        rotated = tile_img.rotate(rotation, expand=True)
                        overlay.paste(rotated, (x, y), rotated)
            else:
                text_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
                text_draw = ImageDraw.Draw(text_layer)
                bbox = text_draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                pos = ((base.width - text_w) / 2, (base.height - text_h) / 2)
                text_draw.text(pos, text, font=font, fill=fill)
                text_layer = text_layer.rotate(rotation, expand=False, center=(base.width / 2, base.height / 2))
                overlay = Image.alpha_composite(overlay, text_layer)

            result = Image.alpha_composite(base, overlay).convert("RGB")
            out = io.BytesIO()
            result.save(out, format="PNG")
            return out.getvalue()

    def remove_image_watermark_fallback(
            self,
            image_bytes: bytes,
            region: Optional[Dict[str, float]] = None,
    ) -> bytes:
        """PIL 模糊回退去水印（best-effort）。"""
        if len(image_bytes) > _MAX_FILE_BYTES:
            raise ValueError("文件大小不能超过 20MB")

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            width, height = img.size
            if region:
                x = int(region.get("x", 0) * width)
                y = int(region.get("y", 0) * height)
                w = int(region.get("width", 0.2) * width)
                h = int(region.get("height", 0.1) * height)
            else:
                x = int(width * 0.65)
                y = int(height * 0.82)
                w = int(width * 0.3)
                h = int(height * 0.12)

            x = max(0, min(x, width - 1))
            y = max(0, min(y, height - 1))
            w = max(1, min(w, width - x))
            h = max(1, min(h, height - y))

            patch = img.crop((x, y, x + w, y + h))
            blurred = patch.filter(ImageFilter.GaussianBlur(radius=max(4, min(w, h) // 8)))
            img.paste(blurred, (x, y))

            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()

    @staticmethod
    def build_mask_from_region(
            image_bytes: bytes,
            region: Dict[str, float],
    ) -> bytes:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            mask = Image.new("L", (width, height), 0)
            draw = ImageDraw.Draw(mask)
            x = int(region.get("x", 0) * width)
            y = int(region.get("y", 0) * height)
            w = int(region.get("width", 0.2) * width)
            h = int(region.get("height", 0.1) * height)
            draw.rectangle((x, y, x + w, y + h), fill=255)
            out = io.BytesIO()
            mask.save(out, format="PNG")
            return out.getvalue()
