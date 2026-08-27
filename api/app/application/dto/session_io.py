from pydantic import BaseModel, Field


class FileReadResult(BaseModel):
    filepath: str
    content: str


class ConsoleRecordResult(BaseModel):
    ps1: str
    command: str
    output: str


class ShellReadResult(BaseModel):
    session_id: str
    output: str
    console_records: list[ConsoleRecordResult] = Field(default_factory=list)
