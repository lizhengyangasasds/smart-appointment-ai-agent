"""
反思引擎 - 协调评估、分析和报告的核心组件

核心功能：
1. 管理反思流程
2. 触发反思机制
3. 协调各组件工作
4. 提供统一的反思接口
"""

from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
from enum import Enum
import logging


class ReflectionTrigger(Enum):
    """反思触发类型"""
    POST_TASK = "post_task"           # 任务后反思
    PERIODIC = "periodic"             # 周期性反思
    THRESHOLD = "threshold"           # 阈值触发
    MANUAL = "manual"                 # 手动触发
    USER_FEEDBACK = "user_feedback"  # 用户反馈触发


class ReflectionEngine:
    """反思引擎核心"""

    def __init__(
        self,
        evaluator=None,
        analyzer=None,
        reporter=None,
        evaluation_repo=None,
        reflection_repo=None,
        feedback_repo=None,
        llm=None
    ):
        self.evaluator = evaluator
        self.analyzer = analyzer
        self.reporter = reporter
        self.evaluation_repo = evaluation_repo
        self.reflection_repo = reflection_repo
        self.feedback_repo = feedback_repo
        self.llm = llm
        self.logger = logging.getLogger(__name__)

        # 反思缓存，避免重复反思
        self._reflection_cache = {}
        self._cache_ttl = 300  # 5分钟缓存

    async def reflect_on_task(
        self,
        session_id: str,
        task_type: str,
        task_result: Dict[str, Any],
        turns_count: int,
        completion_time: float = None,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        对任务执行进行反思

        Args:
            session_id: 会话ID
            task_type: 任务类型
            task_result: 任务结果数据
            turns_count: 对话轮数
            completion_time: 完成时间
            error: 错误信息

        Returns:
            反思结果
        """
        self.logger.info(f"开始反思任务: session={session_id}, type={task_type}")

        # 1. 评估任务
        if task_type == 'appointment':
            evaluation = self.evaluator.evaluate_appointment_task(
                session_id=session_id,
                appointment_history=task_result,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error
            )
        elif task_type == 'consultation':
            evaluation = self.evaluator.evaluate_consultation_task(
                session_id=session_id,
                consultation_data=task_result,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error
            )
        elif task_type == 'classification':
            evaluation = self.evaluator.evaluate_classification_task(
                session_id=session_id,
                classification_data=task_result,
                turns_count=turns_count,
                error=error
            )
        else:
            evaluation = self._generic_evaluation(
                session_id, task_type, task_result, turns_count, completion_time, error
            )

        # 2. 如果需要反思，进行分析
        reflection_result = None
        if evaluation.get('should_reflect'):
            reflection_result = await self._perform_reflection(
                session_id=session_id,
                evaluation_id=evaluation.get('evaluation_id'),
                evaluation=evaluation,
                trigger_type=ReflectionTrigger.POST_TASK
            )

        # 3. 生成报告
        report = self.reporter.generate_post_task_report(
            session_id=session_id,
            evaluation_result=evaluation,
            reflection_result=reflection_result
        )

        self.logger.info(f"任务反思完成: success={evaluation.get('success')}")

        return {
            'evaluation': evaluation,
            'reflection': reflection_result,
            'report': report
        }

    async def _perform_reflection(
        self,
        session_id: str,
        evaluation_id: int,
        evaluation: Dict[str, Any],
        trigger_type: ReflectionTrigger
    ) -> Dict[str, Any]:
        """执行反思"""
        self.logger.info(f"执行反思: trigger={trigger_type.value}")

        # 分析失败任务
        task_type = evaluation.get('task_type', 'unknown')
        failed_analysis = self.analyzer.analyze_failed_tasks(
            task_type=task_type,
            days=7
        )

        # 发现用户模式
        pattern_analysis = self.analyzer.discover_user_patterns(
            user_id="default_user",
            days=30
        )

        # 分析坏case
        bad_case_analysis = self.analyzer.analyze_bad_cases(days=30)

        # 构建反思结果
        findings = {
            'evaluation_summary': evaluation,
            'failure_analysis': failed_analysis,
            'pattern_analysis': pattern_analysis,
            'bad_case_analysis': bad_case_analysis
        }

        recommendations = failed_analysis.get('recommendations', [])
        if not recommendations:
            recommendations = pattern_analysis.get('insights', [])

        patterns = failed_analysis.get('patterns', [])
        bad_cases = bad_case_analysis.get('typical_cases', [])

        # 保存反思日志
        reflection_id = None
        if self.reflection_repo:
            reflection_id = self.reflection_repo.save_reflection(
                session_id=session_id,
                evaluation_id=evaluation_id,
                reflection_type=trigger_type.value,
                findings=findings,
                recommendations=recommendations,
                patterns_discovered=patterns,
                bad_cases=[bc.get('description', '') for bc in bad_cases]
            )

            # 更新评估记录的反思标记
            if evaluation_id and self.evaluation_repo:
                self.evaluation_repo.update_reflection_triggered(evaluation_id)

        return {
            'reflection_id': reflection_id,
            'trigger_type': trigger_type.value,
            'findings': findings,
            'recommendations': recommendations,
            'patterns': patterns,
            'bad_cases': bad_cases
        }

    def trigger_periodic_reflection(self, days: int = 7) -> Dict[str, Any]:
        """
        触发周期性反思

        Args:
            days: 周期天数

        Returns:
            周期性反思结果
        """
        self.logger.info(f"触发周期性反思: days={days}")

        # 生成周期性报告
        report = self.reporter.generate_periodic_report(days=days)

        # 如果有反思仓库，保存反思记录
        if self.reflection_repo:
            self.reflection_repo.save_reflection(
                session_id=f"periodic_{datetime.now().strftime('%Y%m%d')}",
                reflection_type=ReflectionTrigger.PERIODIC.value,
                findings=report
            )

        return report

    def record_user_feedback(
        self,
        session_id: str,
        feedback_type: str,
        rating: int = None,
        content: str = None,
        source: str = "explicit"
    ) -> Optional[int]:
        """
        记录用户反馈

        Args:
            session_id: 会话ID
            feedback_type: 反馈类型
            rating: 评分
            content: 反馈内容
            source: 来源

        Returns:
            反馈记录ID
        """
        if not self.feedback_repo:
            self.logger.warning("feedback_repo not available")
            return None

        feedback_id = self.feedback_repo.save_feedback(
            session_id=session_id,
            feedback_type=feedback_type,
            rating=rating,
            content=content,
            source=source
        )

        # 如果是负面反馈，自动触发反思
        if feedback_type in ['complaint', 'correction'] or (rating and rating <= 2):
            self.logger.info(f"检测到负面反馈，触发反思: feedback_id={feedback_id}")
            # 可以在此异步触发反思

        return feedback_id

    def get_reflection_insights(self, days: int = 7) -> Dict[str, Any]:
        """
        获取反思洞察（供其他Agent使用）

        Args:
            days: 时间范围

        Returns:
            洞察数据
        """
        if not self.reflection_repo:
            return {}

        # 获取最近的反思
        reflections = self.reflection_repo.get_recent_reflections(days=days)

        # 获取可执行的建议
        recommendations = self.reflection_repo.get_actionable_recommendations()

        # 获取坏case
        bad_cases = self.reflection_repo.get_all_bad_cases(days=days)

        return {
            'recent_insights': self._summarize_insights(reflections),
            'actionable_recommendations': recommendations[:3],
            'recent_bad_cases': bad_cases[:5],
            'summary': self._generate_insight_summary(reflections, recommendations)
        }

    def should_trigger_goal_check(self, session_id: str) -> bool:
        """
        检查是否应该触发目标检查

        Args:
            session_id: 会话ID

        Returns:
            是否应该触发
        """
        # 检查缓存
        cache_key = f"goal_check_{session_id}"
        if cache_key in self._reflection_cache:
            cached_time = self._reflection_cache[cache_key]
            if (datetime.now() - cached_time).seconds < self._cache_ttl:
                return False

        # 更新缓存
        self._reflection_cache[cache_key] = datetime.now()

        # 可以添加更多检查逻辑
        return True

    async def generate_remediation_plan(
        self,
        issue_type: str,
        affected_sessions: List[str],
        root_cause: str
    ) -> Dict[str, Any]:
        """
        生成修复计划

        Args:
            issue_type: 问题类型
            affected_sessions: 受影响会话
            root_cause: 根本原因

        Returns:
            修复计划
        """
        return self.reporter.generate_remediation_report(
            issue_type=issue_type,
            affected_sessions=affected_sessions,
            root_cause=root_cause
        )

    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        获取仪表盘数据

        Returns:
            仪表盘数据
        """
        return self.reporter.generate_dashboard_summary()

    def _generic_evaluation(
        self,
        session_id: str,
        task_type: str,
        task_result: Dict,
        turns_count: int,
        completion_time: float,
        error: Exception
    ) -> Dict[str, Any]:
        """通用评估（用于未知任务类型）"""
        success = 0 if error else 1
        success_rate = 0.0 if error else 0.8

        if self.evaluation_repo:
            eval_id = self.evaluation_repo.save_evaluation(
                session_id=session_id,
                task_type=task_type,
                success=success,
                success_rate=success_rate,
                completion_time=completion_time,
                turns_count=turns_count,
                error_type=str(type(error).__name__) if error else None,
                error_message=str(error) if error else None,
                action_data=task_result
            )
        else:
            eval_id = None

        return {
            'evaluation_id': eval_id,
            'success': success,
            'success_level': 'SUCCESS' if success else 'FAILED',
            'success_rate': success_rate,
            'turns_count': turns_count,
            'completion_time': completion_time,
            'should_reflect': error is not None,
            'timestamp': datetime.now().isoformat()
        }

    def _summarize_insights(self, reflections: List[Dict]) -> List[str]:
        """总结洞察"""
        insights = []

        for ref in reflections[-5:]:
            findings = ref.get('findings', {})
            if isinstance(findings, dict):
                summary = findings.get('evaluation_summary', {})
                if summary:
                    insights.append(
                        f"{ref.get('reflection_type')}: "
                        f"成功率 {summary.get('success_rate', 0):.1%}"
                    )

        return insights

    def _generate_insight_summary(
        self,
        reflections: List[Dict],
        recommendations: List[Dict]
    ) -> str:
        """生成洞察总结"""
        parts = []

        if reflections:
            parts.append(f"过去有 {len(reflections)} 次反思记录")

        high_priority = sum(
            1 for r in recommendations
            if r.get('priority') == 'high'
        )
        if high_priority > 0:
            parts.append(f"{high_priority} 个高优先级建议待处理")

        return "，".join(parts) if parts else "暂无洞察数据"
