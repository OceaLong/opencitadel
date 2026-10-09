"""Current dependency authority shared by copied-content reads and retention pins."""


async def content_source_available(scope, citation, *, file_repository, knowledge_repository):
    if citation.get("availability") != "available":
        return False
    if citation.get("resource_kind") == "file":
        file = await file_repository.get_by_id(citation.get("file_id"), scope=scope)
        return bool(
            file
            and file.content_available
            and file.content_digest
            and file.content_digest == citation.get("content_digest")
            and file.object_identity == citation.get("object_identity")
        )
    kb, version, doc, revision = (
        citation.get(k)
        for k in ("knowledge_base_id", "version_id", "doc_id", "document_revision_id")
    )
    if not all((kb, version, doc, revision)):
        return False
    if await knowledge_repository.get_kb(kb, scope=scope) is None:
        return False
    resolved = await knowledge_repository.get_document_for_version(kb, version, doc)
    return resolved is not None and resolved[1] == revision
