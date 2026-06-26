# 反思 Agent 模块导出

# 延迟导入避免循环依赖和 langchain 依赖问题
def __getattr__(name):
    """延迟导入 Agent 类"""
    if name == 'AppointmentAgent':
        from .appointment_agent import AppointmentAgent
        return AppointmentAgent
    elif name == 'ConsultantAgent':
        from .consultant_agent import ConsultantAgent
        return ConsultantAgent
    elif name == 'TaskClassificationAgent':
        from .task_classification_agent import TaskClassificationAgent
        return TaskClassificationAgent
    elif name == 'UserBehaviorAgent':
        from .user_behavior_agent import UserBehaviorAgent
        return UserBehaviorAgent
    elif name == 'ReflectionAgent':
        from .reflection_agent import ReflectionAgent
        return ReflectionAgent
    elif name == 'ReflectionMixin':
        from .reflection_agent import ReflectionMixin
        return ReflectionMixin
    elif name == 'SharedState':
        from config.constants import SharedState
        return SharedState
    elif name == 'StateEnum':
        from config.constants import StateEnum
        return StateEnum
    raise AttributeError(f"module 'agents' has no attribute '{name}'")


__all__ = [
    'AppointmentAgent',
    'ConsultantAgent',
    'TaskClassificationAgent',
    'UserBehaviorAgent',
    'ReflectionAgent',
    'ReflectionMixin',
    'SharedState',
    'StateEnum',
]
