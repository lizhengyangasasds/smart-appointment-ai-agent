"""
聊天处理器 v2 — 集成记忆系统的统一入口

改进点：
1. per-session 隔离：每个 session_id 独立的 MemoryManager
2. 记忆注入：将对话上下文注入到 TaskClassifier、ConsultantAgent、AppointmentAgent
3. 语义提取：每轮对话后自动提取用户偏好并存储
4. 自动压缩：当上下文超过阈值时触发 LLM 摘要压缩
5. 兼容旧接口：session_id 为空时使用全局兼容模式
6. 反思闭环：对话完成后自动触发反思，注入反思引擎到各 Agent
"""

import logging
import uuid
import re
import asyncio
from typing import Optional, Dict, AsyncGenerator
from datetime import datetime
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
from services.reflection_service import get_reflection_service
import logging

logger = logging.getLogger(__name__)


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


# 全局反思服务（懒加载，进程级单例）
_reflection_svc = None


def _get_reflection_service():
    global _reflection_svc
    if _reflection_svc is None:
        _reflection_svc = get_reflection_service()
    return _reflection_svc


class _SessionMeta:
    """
    记录每个 session 的任务元数据，供反思使用
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.task_type: Optional[str] = None          # appointment / consultation / other
        self.turn_count: int = 0                       # 该任务的对话轮数
        self.start_time: Optional[datetime] = None     # 任务开始时间
        self.completion_time: Optional[float] = None    # 完成耗时（秒）
        self.appointment_history: Optional[Dict] = None  # 预约历史（预约任务）
        self.consultation_data: Optional[Dict] = None   # 咨询数据（咨询任务）
        self.reflected: bool = False                    # 本轮是否已触发反思

    def start_task(self, task_type: str):
        self.task_type = task_type
        self.turn_count = 1
        self.start_time = datetime.now()
        self.reflected = False

    def add_turn(self):
        self.turn_count += 1

    def complete_task(self, appointment_history: Dict = None, consultation_data: Dict = None):
        self.completion_time = (datetime.now() - self.start_time).total_seconds() if self.start_time else None
        self.appointment_history = appointment_history
        self.consultation_data = consultation_data


class _MemoryAwareChatSession:
    """
    内存感知的聊天会话

    每个 session_id 对应一个实例，包含：
    - 独立的 MemoryManager（工作记忆 + 语义记忆）
    - 独立的 TaskClassificationAgent（避免跨 session 污染）
    - 独立的反思元数据管理器
    - 反思引擎注入（从全局单例获取）
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

        # 获取反思服务（全局单例）
        reflection_svc = _get_reflection_service()

        # 初始化预约 Agent（注入反思引擎和语义记忆服务）
        # 兜底：appointment → consultant 让步时，由 consultant_fallback_callback
        # 直接调用 consultant_agent.consult_stream；具体闭包在两者初始化完成后注入。
        def _consultant_fallback_factory():
            async def _cb(user_input: str, memory_context: str = ""):
                agent = self.__dict__.get("_consultant_agent") if hasattr(self, "__dict__") else None
                if agent is None:
                    return "（咨询机器人尚未就绪）"
                gen = agent.consult_stream(user_input, memory_context)
                tokens = []
                async for token in gen:
                    tokens.append(token)
                return "".join(tokens) if tokens else ""
            return _cb
        self._consultant_fallback_callback = _consultant_fallback_factory()

        self._appointment_agent = AppointmentAgent(
            session_id=session_id,
            unrelated_callback=None,
            reflection_engine=reflection_svc.agent.engine if reflection_svc.is_available else None,
            semantic_memory=self.memory_manager.semantic,
            consultant_fallback_callback=self._consultant_fallback_callback,
        )
        self._appointment_agent.set_shared_state(self._shared_state)

        # 初始化咨询 Agent（注入反思引擎）
        self._consultant_agent = ConsultantAgent(
            session_id=session_id,
            reflection_engine=reflection_svc.agent.engine if reflection_svc.is_available else None,
        )
        self._consultant_agent.set_shared_state(self._shared_state)

        # 初始化任务分类 Agent（传入同一个 _shared_state，确保状态同步，Q1 修复）
        self._task_agent = TaskClassificationAgent(
            appointment_agent=self._appointment_agent,
            consultant_agent=self._consultant_agent,
            shared_state=self._shared_state,
        )

        self._summary_llm = create_chat_model(temperature=0.3)

        # 任务元数据（供反思使用）
        self._task_meta = _SessionMeta(session_id)

        # 标记当前是否处于对话流中（用于区分单轮请求和会话恢复）
        self._in_conversation_flow = False

        # 追踪当前是预约还是咨询流程（用于反思）
        self._current_flow: Optional[str] = None  # 'appointment' / 'consultation'

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

    async def _trigger_reflection(self):
        """
        触发反思（静默执行，不阻塞用户响应）

        根据当前任务类型，从对话历史中提取元数据并调用反思服务。
        """
        reflection_svc = _get_reflection_service()
        if not reflection_svc.is_available:
            return
        if self._task_meta.reflected:
            return
        if self._task_meta.task_type is None:
            return

        meta = self._task_meta

        # 判断任务是否真正完成（预约已完成 / 咨询已完成）
        task_done = False
        if meta.task_type == 'appointment':
            # 预约完成：finished=True 且没有等待中的确认
            if self._appointment_agent.finished and not self._appointment_agent.appointment_history.get('awaiting_confirmation'):
                task_done = True
        elif meta.task_type == 'consultation':
            task_done = True

        if not task_done:
            return

        meta.reflected = True

        try:
            if meta.task_type == 'appointment':
                await reflection_svc.reflect_on_appointment(
                    session_id=meta.session_id,
                    appointment_history=meta.appointment_history or {},
                    turns_count=meta.turn_count,
                    completion_time=meta.completion_time,
                )
            elif meta.task_type == 'consultation':
                # 咨询数据取当前会话的咨询相关信息
                await reflection_svc.reflect_on_consultation(
                    session_id=meta.session_id,
                    consultation_data=meta.consultation_data or {},
                    turns_count=meta.turn_count,
                    completion_time=meta.completion_time,
                )
        except Exception as e:
            # 反思失败静默吞掉，不影响用户
            logging.warning(f"[Reflection] 反思触发失败: {e}")

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
        6. 触发反思（任务完成后）
        """
        # --- 记忆存储（压缩在后台静默进行） ---
        self.memory_manager.add_user_message(
            content=user_input,
            agent_tag=agent_tag,
            message_type=message_type,
        )

        if self.memory_manager.needs_compression():
            try:
                self.memory_manager.compress(self._summary_llm)
                logger.debug(f"[Memory] session={self.session_id} 上下文压缩完成")
            except Exception as e:
                logger.warning(f"[Memory] 压缩失败: {e}")

        # --- 对话流处理 ---
        self._in_conversation_flow = True
        collected_response = []

        # 修复：把记忆上下文（对话摘要+用户画像）传下去，
        # 让 InputParser 能看到用户偏好（如 preferred_technician）和早期对话摘要，
        # 避免每次都重新问用户已有信息。
        memory_ctx = self.memory_manager.get_full_context(
            user_profile=True, include_summary=True
        )
        async for token in self._task_agent.classify_task_stream(user_input, memory_context=memory_ctx):
            collected_response.append(token)
            yield token

        self._in_conversation_flow = False
        full_response = ''.join(collected_response)

        # --- 记录助手响应到记忆 ---
        if full_response:
            self.memory_manager.add_assistant_message(
                content=full_response,
                agent_tag=agent_tag,
                message_type=message_type,
            )

        # --- 更新任务元数据 ---
        self._update_task_meta(full_response)

        # --- 触发反思（后台异步，不阻塞响应返回） ---
        if full_response:
            asyncio.create_task(self._trigger_reflection())

    def _update_task_meta(self, response: str):
        """
        根据最新响应更新任务元数据

        通过响应内容中的 agent tag 判断当前处于哪个流程，
        并更新任务类型、轮数等信息。
        """
        if '[REPLY][预约机器人]' in response or '[THOUGHT][预约机器人]' in response:
            if self._current_flow != 'appointment':
                # 新进入预约流程
                self._task_meta.start_task('appointment')
                self._current_flow = 'appointment'
            else:
                self._task_meta.add_turn()
            # 同步预约历史
            self._task_meta.appointment_history = self._appointment_agent.appointment_history

        elif '[REPLY][咨询机器人]' in response or '[THOUGHT][咨询机器人]' in response:
            if self._current_flow != 'consultation':
                self._task_meta.start_task('consultation')
                self._current_flow = 'consultation'
            else:
                self._task_meta.add_turn()
            # 同步咨询数据
            self._task_meta.consultation_data = {
                'has_answer': True,
                'answer_quality': 0.8,
                'knowledge_hit': True,
            }

        elif '[REPLY][归类机器人]' in response or '[THOUGHT][归类机器人]' in response:
            if self._current_flow is None:
                self._task_meta.start_task('other')

    def _record_assistant_response(
        self,
        content: str,
        agent_tag: Optional[str] = None,
        message_type: Optional[str] = None,
    ) -> None:
        """记录助手响应到记忆（保留向后兼容）"""
        self.memory_manager.add_assistant_message(
            content=content,
            agent_tag=agent_tag,
            message_type=message_type,
        )

    def reset(self) -> dict:
        """重置该 session 的所有记忆和 Agent 状态"""
        self._shared_state.value = StateEnum.CLASSIFY
        self._appointment_agent.reset()
        self._task_meta = _SessionMeta(self.session_id)
        self._current_flow = None
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

    新增功能：
    - 反思引擎自动注入到 AppointmentAgent 和 ConsultantAgent
    - 对话完成后自动触发反思评估（静默，不影响响应速度）

    返回：AsyncGenerator[str, None]，yield 每个 token
    """
    context = context or {}

    session = _get_or_create_session(session_id, user_id)

    agent_tag = context.get('agent_tag')
    message_type = context.get('message_type')

    async for token in session._stream_response(
        user_input,
        agent_tag=agent_tag,
        message_type=message_type,
    ):
        yield token


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
            'task_type': session._task_meta.task_type,
            'task_turns': session._task_meta.turn_count,
            'current_flow': session._current_flow,
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
