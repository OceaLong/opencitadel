#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Provider-specific structured output schema helpers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Type

from pydantic import BaseModel


def _json_schema_for(model_class: Type[BaseModel]) -> Dict[str, Any]:
    return model_class.model_json_schema()


def _resolve_ref(ref: str, root: Dict[str, Any]) -> Dict[str, Any]:
    if not ref.startswith("#/$defs/"):
        return {}
    key = ref.removeprefix("#/$defs/")
    defs = root.get("$defs") or {}
    target = defs.get(key) or {}
    return deepcopy(target)


def _inline_refs(node: Any, root: Dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_inline_refs(item, root) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        resolved = _resolve_ref(str(node["$ref"]), root)
        merged = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        return _inline_refs(merged, root)

    return {
        key: _inline_refs(value, root)
        for key, value in node.items()
        if key != "$defs"
    }


def _to_strict_object(node: Any) -> Any:
    if isinstance(node, list):
        return [_to_strict_object(item) for item in node]
    if not isinstance(node, dict):
        return node

    converted = {key: _to_strict_object(value) for key, value in node.items()}
    if converted.get("type") == "object" or "properties" in converted:
        properties = converted.get("properties") or {}
        converted["properties"] = properties
        converted["required"] = list(properties.keys())
        converted["additionalProperties"] = False
    return converted


def to_openai_strict(model_class: Type[BaseModel]) -> Dict[str, Any]:
    schema = _to_strict_object(_inline_refs(_json_schema_for(model_class), _json_schema_for(model_class)))
    schema.pop("$defs", None)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_class.__name__,
            "schema": schema,
            "strict": True,
        },
    }


def _to_gemini_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_to_gemini_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    converted: Dict[str, Any] = {}
    for key, value in node.items():
        if key in {"additionalProperties", "$defs", "title", "default"}:
            continue
        if key == "anyOf":
            branches = value if isinstance(value, list) else []
            non_null = [branch for branch in branches if branch.get("type") != "null"]
            if len(non_null) == 1:
                converted.update(_to_gemini_schema(non_null[0]))
                converted["nullable"] = True
            else:
                converted[key] = _to_gemini_schema(value)
            continue
        converted[key] = _to_gemini_schema(value)
    return converted


def to_gemini_schema(model_class: Type[BaseModel]) -> Dict[str, Any]:
    strict_schema = to_openai_strict(model_class)["json_schema"]["schema"]
    return _to_gemini_schema(strict_schema)


def schema_payload(model_class: Type[BaseModel]) -> Dict[str, Any]:
    return {
        "name": model_class.__name__,
        "schema": _to_strict_object(_inline_refs(_json_schema_for(model_class), _json_schema_for(model_class))),
        "model_class": model_class,
    }

