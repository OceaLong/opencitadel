"""Optional in-memory storage and mandatory production upload-intent boundary."""


async def put_execution_object(storage, key, body, *, run_id, scope_key=None):
    owned_write = getattr(storage, "put_execution_bytes", None)
    if owned_write is None:
        await storage.put_bytes(key, body)
    else:
        await owned_write(key, body, run_id=run_id, scope_key=scope_key)


def object_scope(scope):
    return f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
