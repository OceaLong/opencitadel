"""Pure publication grammar shared with the future E07 evaluator."""

from app.domain.models.resource_pin import ResourceIdentity


def validate_rule_definition(rule: dict) -> None:
    """E01 publication grammar; E07 evaluates these seven registered kinds.

    Local nonrecursive JSON Pointer references only. No URI retrieval or script
    interpreter is involved in publication. An injected validator can tighten
    this contract, never bypass it.
    """
    import re

    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    fields = {
        "text_exact": {"expected"},
        "text_normalized": {"expected"},
        "json_schema": {"schema"},
        "jsonpath": {"path", "op", "expected"},
        "required_fields": {"fields"},
        "citations": {"sources"},
        "artifact": {"artifact_kind", "schema"},
    }
    kind = rule.get("kind")
    if (
        not isinstance(kind, str)
        or kind not in fields
        or set(rule)
        - fields[kind]
        - {
            "kind",
            "id",
            "required",
            "reference_required",
        }
    ):
        raise ValueError("invalid_rule_fields")
    if "id" in rule and (not isinstance(rule["id"], str) or not rule["id"].strip()):
        raise ValueError("invalid_rule_id")
    for boolean in ("required", "reference_required"):
        if boolean in rule and type(rule[boolean]) is not bool:
            raise ValueError("invalid_rule_boolean")
    path_pattern = r"\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:0|[1-9][0-9]*)\])+"

    def path(value):
        if not isinstance(value, str) or len(value) > 1024 or not re.fullmatch(path_pattern, value):
            raise ValueError("invalid_rule_path")

    if kind in ("text_exact", "text_normalized"):
        if "expected" in rule and not isinstance(rule["expected"], str):
            raise ValueError("invalid_rule_expected")
        if not isinstance(rule.get("expected"), str) and rule.get("reference_required") is not True:
            raise ValueError("rule_expected_or_reference_required")
    elif kind == "jsonpath":
        path(rule.get("path"))
        if not isinstance(rule.get("op"), str) or rule.get("op") not in {
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "exists",
        }:
            raise ValueError("invalid_rule_operator")
        if rule["op"] != "exists" and "expected" not in rule:
            raise ValueError("rule_expected_required")
    elif kind == "required_fields":
        if not isinstance(rule.get("fields"), (list, tuple)) or not rule["fields"]:
            raise ValueError("rule_fields_required")
        for field in rule["fields"]:
            path(field)
    elif kind == "citations":
        if not isinstance(rule.get("sources", []), (list, tuple)):
            raise ValueError("invalid_citation_sources")
        for source in rule.get("sources", []):
            ResourceIdentity.model_validate(source)
    elif kind == "artifact" and (
        not isinstance(rule.get("artifact_kind"), str)
        or rule.get("artifact_kind") not in {"doc", "web"}
    ):
        raise ValueError("invalid_artifact_kind")
    if kind == "json_schema" or "schema" in rule:
        schema = rule.get("schema")
        if not isinstance(schema, (dict, bool)):
            raise ValueError("invalid_json_schema")
        visited = 0

        def walk(node, refs=(), depth=0):
            nonlocal visited
            visited += 1
            if depth > 64 or visited > 10000:
                raise ValueError("schema_complexity_exceeded")
            if isinstance(node, dict):
                if "$id" in node or "$dynamicRef" in node or "$recursiveRef" in node:
                    raise ValueError("unsupported_schema_reference")
                if (
                    "$schema" in node
                    and node["$schema"] != "https://json-schema.org/draft/2020-12/schema"
                ):
                    raise ValueError("unsupported_schema_dialect")
                ref = node.get("$ref")
                if ref is not None:
                    if not isinstance(ref, str) or not ref.startswith("#/") or ref in refs:
                        raise ValueError("unsupported_schema_reference")
                    target = schema
                    try:
                        for segment in ref[2:].split("/"):
                            target = target[segment.replace("~1", "/").replace("~0", "~")]
                    except (KeyError, TypeError):
                        raise ValueError("unresolved_schema_reference") from None
                    walk(target, (*refs, ref), depth + 1)
                for key, value in node.items():
                    if key != "$ref":
                        walk(value, refs, depth + 1)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    walk(value, refs, depth + 1)

        walk(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except (SchemaError, RecursionError, TypeError, ValueError) as error:
            raise ValueError("invalid_json_schema") from error
