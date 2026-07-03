#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Agent 内部提示词（记忆、截断、修复提示）。"""

MEMORY_SUMMARY_PROMPT = """请将以下 Agent 对话历史压缩为简洁摘要，保留：
- 已完成的关键操作与结论
- 重要文件路径、数据、错误信息
- 用户目标与当前进度

只输出摘要正文，不要 JSON。使用与历史相同的语言。

历史消息:
{history}
"""

TRUNCATION_NOTICE = (
    "\n\n{hint}[结果已截断: 原始长度 {original_len} 字符，保留前 {max_chars} 字符。"
    "如需完整内容请缩小查询范围或使用 read_file 等工具分页获取。]"
)

OFFLOAD_NOTICE = (
    "完整结果已保存到 {cache_path}（{original_len} 字符）。"
    "摘要预览如下，如需完整内容请用 read_file 读取该路径。"
)

STRUCTURED_REPAIR_HINT = (
    "上次输出不符合结构化 schema，请修正后只返回 JSON。\n校验错误:\n{errors}"
)

SUBAGENT_SYSTEM_PROMPT = """
你是一个专注的子任务执行 Agent。你会收到一个自包含的子目标，请独立完成它并返回简洁的结果摘要。

要求：
- 只关注当前子目标，不要扩展范围。
- 使用可用工具直接执行，不要向用户提问（除非子目标本身需要外部信息且无法推断）。
- 完成后用自然语言总结关键结论、文件路径与错误（如有）。
- 不要返回 JSON，直接输出可读的摘要正文。
"""
