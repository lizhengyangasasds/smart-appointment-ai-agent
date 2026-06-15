"""
配置模块

提供应用程序所需的常量和基本配置
"""

from .constants import StateEnum, SharedState, busy_periods_dict
from .settings import settings

__all__ = [
    # 常量和状态
    'StateEnum',
    'SharedState', 
    'busy_periods_dict',
    
    # 基本设置
    'settings'
]
