"""
反思上下文提供者 - 为 Agent 提供结构化的反思上下文

核心功能：
1. 将反思洞察转换为 Agent 可理解的上下文
2. 构建包含历史经验的对话上下文
3. 提供实时的策略调整建议
4. 支持提示词动态注入
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import logging


class ContextFormat(Enum):
    """上下文格式"""
    COMPACT = "compact"       # 紧凑格式（用于提示词）
    DETAILED = "detailed"    # 详细格式（用于调试）
    ACTIONABLE = "actionable" # 可执行格式（用于决策）


@dataclass
class ReflectionContext:
    """反思上下文"""
    # 基本信息
    session_id: str
    task_type: str
    timestamp: datetime = field(default_factory=datetime.now)

    # 洞察数据
    recent_insights: List[str] = field(default_factory=list)
    recommendations: List[Dict[str, Any]] = field(default_factory=list)
    bad_cases: List[Dict[str, Any]] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)

    # 策略配置
    active_strategy: Optional[Dict[str, Any]] = None
    strategy_adjustments: Dict[str, Any] = field(default_factory=dict)

    # 上下文文本
    context_text: str = ""
    prompt_injection: str = ""

    # 元数据
    confidence: float = 0.0
    data_sources: List[str] = field(default_factory=list)


class ReflectionContextProvider:
    """
    反思上下文提供者

    将反思洞察转换为 Agent 可直接使用的上下文
    """

    def __init__(
        self,
        reflection_engine=None,
        strategy_updater=None,
        closed_loop_evaluator=None
    ):
        self.reflection_engine = reflection_engine
        self.strategy_updater = strategy_updater
        self.closed_loop_evaluator = closed_loop_evaluator
        self.logger = logging.getLogger(__name__)

        # 上下文缓存
        self._context_cache: Dict[str, ReflectionContext] = {}
        self._cache_ttl = 60  # 1分钟缓存

    def get_context_for_agent(
        self,
        session_id: str,
        task_type: str,
        format: ContextFormat = ContextFormat.COMPACT
    ) -> ReflectionContext:
        """
        为 Agent 获取反思上下文

        Args:
            session_id: 会话 ID
            task_type: 任务类型
            format: 上下文格式

        Returns:
            反思上下文
        """
        # 检查缓存
        cache_key = f"{session_id}_{task_type}"
        cached = self._context_cache.get(cache_key)

        if cached:
            age = (datetime.now() - cached.timestamp).seconds
            if age < self._cache_ttl:
                return cached

        # 构建新上下文
        context = self._build_context(session_id, task_type, format)

        # 更新缓存
        self._context_cache[cache_key] = context

        return context

    def _build_context(
        self,
        session_id: str,
        task_type: str,
        format: ContextFormat
    ) -> ReflectionContext:
        """构建反思上下文"""
        context = ReflectionContext(
            session_id=session_id,
            task_type=task_type
        )

        # 1. 获取反思洞察
        if self.reflection_engine:
            insights = self.reflection_engine.get_reflection_insights(days=7)
            context.recent_insights = insights.get('recent_insights', [])
            context.recommendations = insights.get('actionable_recommendations', [])
            context.bad_cases = insights.get('recent_bad_cases', [])
            context.data_sources.append('reflection_engine')
        else:
            context.recent_insights = []
            context.recommendations = []
            context.bad_cases = []

        # 2. 获取任务类型特定的洞察
        if self.reflection_engine:
            task_insights = self.reflection_engine.get_task_type_insights(task_type)
            context.patterns = task_insights.get('patterns', [])
            context.data_sources.append('task_specific')

        # 3. 获取活跃策略
        if self.strategy_updater:
            from .strategy_updater import StrategyType

            # 根据任务类型选择策略类型
            if task_type == 'appointment':
                strategy_type = StrategyType.MATCHING
            elif task_type == 'consultation':
                strategy_type = StrategyType.PROMPT
            else:
                strategy_type = StrategyType.ROUTING

            context.active_strategy = self.strategy_updater.get_active_strategy(strategy_type)
            context.strategy_adjustments = context.active_strategy.get('config', {}) if context.active_strategy else {}
            context.data_sources.append('strategy_updater')

        # 4. 根据格式生成文本
        if format == ContextFormat.COMPACT:
            context.context_text = self._generate_compact_text(context)
            context.prompt_injection = self._generate_prompt_injection(context)
        elif format == ContextFormat.DETAILED:
            context.context_text = self._generate_detailed_text(context)
            context.prompt_injection = self._generate_prompt_injection(context)
        elif format == ContextFormat.ACTIONABLE:
            context.context_text = self._generate_actionable_text(context)
            context.prompt_injection = self._generate_actionable_prompt(context)

        # 5. 计算置信度
        context.confidence = self._calculate_confidence(context)

        return context

    def _generate_compact_text(self, context: ReflectionContext) -> str:
        """生成紧凑格式文本"""
        parts = []

        # 添加洞察摘要
        if context.recent_insights:
            parts.append(f"【历史洞察】{'; '.join(context.recent_insights[:3])}")

        # 添加高优先级建议
        high_priority = [r for r in context.recommendations if r.get('priority') == 'high']
        if high_priority:
            rec_texts = [r.get('title', r.get('description', '')) for r in high_priority[:2]]
            parts.append(f"【当前建议】{'; '.join(rec_texts)}")

        # 添加需要避免的模式
        if context.bad_cases:
            avoid_texts = [bc.get('description', '')[:30] for bc in context.bad_cases[:2]]
            parts.append(f"【避免问题】{'; '.join(avoid_texts)}")

        return '\n'.join(parts) if parts else "暂无反思数据"

    def _generate_detailed_text(self, context: ReflectionContext) -> str:
        """生成详细格式文本"""
        lines = ["=== 反思上下文详情 ==="]

        # 洞察列表
        if context.recent_insights:
            lines.append("\n【历史洞察】")
            for insight in context.recent_insights:
                lines.append(f"  - {insight}")

        # 推荐列表
        if context.recommendations:
            lines.append("\n【可执行建议】")
            for i, rec in enumerate(context.recommendations[:5], 1):
                lines.append(f"  {i}. {rec.get('title', rec.get('description', ''))}")
                if rec.get('action'):
                    lines.append(f"     操作: {rec['action']}")

        # 坏 case 列表
        if context.bad_cases:
            lines.append("\n【需要避免的 case】")
            for bc in context.bad_cases[:3]:
                lines.append(f"  - {bc.get('description', '')}")
                if bc.get('suggested_fix'):
                    lines.append(f"    建议: {bc['suggested_fix']}")

        # 活跃策略
        if context.active_strategy:
            lines.append("\n【当前策略】")
            lines.append(f"  版本: {context.active_strategy.get('version_id', 'unknown')}")
            lines.append(f"  配置: {context.active_strategy.get('config', {})}")

        return '\n'.join(lines)

    def _generate_actionable_text(self, context: ReflectionContext) -> str:
        """生成可执行格式文本"""
        actions = []

        # 从建议生成可执行操作
        for rec in context.recommendations[:3]:
            if rec.get('action'):
                actions.append({
                    'type': 'recommendation',
                    'description': rec.get('title', ''),
                    'action': rec['action'],
                    'priority': rec.get('priority', 'normal')
                })

        # 从坏 case 生成避免操作
        for bc in context.bad_cases[:2]:
            if bc.get('suggested_fix'):
                actions.append({
                    'type': 'avoidance',
                    'description': bc.get('description', ''),
                    'action': bc['suggested_fix'],
                    'priority': 'high'
                })

        # 转换为文本
        lines = ["=== 可执行操作 ==="]
        for action in actions:
            lines.append(f"[{action['priority'].upper()}] {action['description']}")
            lines.append(f"  操作: {action['action']}")

        return '\n'.join(lines)

    def _generate_prompt_injection(self, context: ReflectionContext) -> str:
        """生成提示词注入片段"""
        injections = []

        # 基于洞察添加指导
        if context.recent_insights:
            injections.append(
                f"参考以下历史经验: {'; '.join(context.recent_insights[:2])}"
            )

        # 基于坏 case 添加警告
        if context.bad_cases:
            avoid_patterns = [bc.get('description', '')[:40] for bc in context.bad_cases[:2]]
            injections.append(
                f"注意避免: {'; '.join(avoid_patterns)}"
            )

        # 基于建议添加指导
        high_priority = [r for r in context.recommendations if r.get('priority') == 'high']
        if high_priority:
            tips = [r.get('title', '') for r in high_priority[:2]]
            injections.append(f"建议: {'; '.join(tips)}")

        return '\n'.join(injections)

    def _generate_actionable_prompt(self, context: ReflectionContext) -> str:
        """生成可执行的提示词"""
        lines = [
            "【决策指导】",
            "在做决策时，请考虑以下因素:"
        ]

        # 添加建议
        for i, rec in enumerate(context.recommendations[:3], 1):
            lines.append(f"  {i}. {rec.get('title', '')}: {rec.get('action', '')}")

        # 添加警告
        if context.bad_cases:
            lines.append("\n【警告】")
            for bc in context.bad_cases[:2]:
                lines.append(f"  - 避免: {bc.get('description', '')}")

        return '\n'.join(lines)

    def _calculate_confidence(self, context: ReflectionContext) -> float:
        """计算上下文置信度"""
        confidence = 0.0

        # 数据源数量
        if 'reflection_engine' in context.data_sources:
            confidence += 0.3

        if 'task_specific' in context.data_sources:
            confidence += 0.2

        if 'strategy_updater' in context.data_sources:
            confidence += 0.2

        # 数据完整性
        if context.recent_insights:
            confidence += 0.1

        if context.recommendations:
            confidence += 0.1

        if context.bad_cases:
            confidence += 0.1

        return min(1.0, confidence)

    def inject_context_into_prompt(
        self,
        base_prompt: str,
        session_id: str,
        task_type: str,
        format: ContextFormat = ContextFormat.COMPACT
    ) -> str:
        """
        将反思上下文注入到提示词中

        Args:
            base_prompt: 基础提示词
            session_id: 会话 ID
            task_type: 任务类型
            format: 上下文格式

        Returns:
            注入后的提示词
        """
        context = self.get_context_for_agent(session_id, task_type, format)

        if not context.prompt_injection:
            return base_prompt

        # 构建注入文本
        injection = f"\n\n## 历史反思经验\n{context.prompt_injection}\n"

        # 在适当位置注入（通常在角色定义之后，任务描述之前）
        if "## 任务" in base_prompt:
            return base_prompt.replace("## 任务", f"{injection}\n## 任务")
        elif "# 任务" in base_prompt:
            return base_prompt.replace("# 任务", f"{injection}\n# 任务")
        else:
            return base_prompt + injection

    def get_strategy_for_context(
        self,
        task_type: str,
        additional_context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        获取适用于当前上下文的策略

        Args:
            task_type: 任务类型
            additional_context: 额外上下文

        Returns:
            策略配置
        """
        if not self.strategy_updater:
            return {}

        from .strategy_updater import StrategyType

        if task_type == 'appointment':
            strategy_type = StrategyType.MATCHING
        elif task_type == 'consultation':
            strategy_type = StrategyType.PROMPT
        else:
            strategy_type = StrategyType.ROUTING

        strategy = self.strategy_updater.get_active_strategy(strategy_type)

        if not strategy:
            return {}

        config = strategy.get('config', {}).copy()

        # 根据额外上下文调整配置
        if additional_context:
            # 可以根据上下文动态调整参数
            pass

        return {
            'version_id': strategy.get('version_id'),
            'config': config,
            'adjustments': self._generate_strategy_adjustments(task_type, config)
        }

    def _generate_strategy_adjustments(
        self,
        task_type: str,
        config: Dict[str, Any]
    ) -> List[str]:
        """生成策略调整说明"""
        adjustments = []

        for key, value in config.items():
            if isinstance(value, (int, float)):
                adjustments.append(f"{key}={value}")

        return adjustments

    def clear_cache(self) -> None:
        """清除缓存"""
        self._context_cache.clear()
        self.logger.info("反思上下文缓存已清除")
