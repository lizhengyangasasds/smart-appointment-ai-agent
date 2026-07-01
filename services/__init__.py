"""
业务服务层模块

包含：
- 知识库服务
- 技师服务
- 预约服务
- 用户行为服务
- 推荐调度服务
- 文本嵌入工具
- 记忆服务（工作记忆 + 语义记忆）
"""

from .text_embedding import (
    embed_input,
    find_best_match_indices,
    save_technician_embeddings,
    load_technician_embeddings
)
from .knowledge_service import KnowledgeService
from .technician_service import TechnicianService
from .appointment_service import AppointmentService
from .user_behavior_service import UserBehaviorService
from .recommendation_service import RecommendationService
from .conversation_memory_service import ConversationMemoryService, TokenCounter
from .semantic_memory_service import SemanticMemoryService, SemanticExtractor
from .memory_manager import MemoryManager
from .reflection_service import ReflectionService, get_reflection_service

__all__ = [
    'embed_input',
    'find_best_match_indices',
    'save_technician_embeddings',
    'load_technician_embeddings',
    'KnowledgeService',
    'TechnicianService',
    'AppointmentService',
    'UserBehaviorService',
    'RecommendationService',
    'ConversationMemoryService',
    'TokenCounter',
    'SemanticMemoryService',
    'SemanticExtractor',
    'MemoryManager',
    'ReflectionService',
    'get_reflection_service',
]
