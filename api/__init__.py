"""简化的API模块

用于集中注册各业务子模块的 FastAPI 路由。
"""

# 导入各业务模块的路由
from .appointment import router as appointment_router
from .consultation import router as consultation_router
from .task import router as task_router
from .knowledge import router as knowledge_router
from .technician import router as technician_router
from .user_behavior_analysis import router as user_behavior_analysis_router
from .user_behavior_analysis import router_underscore as user_behavior_analysis_underscore_router
from .memory import router as memory_router
from .reflection_api import router as reflection_router
from .bulk_knowledge import router as bulk_knowledge_router

# FastAPI 路由列表（按注册顺序统一挂载）
api_routers = [
    appointment_router,
    consultation_router,
    task_router,
    knowledge_router,
    technician_router,
    user_behavior_analysis_router,
    user_behavior_analysis_underscore_router,
    memory_router,
    reflection_router,
    bulk_knowledge_router,
]
