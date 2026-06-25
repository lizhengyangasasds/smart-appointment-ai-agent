"""
反思 Agent 主控制器

职责：
1. 初始化各个组件
2. 管理反思流程
3. 协调评估、分析和报告
4. 与其他Agent集成
"""

import uuid
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

from config.model_provider import create_chat_model
from .reflection import (
    TaskEvaluator,
    ReflectionAnalyzer,
    ReflectionReporter,
    ReflectionEngine
)

load_dotenv()


class ReflectionAgent:
    """
    反思 Agent 主控制器
    
    职责：
    1. 初始化反思引擎组件
    2. 提供任务反思接口
    3. 生成反思报告
    4. 管理用户反馈
    """
    
    def __init__(self):
        # 基础设置
        self.session_id = str(uuid.uuid4())
        self.logger = logging.getLogger(__name__)
        
        # 初始化LLM
        self.llm = self._initialize_llm()
        
        # 初始化数据仓库
        self._init_repositories()
        
        # 初始化组件
        self.evaluator = TaskEvaluator(evaluation_repo=self.evaluation_repo)
        self.analyzer = ReflectionAnalyzer(
            evaluation_repo=self.evaluation_repo,
            reflection_repo=self.reflection_repo,
            llm=self.llm
        )
        self.reporter = ReflectionReporter(
            llm=self.llm,
            reflection_repo=self.reflection_repo
        )
        
        # 初始化反思引擎
        self.engine = ReflectionEngine(
            evaluator=self.evaluator,
            analyzer=self.analyzer,
            reporter=self.reporter,
            evaluation_repo=self.evaluation_repo,
            reflection_repo=self.reflection_repo,
            feedback_repo=self.feedback_repo,
            llm=self.llm
        )
        
        self.logger.info("反思 Agent 初始化完成")

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0.3)

    def _init_repositories(self):
        """初始化数据仓库"""
        try:
            from db.db_router import DatabaseRouter
            db = DatabaseRouter()
            self.evaluation_repo = db.evaluation
            self.reflection_repo = db.reflection
            self.feedback_repo = db.feedback
        except Exception as e:
            self.logger.warning(f"数据库仓库初始化失败: {e}")
            self.evaluation_repo = None
            self.reflection_repo = None
            self.feedback_repo = None

    async def reflect_on_appointment(
        self,
        session_id: str,
        appointment_history: Dict[str, Any],
        turns_count: int,
        completion_time: float = None,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        反思预约任务
        
        Args:
            session_id: 会话ID
            appointment_history: 预约历史数据
            turns_count: 对话轮数
            completion_time: 完成时间
            error: 错误信息
            
        Returns:
            反思结果
        """
        self.logger.info(f"反思预约任务: session={session_id}")
        
        result = await self.engine.reflect_on_task(
            session_id=session_id,
            task_type='appointment',
            task_result=appointment_history,
            turns_count=turns_count,
            completion_time=completion_time,
            error=error
        )
        
        # 记录用户反馈（基于结果）
        self._record_implicit_feedback(session_id, result)
        
        return result

    async def reflect_on_consultation(
        self,
        session_id: str,
        consultation_data: Dict[str, Any],
        turns_count: int,
        completion_time: float = None,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        反思咨询任务
        
        Args:
            session_id: 会话ID
            consultation_data: 咨询数据
            turns_count: 对话轮数
            completion_time: 完成时间
            error: 错误信息
            
        Returns:
            反思结果
        """
        self.logger.info(f"反思咨询任务: session={session_id}")
        
        result = await self.engine.reflect_on_task(
            session_id=session_id,
            task_type='consultation',
            task_result=consultation_data,
            turns_count=turns_count,
            completion_time=completion_time,
            error=error
        )
        
        self._record_implicit_feedback(session_id, result)
        
        return result

    def record_explicit_feedback(
        self,
        session_id: str,
        feedback_type: str,
        rating: int = None,
        content: str = None
    ) -> Optional[int]:
        """
        记录显式用户反馈
        
        Args:
            session_id: 会话ID
            feedback_type: 反馈类型 (rating/correction/complaint/praise)
            rating: 评分
            content: 反馈内容
            
        Returns:
            反馈记录ID
        """
        return self.engine.record_user_feedback(
            session_id=session_id,
            feedback_type=feedback_type,
            rating=rating,
            content=content,
            source='explicit'
        )

    def get_weekly_report(self) -> Dict[str, Any]:
        """
        获取周报
        
        Returns:
            周报数据
        """
        return self.engine.trigger_periodic_reflection(days=7)

    def get_monthly_report(self) -> Dict[str, Any]:
        """
        获取月报
        
        Returns:
            月报数据
        """
        return self.engine.trigger_periodic_reflection(days=30)

    def get_insights(self, days: int = 7) -> Dict[str, Any]:
        """
        获取反思洞察
        
        Args:
            days: 时间范围
            
        Returns:
            洞察数据
        """
        return self.engine.get_reflection_insights(days=days)

    def get_dashboard(self) -> Dict[str, Any]:
        """
        获取仪表盘数据
        
        Returns:
            仪表盘数据
        """
        return self.engine.get_dashboard_data()

    def get_statistics(self, days: int = 30) -> Dict[str, Any]:
        """
        获取评估统计
        
        Args:
            days: 时间范围
            
        Returns:
            统计数据
        """
        return self.evaluator.get_statistics(days=days)

    def _record_implicit_feedback(self, session_id: str, result: Dict[str, Any]):
        """记录隐式反馈"""
        try:
            evaluation = result.get('evaluation', {})
            success = evaluation.get('success', 0)
            
            # 基于成功率推断隐式反馈
            success_rate = evaluation.get('success_rate', 0)
            
            if success == 2:  # 完全成功
                feedback_type = 'praise'
                rating = 5
            elif success == 1:  # 部分成功
                feedback_type = 'implicit'
                rating = 4
            elif success_rate >= 0.5:
                feedback_type = 'implicit'
                rating = 3
            else:
                feedback_type = 'complaint'
                rating = 2
            
            self.engine.record_user_feedback(
                session_id=session_id,
                feedback_type=feedback_type,
                rating=rating,
                source='implicit'
            )
        except Exception as e:
            self.logger.error(f"记录隐式反馈失败: {e}")

    def update_reflection_thresholds(self, **kwargs):
        """更新反思触发阈值"""
        self.evaluator.update_thresholds(**kwargs)

    async def diagnose_issue(self, session_id: str) -> Dict[str, Any]:
        """
        诊断特定会话的问题
        
        Args:
            session_id: 会话ID
            
        Returns:
            诊断报告
        """
        # 获取该会话的评估记录
        if self.evaluation_repo:
            evaluations = self.evaluation_repo.get_recent_evaluations(
                task_type=None,
                days=30,
                limit=100
            )
            session_evals = [e for e in evaluations if e.get('session_id') == session_id]
            
            if not session_evals:
                return {"error": "未找到该会话的评估记录"}
            
            # 分析该会话的问题
            failed = [e for e in session_evals if e.get('success', 1) == 0]
            
            return {
                "session_id": session_id,
                "total_tasks": len(session_evals),
                "failed_tasks": len(failed),
                "issues": [
                    {
                        "error_type": e.get('error_type'),
                        "error_message": e.get('error_message'),
                        "turns_count": e.get('turns_count')
                    }
                    for e in failed
                ],
                "recommendations": self._generate_session_recommendations(failed)
            }
        
        return {"error": "评估仓库不可用"}


class ReflectionMixin:
    """
    反思功能混入类
    
    为其他Agent提供反思能力的混入类
    """
    
    def __init__(self):
        self._reflection_agent = None
    
    @property
    def reflection(self) -> ReflectionAgent:
        """懒加载反思Agent"""
        if self._reflection_agent is None:
            self._reflection_agent = ReflectionAgent()
        return self._reflection_agent
    
    async def reflect_after_completion(
        self,
        session_id: str,
        task_type: str,
        task_data: Dict[str, Any],
        turns_count: int,
        completion_time: float = None,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        任务完成后进行反思
        
        Args:
            session_id: 会话ID
            task_type: 任务类型
            task_data: 任务数据
            turns_count: 对话轮数
            completion_time: 完成时间
            error: 错误信息
            
        Returns:
            反思结果
        """
        if task_type == 'appointment':
            return await self.reflection.reflect_on_appointment(
                session_id=session_id,
                appointment_history=task_data,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error
            )
        elif task_type == 'consultation':
            return await self.reflection.reflect_on_consultation(
                session_id=session_id,
                consultation_data=task_data,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error
            )
        else:
            return await self.reflection.engine.reflect_on_task(
                session_id=session_id,
                task_type=task_type,
                task_result=task_data,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error
            )
    
    def record_feedback(
        self,
        session_id: str,
        feedback_type: str,
        rating: int = None,
        content: str = None
    ) -> Optional[int]:
        """记录用户反馈"""
        return self.reflection.record_explicit_feedback(
            session_id=session_id,
            feedback_type=feedback_type,
            rating=rating,
            content=content
        )
