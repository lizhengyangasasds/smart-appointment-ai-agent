"""
UserBehavior Module

提供用户行为分析相关的核心组件（简化版）：
- BehaviorRecorder: 行为记录器 - 记录用户操作行为
- PatternAnalyzer: 模式分析器 - 分析用户行为模式和生成回访提醒
- PreferenceManager: 偏好管理器 - 管理用户偏好数据
"""

from .behavior_recorder import BehaviorRecorder
from .pattern_analyzer import PatternAnalyzer
from .preference_manager import PreferenceManager

__all__ = [
    'BehaviorRecorder',
    'PatternAnalyzer',
    'PreferenceManager'
]
