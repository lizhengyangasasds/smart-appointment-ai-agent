"""
反思感知基类 - 让 Agent 能够访问和应用反思洞察

提供闭环反馈机制，使反思结果能够影响 Agent 的后续决策
"""

from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod
import logging


class ReflectionAwareMixin(ABC):
    """
    反思感知混入类

    其他 Agent 可以通过继承此类来获取反思洞察，
    并在决策时参考这些洞察来优化行为
    """

    def __init__(self, reflection_engine=None, **kwargs):
        super().__init__(**kwargs)
        self._reflection_engine = reflection_engine
        self._cached_insights: Optional[Dict[str, Any]] = None
        self._insights_cache_ttl = 300  # 5分钟缓存
        self._last_insights_fetch = None
        self.logger = logging.getLogger(self.__class__.__name__)

    def get_insights(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        获取反思洞察（带缓存）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            反思洞察数据
        """
        from datetime import datetime, timedelta

        if not self._reflection_engine:
            return self._get_default_insights()

        # 检查缓存
        if (not force_refresh
            and self._cached_insights is not None
            and self._last_insights_fetch is not None
        ):
            if (datetime.now() - self._last_insights_fetch).seconds < self._insights_cache_ttl:
                return self._cached_insights

        # 获取最新洞察
        try:
            self._cached_insights = self._reflection_engine.get_reflection_insights(days=7)
            self._last_insights_fetch = datetime.now()
            self.logger.debug(f"刷新反思洞察缓存: {len(self._cached_insights)} 个字段")
        except Exception as e:
            self.logger.warning(f"获取反思洞察失败: {e}")
            self._cached_insights = self._get_default_insights()

        return self._cached_insights

    def get_task_type_insights(self, task_type: str) -> Dict[str, Any]:
        """
        获取特定任务类型的洞察

        Args:
            task_type: 任务类型 (appointment/consultation/classification)

        Returns:
            该任务类型的相关洞察
        """
        insights = self.get_insights()
        task_key = f"{task_type}_insights"

        if task_key in insights:
            return insights[task_key]

        # 从推荐中筛选相关任务类型的建议
        recommendations = insights.get('actionable_recommendations', [])
        relevant_recs = [
            r for r in recommendations
            if r.get('related_task_type') == task_type or r.get('task_type') == task_type
        ]

        return {
            'task_type': task_type,
            'recommendations': relevant_recs,
            'bad_cases': [bc for bc in insights.get('recent_bad_cases', [])
                         if bc.get('task_type') == task_type],
            'summary': insights.get('summary', '')
        }

    def _get_default_insights(self) -> Dict[str, Any]:
        """获取默认洞察（当反思引擎不可用时）"""
        return {
            'recent_insights': [],
            'actionable_recommendations': [],
            'recent_bad_cases': [],
            'summary': '暂无反思数据'
        }

    @abstractmethod
    def apply_insights(self, insights: Dict[str, Any]) -> None:
        """
        应用反思洞察到 Agent 行为

        子类需要实现此方法来根据洞察调整自身行为
        """
        pass

    def should_avoid_pattern(self, pattern: str) -> bool:
        """
        检查是否应该避免某个模式

        Args:
            pattern: 模式标识

        Returns:
            是否应该避免
        """
        insights = self.get_insights()
        avoid_patterns = insights.get('avoid_patterns', [])

        return pattern in avoid_patterns

    def get_preferred_strategy(self, scenario: str) -> Optional[str]:
        """
        获取推荐策略

        Args:
            scenario: 场景标识

        Returns:
            推荐策略名称
        """
        insights = self.get_insights()
        strategies = insights.get('recommended_strategies', {})

        return strategies.get(scenario)

    def validate_action_against_insights(
        self,
        action: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        根据反思洞察验证动作

        Args:
            action: 待验证的动作
            context: 动作上下文

        Returns:
            验证结果 {valid: bool, warnings: List[str], adjustments: Dict}
        """
        insights = self.get_insights()
        bad_cases = insights.get('recent_bad_cases', [])

        warnings = []
        adjustments = {}

        # 检查是否触发了坏 case
        for bad_case in bad_cases:
            if self._matches_bad_case(action, context, bad_case):
                warnings.append(bad_case.get('description', '未知问题'))
                adjustment = bad_case.get('suggested_fix', {})
                if adjustment:
                    adjustments.update(adjustment)

        return {
            'valid': len(warnings) == 0,
            'warnings': warnings,
            'adjustments': adjustments,
            'requires_adjustment': bool(adjustments)
        }

    def _matches_bad_case(
        self,
        action: Dict[str, Any],
        context: Dict[str, Any],
        bad_case: Dict[str, Any]
    ) -> bool:
        """检查动作是否匹配坏 case"""
        trigger = bad_case.get('trigger', {})

        # 检查动作类型
        if 'action_type' in trigger:
            if action.get('type') != trigger['action_type']:
                return False

        # 检查用户特征
        if 'user_profile' in trigger:
            for key, value in trigger['user_profile'].items():
                if context.get(key) != value:
                    return False

        # 检查时间条件
        if 'time_conditions' in trigger:
            # 可以扩展更多时间条件检查
            pass

        return True

    def record_action_outcome(
        self,
        action: Dict[str, Any],
        outcome: str,
        session_id: str
    ) -> None:
        """
        记录动作结果（供后续反思使用）

        Args:
            action: 执行的动作
            outcome: 结果 (success/failure)
            session_id: 会话 ID
        """
        if not self._reflection_engine:
            return

        # 这里可以将动作结果传递给反思引擎
        # 反思引擎可以在后续分析中考虑这些数据
        self.logger.debug(f"记录动作结果: action={action.get('type')}, outcome={outcome}")

    def refresh_insights(self) -> None:
        """强制刷新洞察缓存"""
        self.get_insights(force_refresh=True)
