"""
聊天处理器 v2 — 集成记忆系统的统一入口

改进点：
1. per-session 隔离：每个 session_id 独立的 MemoryManager
2. 记忆注入：将对话上下文注入到 TaskClassifier、ConsultantAgent、AppointmentAgent
3. 语义提取：每轮对话后自动提取用户偏好并存储
4. 自动压缩：当上下文超过阈值时触发 LLM 摘要压缩
5. 兼容旧接口：session_id 为空时使用全局兼容模式
"""

import uuid
import re
from typing import Optional, Dict, AsyncGenerator
from langchain_core.language_models.chat_models import BaseChatModel

from agents.task_classification_agent import TaskClassificationAgent
from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent
from config.constants import SharedState, StateEnum
from config.model_provider import create_chat_model
from db.base.session_manager import SessionManager
from db.repositories.memory_repository import MemoryRepository
from services.memory_manager import MemoryManager
from services.conversation_memory_service import TokenCounter


# ============================================
# 全局单例（按 session_id 分隔的 MemoryManager 映射）
# ============================================

_global_session_id = str(uuid.uuid4())
_chat_handlers: Dict[str, '_MemoryAwareChatSession'] = {}
_db_session_manager: Optional[SessionManager] = None


def _get_db_manager() -> SessionManager:
    global _db_session_manager
    if _db_session_manager is None:
        _db_session_manager = SessionManager()
    return _db_session_manager


class _MemoryAwareChatSession:
    """
    内存感知的聊天会话

    每个 session_id 对应一个实例，包含：
    - 独立的 MemoryManager（工作记忆 + 语义记忆）
    - 独立的 TaskClassificationAgent（避免跨 session 污染）
    - 摘要 LLM（用于压缩）
    """

    def __init__(self, session_id: str, user_id: Optional[str] = None):
        self.session_id = session_id
        self.user_id = user_id

        db = _get_db_manager()
        repo = MemoryRepository(db)
        self.memory_manager = MemoryManager(
            session_id=session_id,
            memory_repo=repo,
            user_id=user_id,
        )

        self._shared_state = SharedState()
        self._appointment_agent = AppointmentAgent(
            session_id=session_id,
            unrelated_callback=None,
        )
        self._consultant_agent = ConsultantAgent(session_id=session_id)
        self._task_agent = TaskClassificationAgent(
            appointment_agent=self._appointment_agent,
            consultant_agent=self._consultant_agent,
        )

        self._summary_llm = create_chat_model(temperature=0.3)

    def _build_classification_prompt(self, user_input: str) -> str:
        """构建带上下文的分类 prompt"""
        context = self.memory_manager.get_conversation_context(include_summary=True)
        if context:
            return (
                f"【对话历史上下文】：\n{context}\n\n"
                f"【当前用户输入】：\n{user_input}\n\n"
                f"请根据以上上下文判断用户意图，并输出分类结果。"
            )
        return user_input

    def _build_appointment_context(self) -> str:
        """构建预约上下文（对话历史 + 用户画像）"""
        return self.memory_manager.get_full_context(user_profile=True, include_summary=True)

    async def _stream_response(
        self,
        user_input: str,
        agent_tag: Optional[str] = None,
        message_type: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        执行带记忆的流式响应

        流程：
        1. 存储用户消息（自动提取语义记忆）
        2. 判断是否需要压缩
        3. 执行分类 + Agent 处理
        4. 存储助手响应
        5. 触发压缩检查
        """
        self.memory_manager.add_user_message(
            content=user_input,
            agent_tag=agent_tag,
            message_type=message_type,
        )

        if self.memory_manager.needs_compression():
            try:
                self.memory_manager.compress(self._summary_llm)
                print(f"[Memory] session={self.session_id} 上下文压缩完成")
            except Exception as e:
                print(f"[Memory] 压缩失败: {e}")

        async for token in self._task_agent.classify_task_stream(user_input):
            yield token

    def _record_assistant_response(
        self,
        content: str,
        agent_tag: Optional[str] = None,
        message_type: Optional[str] = None,
    ) -> None:
        """记录助手响应到记忆"""
        self.memory_manager.add_assistant_message(
            content=content,
            agent_tag=agent_tag,
            message_type=message_type,
        )

    def reset(self) -> dict:
        """重置该 session 的所有记忆和 Agent 状态"""
        self._shared_state.value = StateEnum.CLASSIFY
        self._appointment_agent.reset()
        return self.memory_manager.reset()

    def get_context_status(self) -> dict:
        """获取上下文状态"""
        return self.memory_manager.get_context_status()

    def get_preferences(self) -> dict:
        """获取用户偏好"""
        return self.memory_manager.get_preferences()

    def get_recommendation_context(self) -> str:
        """获取推荐上下文"""
        return self.memory_manager.get_recommendation_context()


def _get_or_create_session(session_id: Optional[str], user_id: Optional[str] = None) -> _MemoryAwareChatSession:
    """获取或创建 session"""
    if not session_id:
        session_id = _global_session_id
    if session_id not in _chat_handlers:
        _chat_handlers[session_id] = _MemoryAwareChatSession(session_id, user_id)
    return _chat_handlers[session_id]


def list_active_sessions() -> list:
    return list(_chat_handlers.keys())


def reset_session(session_id: str) -> dict:
    if session_id in _chat_handlers:
        return _chat_handlers[session_id].reset()
    return {}


# ============================================
# 兼容层：保持与原有 API 入口的接口兼容
# ============================================

async def ProcessUserInput_stream(
    user_input: str,
    state: Optional[str] = None,
    context: Optional[dict] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    带记忆的流式聊天处理（主要入口）

    新增参数：
    - session_id: 会话 ID（用于多用户隔离）
    - user_id: 用户 ID（用于偏好持久化）

    返回：AsyncGenerator[str, None]，yield 每个 token
    """
    context = context or {}

    session = _get_or_create_session(session_id, user_id)

    agent_tag = context.get('agent_tag')
    message_type = context.get('message_type')

    collected_response = []

    async for token in session._stream_response(
        user_input,
        agent_tag=agent_tag,
        message_type=message_type,
    ):
        collected_response.append(token)
        yield token

    full_response = ''.join(collected_response)
    if full_response:
        session._record_assistant_response(
            content=full_response,
            agent_tag=agent_tag,
            message_type=message_type,
        )


def get_session_status(session_id: str) -> Optional[dict]:
    """获取 session 状态（调试用）"""
    if session_id in _chat_handlers:
        session = _chat_handlers[session_id]
        return {
            'session_id': session_id,
            'user_id': session.user_id,
            'turn_index': session.memory_manager.get_turn_index(),
            'context_status': session.get_context_status(),
            'preferences': session.get_preferences(),
        }
    return None


def get_global_session_id() -> str:
    """获取全局 session_id（单用户兼容模式）"""
    return _global_session_id


def get_memory_context(session_id: str, include_profile: bool = True) -> str:
    """获取指定 session 的记忆上下文"""
    if session_id in _chat_handlers:
        return _chat_handlers[session_id].memory_manager.get_full_context(
            user_profile=include_profile,
            include_summary=True,
        )
    return ""


def get_recommendation_context(session_id: str) -> str:
    """获取指定 session 的推荐上下文"""
    if session_id in _chat_handlers:
        return _chat_handlers[session_id].get_recommendation_context()
    return ""
