"""A2A JSON-RPC correlation and positive completion evidence, without I/O."""


def response_result(response, request_id):
    if (
        not isinstance(response, dict)
        or response.get("jsonrpc") != "2.0"
        or response.get("id") != request_id
        or "error" in response
        or not isinstance(response.get("result"), dict)
    ):
        raise ValueError("a2a_response_invalid")
    return response["result"]


def _message_result(result):
    # Preserve the application's existing {message: Message} response wrapper.
    if isinstance(result, dict) and set(result) == {"message"}:
        return result["message"]
    return result


def _valid_part(part):
    if not isinstance(part, dict):
        return False
    if not isinstance(part.get("kind"), str):
        return False
    field, value_type = {
        "text": ("text", str),
        "file": ("file", dict),
        "data": ("data", dict),
    }.get(part.get("kind"), (None, type(None)))
    return field is not None and isinstance(part.get(field), value_type)


def completed_result(result, *, streaming=False):
    result = _message_result(result)
    if not isinstance(result, dict):
        return False
    kind = result.get("kind")
    if kind is not None and not isinstance(kind, str):
        return False
    if kind == "message":
        return (
            isinstance(result.get("messageId"), str)
            and bool(result["messageId"])
            and result.get("role") == "agent"
            and isinstance(result.get("parts"), list)
            and bool(result["parts"])
            and all(_valid_part(part) for part in result["parts"])
        )
    identity_field = {"task": "id", "status-update": "taskId"}.get(kind)
    if identity_field is not None and (
        not isinstance(result.get(identity_field), str) or not result[identity_field]
    ):
        return False
    if kind == "status-update" and (not streaming or result.get("final") is not True):
        return False
    if kind not in {None, "task", "status-update"}:
        return False
    status = result.get("status")
    # Legacy owned fixtures omit kind, but still return explicit Task status.
    return isinstance(status, dict) and status.get("state") == "completed"


def final_stream_result(result):
    result = _message_result(result)
    if not isinstance(result, dict):
        return False
    if result.get("kind") is not None and not isinstance(result["kind"], str):
        return False
    if result.get("kind") == "message":
        return True
    if result.get("kind") == "status-update":
        return result.get("final") is True
    status = result.get("status")
    return (
        result.get("kind") in {None, "task"}
        and isinstance(status, dict)
        and isinstance(status.get("state"), str)
        and status.get("state") in {"completed", "failed", "canceled", "rejected"}
    )


def result_task_id(result):
    result = _message_result(result)
    if not isinstance(result, dict):
        return None
    return result.get("id") if result.get("kind") == "task" else result.get("taskId")
