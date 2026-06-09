#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多模态小型评测脚本（离线 mock，不调用真实 LLM）。
用法: cd api && python -m scripts.eval_multimodal
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

# 确保 api 根目录在 path 中
API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.application.services.marketplace.utils import validate_json_schema
from app.domain.utils.vision_tokens import estimate_image_tokens, estimate_messages_tokens


def _eval_token_budget() -> dict:
    tokens = estimate_image_tokens(2048, 2048, detail="high")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "test"},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{base64.b64encode(b'x'*500).decode()}",
            }},
        ],
    }]
    total = estimate_messages_tokens(messages)
    return {"vision_tokens_2048": tokens, "message_total_tokens": total, "pass": tokens > 85 and total > 85}


def _eval_schema_validation() -> dict:
    schema = {"required": ["items"], "properties": {"items": {"type": "array"}}}
    ok = validate_json_schema({"items": [{"name": "rice"}]}, schema)
    bad = validate_json_schema({"meal": "x"}, schema)
    return {"valid_pass": ok is not None, "invalid_pass": bad is None}


def main() -> None:
    results = {
        "token_budget": _eval_token_budget(),
        "schema_validation": _eval_schema_validation(),
    }
    all_pass = all(
        v.get("pass", v.get("valid_pass", True)) and v.get("invalid_pass", True)
        for v in results.values()
    )
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nOverall: {'PASS' if all_pass else 'FAIL'}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
