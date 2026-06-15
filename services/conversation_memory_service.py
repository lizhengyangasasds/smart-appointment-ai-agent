"""
会话记忆服务 — 工作记忆 + 情景记忆 + 上下文窗口管理

负责：
1. 对话消息的持久化存储（工作记忆）
2. 基于 token 计数的上下文窗口管理
3. 自动压缩：当上下文快满时，用 LLM 生成摘要压缩旧消息
4. 向 Agent 提供"最近 N 条消息"或"摘要 + 最近 M 条"的上下文格式
"""

import re
import math
from typing import List, Optional, Tuple, AsyncGenerator
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from db.repositories.memory_repository import MemoryRepository


class TokenCounter:
    """
    轻量级 token 计数器

    策略：
    - 中文按字符数 / 2 估算（1 中文字 ≈ 2 tokens）
    - 英文按空格分隔的 word 数 * 1.3 估算
    - 混合文本分段估算后求和
    - 结果偏保守，留有一定 buffer
    """

    CHINESE_RATIO = 0.5   # 1 中文字 ≈ 0.5 tokens (实测大多数模型 1 char ≈ 1 token)
    ENGLISH_WORD_TOKENS = 1.3  # 1 个英文 word ≈ 1.3 tokens
    RESERVE_RATIO = 0.85  # 实际可用 token 空间打个折扣

    @classmethod
    def estimate(cls, text: str) -> int:
        if not text:
            return 0
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        non_chinese = len(text) - chinese_chars
        english_words = len(re.findall(r'[a-zA-Z]+', text))
        tokens = chinese_chars + int(english_words * cls.ENGLISH_WORD_TOKENS) + int(non_chinese * cls.RESERVE_RATIO)
        return max(1, tokens)

    @classmethod
    def estimate_messages(cls, messages: List[BaseMessage]) -> int:
        return sum(cls.estimate(m.content) for m in messages)

    @classmethod
    def estimate_messages_dict(cls, messages: List[dict]) -> int:
        return sum(cls.estimate(m.get("content", "")) for m in messages)


class ConversationMemoryService:
    """
    会话记忆服务

    核心职责：
    1. 持久化每轮对话消息（写入 DB）
    2. 管理上下文窗口：按 token 数滑动窗口
    3. 压缩：当上下文超出阈值时，调用 LLM 生成摘要，保留摘要 + 最近消息
    4. 向 Agent 提供格式化后的上下文字符串

    上下文窗口大小配置（单位：tokens）：
    - max_context_tokens: 上下文总上限（默认 6000，约 3000 中文）
    - summary_threshold_tokens: 触发压缩的阈值（默认 4800）
    - preserve_after_summary: 摘要后保留的最新 N 条消息 token 数（默认 1200）
    """

    DEFAULT_MAX_CONTEXT = 6000
    DEFAULT_SUMMARY_THRESHOLD = 4800
    DEFAULT_PRESERVE_AFTER_SUMMARY = 1200

    def __init__(
        self,
        memory_repo: MemoryRepository,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT,
        summary_threshold_tokens: int = DEFAULT_SUMMARY_THRESHOLD,
        preserve_after_summary: int = DEFAULT_PRESERVE_AFTER_SUMMARY,
    ):
        self.repo = memory_repo
        self.max_context_tokens = max_context_tokens
        self.summary_threshold_tokens = summary_threshold_tokens
        self.preserve_after_summary = preserve_after_summary

    # ======================================
    # 消息写入
    # ======================================

    def add_user_message(
        self,
        session_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_tag: Optional[str] = None,
        message_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """写入一条用户消息，返回 turn_index"""
        turn_index = self.repo.get_latest_turn_index(session_id) + 1
        token_count = TokenCounter.estimate(content)
        self.repo.add_message(
            session_id=session_id,
            user_id=user_id,
            role='user',
            content=content,
            agent_tag=agent_tag,
            turn_index=turn_index,
            message_type=message_type,
            token_count=token_count,
            metadata=metadata,
        )
        return turn_index

    def add_assistant_message(
        self,
        session_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_tag: Optional[str] = None,
        message_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """写入一条助手消息，返回 turn_index"""
        turn_index = self.repo.get_latest_turn_index(session_id) + 1
        token_count = TokenCounter.estimate(content)
        self.repo.add_message(
            session_id=session_id,
            user_id=user_id,
            role='assistant',
            content=content,
            agent_tag=agent_tag,
            turn_index=turn_index,
            message_type=message_type,
            token_count=token_count,
            metadata=metadata,
        )
        return turn_index

    # ======================================
    # 上下文构建
    # ======================================

    def build_context(
        self,
        session_id: str,
        max_turns: Optional[int] = None,
        include_summary: bool = True,
    ) -> Tuple[str, int]:
        """
        构建用于 LLM prompt 的上下文字符串

        返回：(context_str, total_tokens)
        策略：
        1. 如果有摘要且 include_summary=True，拼接摘要
        2. 从最新消息往前取，直到 token 数达到上限
        3. 如果超出了 summary_threshold，触发压缩逻辑

        context_str 格式：
        [会话摘要]
        用户：...
        助手：...
        用户：...
        ...
        """
        total_tokens = 0
        parts = []

        if include_summary:
            latest_summary = self.repo.get_latest_summary(session_id)
            if latest_summary:
                summary_part = f"【会话摘要（早期对话）】：\n{latest_summary.summary_text}\n"
                parts.append(summary_part)
                total_tokens += TokenCounter.estimate(summary_part)

        messages = self.repo.get_uncompressed_messages(session_id)
        if not messages:
            return "", 0

        context_parts = []
        current_tokens = 0

        for msg in reversed(messages):
            msg_token = TokenCounter.estimate(msg.content)
            if current_tokens + msg_token + total_tokens > self.max_context_tokens:
                break
            current_tokens += msg_token
            prefix = "用户" if msg.role == "user" else "助手"
            context_parts.append(f"{prefix}：{msg.content}")

        context_parts.reverse()
        if context_parts:
            parts.append("【最近对话】：\n" + "\n".join(context_parts))

        full_context = "\n".join(parts)
        return full_context, total_tokens + current_tokens

    def get_context_status(self, session_id: str) -> dict:
        """获取当前上下文状态（用于调试和监控）"""
        messages = self.repo.get_uncompressed_messages(session_id)
        total_tokens = sum(TokenCounter.estimate(m.content) for m in messages)
        summary = self.repo.get_latest_summary(session_id)
        message_count = self.repo.get_message_count(session_id, include_compressed=False)

        return {
            "session_id": session_id,
            "uncompressed_message_count": message_count,
            "estimated_tokens": total_tokens,
            "max_tokens": self.max_context_tokens,
            "usage_ratio": round(total_tokens / self.max_context_tokens, 3) if self.max_context_tokens else 0,
            "needs_compression": total_tokens >= self.summary_threshold_tokens,
            "has_summary": summary is not None,
            "summary_coverage": f"turn {summary.summary_turn_start}-{summary.summary_turn_end}" if summary else None,
        }

    # ======================================
    # 压缩逻辑
    # ======================================

    def should_compress(self, session_id: str) -> bool:
        """判断当前上下文是否需要压缩"""
        messages = self.repo.get_uncompressed_messages(session_id)
        total_tokens = sum(TokenCounter.estimate(m.content) for m in messages)
        return total_tokens >= self.summary_threshold_tokens

    def compress(
        self,
        session_id: str,
        summary_llm,
        system_prompt: str = "",
    ) -> str:
        """
        执行对话压缩

        流程：
        1. 收集所有未压缩的消息
        2. 调用 LLM 生成一段压缩摘要
        3. 保存摘要到 DB
        4. 标记旧消息为已压缩
        5. 返回压缩后的摘要文本
        """
        messages = self.repo.get_uncompressed_messages(session_id)
        if not messages:
            return ""

        turn_start = messages[0].turn_index
        turn_end = messages[-1].turn_index

        message_texts = []
        for msg in messages:
            prefix = "用户" if msg.role == "user" else "助手"
            message_texts.append(f"{prefix}：{msg.content}")

        history_str = "\n".join(message_texts)
        total_tokens = TokenCounter.estimate(history_str)

        prompt = (
            f"{system_prompt}\n\n"
            f"以下是一段对话记录，请用3-5句话总结其核心内容：\n"
            f"1. 用户的主要需求/问题是什么？\n"
            f"2. 机器人给出了什么信息或处理结果？\n"
            f"3. 对话是否有明确的结论或未完成事项？\n\n"
            f"【对话记录】（共约 {total_tokens} tokens）：\n"
            f"{history_str}\n\n"
            f"摘要："
        )

        try:
            response = summary_llm.invoke([{"role": "user", "content": prompt}])
            summary_text = response.content.strip()
        except Exception as e:
            summary_text = f"[摘要生成失败：{str(e)}]"
            print(f"[Memory] 摘要生成失败: {e}")

        summary_tokens = TokenCounter.estimate(summary_text)
        self.repo.add_summary(
            session_id=session_id,
            summary_text=summary_text,
            summary_turn_start=turn_start,
            summary_turn_end=turn_end,
            token_count=summary_tokens,
        )
        self.repo.mark_messages_compressed(session_id, turn_start, turn_end)

        print(f"[Memory] 压缩完成: session={session_id}, turns={turn_start}-{turn_end}, "
              f"原tokens≈{total_tokens}, 摘要tokens≈{summary_tokens}")

        return summary_text

    # ======================================
    # 会话管理
    # ======================================

    def reset_session(self, session_id: str) -> dict:
        """重置指定 session 的所有记忆"""
        return self.repo.clear_session(session_id)

    def get_turn_index(self, session_id: str) -> int:
        """获取当前 turn_index（下一条消息应该用哪个序号）"""
        return self.repo.get_latest_turn_index(session_id) + 1
