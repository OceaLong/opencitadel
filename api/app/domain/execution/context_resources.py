"""Private, producer-attested resource versions behind a formal context result."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.execution.context_models import context_digest
from app.domain.execution.definition import DefinitionValue, Digest
from app.domain.models.knowledge_citation import KnowledgeCitation


def memory_content_digest(entry):
    # Usage counters and ranking scores are mutable operational observations,
    # not revisions of the text or its authority.
    return context_digest(
        entry.model_dump(
            mode="json",
            include={
                "id",
                "scope",
                "session_id",
                "title",
                "content",
                "tags",
                "owner_user_id",
                "team_id",
                "source",
                "embedding_space_digest",
            },
        )
    )


class ContextMemoryVersion(DefinitionValue):
    entry_id: str = Field(min_length=1, max_length=255)
    content_digest: Digest
    embedding_space_digest: Digest | None = None

    @classmethod
    def from_entry(cls, entry):
        return cls(
            entry_id=entry.id,
            content_digest=memory_content_digest(entry),
            embedding_space_digest=entry.embedding_space_digest,
        )


class ContextMemoryEvidence(DefinitionValue):
    format_version: Literal[1] = 1
    query_digest: Digest
    query_embedding_space_digest: Digest | None = None
    entries: tuple[ContextMemoryVersion, ...] = Field(max_length=100)


class ContextMemoryRecall(ContextMemoryEvidence):
    content: str = Field(max_length=1048576)

    def evidence(self):
        return ContextMemoryEvidence.model_validate(self.model_dump(exclude={"content"}))


class ContextResourceEvidence(DefinitionValue):
    format_version: Literal[1] = 1
    memory: ContextMemoryEvidence | None = None
    knowledge: tuple["ContextKnowledgeVersion", ...] = Field(default=(), max_length=100)
    unresolved_sources: bool = False


class ContextKnowledgeVersion(DefinitionValue):
    knowledge_base_id: str = Field(min_length=1, max_length=255)
    version_id: str = Field(min_length=1, max_length=255)
    embedding_space_digest: Digest | None = None
    citations: tuple[KnowledgeCitation, ...] = Field(default=(), max_length=100)


class ContextSkillEvidence(DefinitionValue):
    skill_id: str = Field(min_length=1, max_length=255)
    content_digest: Digest
    rendered: str = Field(max_length=262144)


def skill_content_digest(skill):
    # Include tool restrictions and ownership as well as rendered instructions.
    return context_digest(skill.model_dump(mode="json", exclude={"created_at", "updated_at"}))


class ContextFileEvidence(DefinitionValue):
    file_id: str = Field(min_length=1, max_length=255)
    content_digest: Digest
    object_identity: UUID
    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=255)
    size: int = Field(ge=0, le=8388608)
    sandbox_path: str = Field(min_length=1, max_length=1024)
    representation: Literal["text", "image", "manifest"]
    object_ref: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def representation_reference(self):
        if (self.representation == "manifest") != (self.object_ref is None):
            raise ValueError("CONTEXT_ATTACHMENT_REFERENCE_INVALID")
        return self
