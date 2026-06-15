"""
统一记忆管理器 — 整合工作记忆 + 语义记忆

对外提供统一的记忆接口，封装 ConversationMemoryService 和 SemanticMemoryService。
负责：
1. 管理单一 session 的所有记忆读写
2. 在每轮对话后自动触发语义记忆提取
3. 在上下文快满时自动触发压缩
4. 向 Agent 提供统一的上下文格式化接口
"""

from typing import Optional, Tuple
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage

from db.repositories.memory_repository import MemoryRepository
from services.conversation_memory_service import ConversationMemoryService
from services.semantic_memory_service import SemanticMemoryService


class MemoryManager:
    """
    统一记忆管理器

    使用方式：
    ```python
    memory_manager = MemoryManager(session_id="user-123", memory_repo=repo)

    # 每次用户输入时
    context = memory_manager.prepare_context_for_agent()

    # 存储用户消息
    memory_manager.add_user_message(user_input)

    # 存储助手消息
    memory_manager.add_assistant_message(response_text, agent_tag="[预约机器人]")

    # 检查并触发压缩
    if memory_manager.needs_compression():
        memory_manager.compress(summary_llm)
    ```
    """

    DEFAULT_SYSTEM_PROMPT = (
        "你是一个对话摘要助手。请简洁地用3-5句话总结以下对话的核心内容，"
        "包括用户的主要需求、机器人的回应以及是否有待办事项。"
    )

    def __init__(
        self,
        session_id: str,
        memory_repo: MemoryRepository,
        user_id: Optional[str] = None,
        max_context_tokens: int = 6000,
        summary_threshold_tokens: int = 4800,
        preserve_after_summary: int = 1200,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.repo = memory_repo

        self.conversation = ConversationMemoryService(
            memory_repo=memory_repo,
            max_context_tokens=max_context_tokens,
            summary_threshold_tokens=summary_threshold_tokens,
            preserve_after_summary=preserve_after_summary,
        )
        self.semantic = SemanticMemoryService(memory_repo=memory_repo)

        self._user_message_turn: Optional[int] = None

    # ======================================
    # 对话消息管理
    # ======================================

    def add_user_message(
        self,
        content: str,
        agent_tag: Optional[str] = None,
        message_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """存储用户消息并自动提取语义记忆"""
        turn_index = self.conversation.add_user_message(
            session_id=self.session_id,
            content=content,
            user_id=self.user_id,
            agent_tag=agent_tag,
            message_type=message_type,
            metadata=metadata,
        )
        self._user_message_turn = turn_index

        self.semantic.extract_and_store(
            text=content,
            session_id=self.session_id,
            user_id=self.user_id,
            turn_index=turn_index,
        )
        return turn_index

    def add_assistant_message(
        self,
        content: str,
        agent_tag: Optional[str] = None,
        message_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """存储助手消息"""
        return self.conversation.add_assistant_message(
            session_id=self.session_id,
            content=content,
            user_id=self.user_id,
            agent_tag=agent_tag,
            message_type=message_type,
            metadata=metadata,
        )

    # ======================================
    # 上下文获取
    # ======================================

    def get_conversation_context(
        self,
        max_turns: Optional[int] = None,
        include_summary: bool = True,
    ) -> str:
        """获取对话上下文（用于 TaskClassifier）"""
        context, _ = self.conversation.build_context(
            session_id=self.session_id,
            max_turns=max_turns,
            include_summary=include_summary,
        )
        return context

    def get_full_context(
        self,
        user_profile: bool = True,
        include_summary: bool = True,
    ) -> str:
        """
        获取完整上下文（对话 + 用户画像）

        返回格式化的字符串，供各 Agent 的 prompt 使用。
        """
        parts = []
        context, tokens = self.conversation.build_context(
            session_id=self.session_id,
            include_summary=include_summary,
        )
        if context:
            parts.append(context)

        if user_profile:
            profile = self.semantic.get_user_profile(
                session_id=self.session_id,
                user_id=self.user_id,
            )
            if profile:
                parts.append(profile)

        return "\n\n".join(parts)

    def get_recommendation_context(self) -> str:
        """获取推荐相关上下文（专供推荐系统）"""
        return self.semantic.get_recommendation_context(
            session_id=self.session_id,
            user_id=self.user_id,
        )

    def get_appointment_history_context(self) -> str:
        """获取预约历史上下文（用于 AppointmentAgent 的 InputParser）"""
        history, _ = self.conversation.build_context(
            session_id=self.session_id,
            include_summary=True,
        )
        return history

    # ======================================
    # 压缩管理
    # ======================================

    def needs_compression(self) -> bool:
        """检查是否需要压缩"""
        return self.conversation.should_compress(self.session_id)

    def get_context_status(self) -> dict:
        """获取上下文状态（调试用）"""
        return self.conversation.get_context_status(self.session_id)

    def compress(self, summary_llm: BaseChatModel, system_prompt: str = "") -> str:
        """
        执行对话压缩

        调用 LLM 生成摘要，标记旧消息为已压缩，保存摘要。
        """
        system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        return self.conversation.compress(
            session_id=self.session_id,
            summary_llm=summary_llm,
            system_prompt=system_prompt,
        )

    # ======================================
    # 语义记忆管理
    # ======================================

    def store_preference(
        self,
        key: str,
        value: str,
        confidence_delta: int = 1,
    ) -> None:
        """手动存储用户偏好"""
        self.semantic.store_preference(
            session_id=self.session_id,
            key=key,
            value=value,
            user_id=self.user_id,
            confidence_delta=confidence_delta,
            source_turn=self._user_message_turn,
        )

    def store_fact(self, key: str, value: str) -> None:
        """手动存储关键事实"""
        self.semantic.store_fact(
            session_id=self.session_id,
            key=key,
            value=value,
            user_id=self.user_id,
            source_turn=self._user_message_turn,
        )

    def store_pattern(self, key: str, value: str) -> None:
        """手动存储行为模式"""
        self.semantic.store_pattern(
            session_id=self.session_id,
            key=key,
            value=value,
            user_id=self.user_id,
            source_turn=self._user_message_turn,
        )

    def boost_preference(self, key: str) -> None:
        """确认偏好时增加置信度"""
        self.semantic.boost_confidence(self.session_id, key)

    def get_preferences(self) -> dict:
        """获取用户偏好字典"""
        return self.semantic.get_preferences(
            session_id=self.session_id,
            user_id=self.user_id,
        )

    # ======================================
    # 会话管理
    # ======================================

    def reset(self) -> dict:
        """重置该 session 的所有记忆"""
        conv_result = self.conversation.reset_session(self.session_id)
        sem_result = self.semantic.reset_session_memory(self.session_id)
        return {**conv_result, **sem_result}

    def get_turn_index(self) -> int:
        """获取当前轮次"""
        return self.conversation.get_turn_index(self.session_id)
