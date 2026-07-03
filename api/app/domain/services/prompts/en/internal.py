#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Internal agent prompts (memory, truncation, repair hints)."""

MEMORY_SUMMARY_PROMPT = """Compress the following agent conversation history into a concise summary, preserving:
- Key completed actions and conclusions
- Important file paths, data, and error messages
- User goals and current progress

Output only the summary body, no JSON. Use the same language as the history.

History:
{history}
"""

TRUNCATION_NOTICE = (
    "\n\n{hint}[Result truncated: original length {original_len} characters, "
    "showing first {max_chars} characters. "
    "Use read_file or narrow the query for full content.]"
)

OFFLOAD_NOTICE = (
    "Full result saved to {cache_path} ({original_len} characters). "
    "Preview below; use read_file on that path for the complete content."
)

STRUCTURED_REPAIR_HINT = (
    "Previous output did not match the required structured schema. "
    "Fix and return JSON only.\nValidation errors:\n{errors}"
)

SUBAGENT_SYSTEM_PROMPT = """
You are a focused sub-task execution agent. You receive a self-contained sub-goal; complete it independently and return a concise summary.

Requirements:
- Focus only on the current sub-goal; do not expand scope.
- Use available tools directly; do not ask the user unless the sub-goal requires external input that cannot be inferred.
- When done, summarize key conclusions, file paths, and errors (if any) in natural language.
- Do not return JSON; output readable summary text only.
"""
