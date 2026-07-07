#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from typing import Dict, Optional, Tuple


def encode_notification_message(
        message: str,
        *,
        i18n_key: Optional[str] = None,
        i18n_params: Optional[Dict[str, str]] = None,
) -> str:
    if not i18n_key:
        return message
    payload = {
        "__i18n__": True,
        "i18n_key": i18n_key,
        "i18n_params": i18n_params or {},
        "fallback": message,
    }
    return json.dumps(payload, ensure_ascii=False)


def decode_notification_message(raw: str) -> Tuple[str, Optional[str], Optional[Dict[str, str]]]:
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw, None, None
    if not isinstance(parsed, dict) or not parsed.get("__i18n__"):
        return raw, None, None
    return (
        str(parsed.get("fallback") or ""),
        parsed.get("i18n_key"),
        parsed.get("i18n_params") if isinstance(parsed.get("i18n_params"), dict) else None,
    )
