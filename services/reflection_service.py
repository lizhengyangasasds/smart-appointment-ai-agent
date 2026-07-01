"""
反思服务 — 全局单例封装

封装 ReflectionAgent，提供：
1. 全局单例，避免每个请求重复创建 LLM 实例
2. 懒加载，首次访问时才初始化
3. 同步/异步双接口，对接 chat_handler（同步）和 app.py（异步定时任务）
4. 自动忽略初始化失败的仓库（repository 未建表时不影响主流程）
"""

import threading
import asyncio
from typing import Dict, Any, Optional


class ReflectionService:
    """
    反思服务全局单例

    用法：
        from services.reflection_service import get_reflection_service
        svc = get_reflection_service()

        # 异步反思（主流程对话后调用）
        result = await svc.reflect_on_appointment(session_id, appointment_history, turns_count, completion_time)

        # 同步反思（定时任务调用）
        result = svc.reflect_on_appointment_sync(...)

        # 触发完整闭环周期
        loop_result = svc.run_closed_loop_cycle()
    """

    _instance: Optional['ReflectionService'] = None
    _lock = threading.Lock()

    def __init__(self):
        self._reflection_agent = None
        self._init_error: Optional[Exception] = None
        self._initialized = False

    def _ensure_initialized(self):
        """延迟初始化 ReflectionAgent（线程安全）"""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                from agents.reflection_agent import ReflectionAgent
                self._reflection_agent = ReflectionAgent()
                self._initialized = True
            except Exception as e:
                self._init_error = e
                self._initialized = True

    @property
    def agent(self):
        """懒加载获取 ReflectionAgent 实例"""
        self._ensure_initialized()
        return self._reflection_agent

    @property
    def is_available(self) -> bool:
        """检查反思服务是否可用"""
        self._ensure_initialized()
        return self._reflection_agent is not None

    # ==================== 异步接口（供 chat_handler 调用） ====================

    async def reflect_on_appointment(
        self,
        session_id: str,
        appointment_history: Dict[str, Any],
        turns_count: int,
        completion_time: Optional[float] = None,
        error: Optional[Exception] = None,
    ) -> Dict[str, Any]:
        """
        反思预约任务（异步）

        Args:
            session_id: 会话ID
            appointment_history: 预约历史（包含 gender/start_time/duration/project 等字段）
            turns_count: 对话轮数
            completion_time: 完成耗时（秒）
            error: 异常信息

        Returns:
            反思结果字典 {evaluation, reflection, report}
        """
        if not self.is_available:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": "reflection service unavailable"}

        try:
            result = await self.agent.reflect_on_appointment(
                session_id=session_id,
                appointment_history=appointment_history,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error,
            )
            return result
        except Exception as e:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": str(e)}

    async def reflect_on_consultation(
        self,
        session_id: str,
        consultation_data: Dict[str, Any],
        turns_count: int,
        completion_time: Optional[float] = None,
        error: Optional[Exception] = None,
    ) -> Dict[str, Any]:
        """
        反思咨询任务（异步）

        Args:
            session_id: 会话ID
            consultation_data: 咨询数据（包含 has_answer/answer_quality/knowledge_hit 等字段）
            turns_count: 对话轮数
            completion_time: 完成耗时（秒）
            error: 异常信息

        Returns:
            反思结果字典
        """
        if not self.is_available:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": "reflection service unavailable"}

        try:
            result = await self.agent.reflect_on_consultation(
                session_id=session_id,
                consultation_data=consultation_data,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error,
            )
            return result
        except Exception as e:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": str(e)}

    # ==================== 同步接口（供定时任务调用） ====================

    def reflect_on_appointment_sync(
        self,
        session_id: str,
        appointment_history: Dict[str, Any],
        turns_count: int,
        completion_time: Optional[float] = None,
        error: Optional[Exception] = None,
    ) -> Dict[str, Any]:
        """reflect_on_appointment 的同步包装（供后台线程调用）"""
        if not self.is_available:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": "reflection service unavailable"}

        try:
            return asyncio.run(
                self.agent.reflect_on_appointment(
                    session_id=session_id,
                    appointment_history=appointment_history,
                    turns_count=turns_count,
                    completion_time=completion_time,
                    error=error,
                )
            )
        except Exception as e:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": str(e)}

    def reflect_on_consultation_sync(
        self,
        session_id: str,
        consultation_data: Dict[str, Any],
        turns_count: int,
        completion_time: Optional[float] = None,
        error: Optional[Exception] = None,
    ) -> Dict[str, Any]:
        """reflect_on_consultation 的同步包装（供后台线程调用）"""
        if not self.is_available:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": "reflection service unavailable"}

        try:
            return asyncio.run(
                self.agent.reflect_on_consultation(
                    session_id=session_id,
                    consultation_data=consultation_data,
                    turns_count=turns_count,
                    completion_time=completion_time,
                    error=error,
                )
            )
        except Exception as e:
            return {"evaluation": {}, "reflection": None, "report": {}, "error": str(e)}

    # ==================== 闭环核心 ====================

    def run_closed_loop_cycle(self, task_type: str = None) -> Dict[str, Any]:
        """
        运行完整的闭环周期（同步，供定时任务调用）

        包括：
        1. 获取反思洞察
        2. 生成策略更新
        3. 评估策略效果
        4. 自动调整（回滚/保持）

        Args:
            task_type: 任务类型（可选，不指定则评估所有类型）

        Returns:
            闭环周期结果
        """
        if not self.is_available:
            return {"error": "reflection service unavailable"}

        try:
            return self.agent.engine.run_closed_loop_cycle(task_type=task_type)
        except Exception as e:
            return {"error": str(e)}

    def get_insights(self, days: int = 7) -> Dict[str, Any]:
        """获取反思洞察"""
        if not self.is_available:
            return {}
        try:
            return self.agent.get_insights(days=days)
        except Exception:
            return {}

    def get_statistics(self, days: int = 30) -> Dict[str, Any]:
        """获取评估统计"""
        if not self.is_available:
            return {}
        try:
            return self.agent.get_statistics(days=days)
        except Exception:
            return {}

    def record_feedback(
        self,
        session_id: str,
        feedback_type: str,
        rating: Optional[int] = None,
        content: Optional[str] = None,
    ) -> Optional[int]:
        """记录用户反馈"""
        if not self.is_available:
            return None
        try:
            return self.agent.record_explicit_feedback(
                session_id=session_id,
                feedback_type=feedback_type,
                rating=rating,
                content=content,
            )
        except Exception:
            return None


# ==================== 全局单例访问函数 ====================

_instance: Optional[ReflectionService] = None
_instance_lock = threading.Lock()


def get_reflection_service() -> ReflectionService:
    """获取反思服务全局单例（线程安全，懒加载）"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ReflectionService()
    return _instance
