"""
反思 Agent 核心组件

包含开环组件（评估-分析-报告）和闭环组件（策略更新-效果验证-上下文注入）
"""

from .evaluator import TaskEvaluator
from .analyzer import ReflectionAnalyzer
from .reporter import ReflectionReporter
from .engine import ReflectionEngine

# 闭环组件
from .reflection_aware import ReflectionAwareMixin
from .strategy_updater import StrategyUpdater, StrategyType, StrategyStatus
from .closed_loop_evaluator import ClosedLoopEvaluator, EvaluationResult
from .context_provider import ReflectionContextProvider, ContextFormat

__all__ = [
    # 开环组件
    'TaskEvaluator',
    'ReflectionAnalyzer',
    'ReflectionReporter',
    'ReflectionEngine',
    # 闭环组件
    'ReflectionAwareMixin',
    'StrategyUpdater',
    'StrategyType',
    'StrategyStatus',
    'ClosedLoopEvaluator',
    'EvaluationResult',
    'ReflectionContextProvider',
    'ContextFormat',
]
