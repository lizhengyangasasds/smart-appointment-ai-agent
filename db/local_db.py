"""
兼容性模块 - 重定向到新的Repository模式

此文件提供向后兼容性，确保旧代码可以继续正常工作。
建议逐步迁移到新的 DatabaseRouter 和 Repository 模式。
"""

from .repositories import UserBehaviorRepository, TechnicianRepository
from .base import SessionManager
from typing import Dict, List, Any, Optional
import warnings


class LocalUserBehaviorDB:
    """
    用户行为数据库的兼容性类
    
    将旧接口重定向到新的UserBehaviorRepository
    """
    
    def __init__(self, db_path='sqlite:///data/smart_appointment.db'):
        warnings.warn(
            "LocalUserBehaviorDB已弃用，请使用 DatabaseRouter().user_behavior 或 UserBehaviorRepository",
            DeprecationWarning,
            stacklevel=2
        )
        self.session_manager = SessionManager(db_path)
        self.repo = UserBehaviorRepository(self.session_manager)

    def record_user_behavior(self, action_type: str, action_data: Optional[Dict[str, Any]] = None, 
                            technician_id: Optional[int] = None, session_id: Optional[str] = None, 
                            user_id: str = 'default_user'):
        """兼容性方法：记录用户行为"""
        return self.repo.record_behavior(user_id, action_type, action_data, technician_id, session_id)

    def get_user_behaviors(self, user_id: str = 'default_user', action_type: Optional[str] = None, 
                          days_back: Optional[int] = None) -> List[Dict[str, Any]]:
        """兼容性方法：获取用户行为历史"""
        return self.repo.get_user_behaviors(user_id, action_type, days_back)

    def update_user_preference(self, preference_type: str, preference_value: str, user_id: str = 'default_user'):
        """兼容性方法：更新用户偏好"""
        return self.repo.update_user_preference(user_id, preference_type, preference_value)

    def get_user_preferences(self, user_id: str = 'default_user', preference_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """兼容性方法：获取用户偏好"""
        return self.repo.get_user_preferences(user_id, preference_type)

    def create_recommendation(self, recommendation_type: str, content: str, 
                            technician_id: Optional[int] = None, user_id: str = 'default_user'):
        """兼容性方法：创建推荐"""
        return self.repo.create_recommendation(user_id, recommendation_type, content, technician_id)

    def get_pending_recommendations(self, user_id: str = 'default_user') -> List[Dict[str, Any]]:
        """兼容性方法：获取待发送的推荐"""
        return self.repo.get_pending_recommendations(user_id)

    def mark_recommendation_sent(self, recommendation_id: int):
        """兼容性方法：标记推荐为已发送"""
        return self.repo.mark_recommendation_sent(recommendation_id)

    def get_user_statistics(self, user_id: str = 'default_user', days_back: int = 30) -> Dict[str, Any]:
        """兼容性方法：获取用户统计信息"""
        return self.repo.get_user_statistics(user_id, days_back)


class LocalTechnicianDB:
    """
    技师数据库的兼容性类
    
    将旧接口重定向到新的TechnicianRepository
    """
    
    def __init__(self, db_path='sqlite:///data/smart_appointment.db'):
        warnings.warn(
            "LocalTechnicianDB已弃用，请使用 DatabaseRouter().technicians 或 TechnicianRepository",
            DeprecationWarning,
            stacklevel=2
        )
        self.session_manager = SessionManager(db_path)
        self.repo = TechnicianRepository(self.session_manager)

    def get_technician_by_name(self, session, name: str):
        """兼容性方法：根据姓名获取技师信息"""
        return self.repo.get_technician_by_name(name)

    def get_all_technicians(self, session):
        """兼容性方法：获取所有技师"""
        return self.repo.get_all_technicians()

    def get_all_strengths(self, session):
        """兼容性方法：获取所有技师专长"""
        return self.repo.get_all_strengths()

    def add_technician(self, session, name: str, gender: Optional[str] = None, strength: Optional[str] = None):
        """兼容性方法：添加技师"""
        return self.repo.add_technician(name, gender, strength)

    def add_schedule(self, session, technician_id: int, start_time, end_time, status: str, appointment_id: Optional[int] = None):
        """兼容性方法：添加排班"""
        return self.repo.add_schedule(technician_id, start_time, end_time, status, appointment_id)

    def get_technician_schedules(self, session, technician_id: int, date):
        """兼容性方法：获取技师排班"""
        return self.repo.get_technician_schedules(technician_id, date)

    def is_technician_available(self, session, technician_id: int, start_time, end_time) -> bool:
        """兼容性方法：检查技师是否可用"""
        return self.repo.is_technician_available(technician_id, start_time, end_time)

    def get_technicians_by_gender(self, session, gender: str):
        """兼容性方法：根据性别获取技师"""
        return self.repo.get_technicians_by_gender(gender)


# 为了完全兼容，也提供从原始base_db模块导入的接口
class BaseTechnicianDB:
    """兼容性基类"""
    pass

class BaseKnowledgeDB:
    """兼容性基类"""
    pass


class LocalKnowledgeDB:
    """
    知识库数据库的兼容性类
    """
    
    def __init__(self, db_path='sqlite:///data/smart_appointment.db'):
        warnings.warn(
            "LocalKnowledgeDB已弃用，请使用 DatabaseRouter().knowledge 或 KnowledgeRepository",
            DeprecationWarning,
            stacklevel=2
        )
        from .repositories import KnowledgeRepository
        self.session_manager = SessionManager(db_path)
        self.repo = KnowledgeRepository(self.session_manager)

    def add_document(self, content: str, category: str, keywords: Optional[List[str]] = None, embedding: Optional[List[float]] = None) -> int:
        """兼容性方法：添加知识文档"""
        return self.repo.add_document(content, category, keywords, embedding)

    def get_document(self, doc_id: int) -> Dict[str, Any]:
        """兼容性方法：获取指定文档"""
        return self.repo.get_document(doc_id)

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """兼容性方法：获取所有文档"""
        return self.repo.get_all_documents(include_inactive)

    def update_document(self, doc_id: int, content: Optional[str] = None, category: Optional[str] = None, 
                       keywords: Optional[List[str]] = None, embedding: Optional[List[float]] = None) -> bool:
        """兼容性方法：更新文档"""
        return self.repo.update_document(doc_id, content, category, keywords, embedding)

    def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """兼容性方法：删除文档"""
        return self.repo.delete_document(doc_id, soft_delete)

    def search_documents_by_category(self, category: str) -> List[Dict[str, Any]]:
        """兼容性方法：按分类搜索文档"""
        return self.repo.search_documents_by_category(category)

    def search_documents_by_keywords(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """兼容性方法：按关键词搜索文档"""
        return self.repo.search_documents_by_keywords(keywords)

    def get_all_categories(self) -> List[str]:
        """兼容性方法：获取所有分类"""
        return self.repo.get_all_categories()

    def get_documents_count(self) -> int:
        """兼容性方法：获取文档总数"""
        return self.repo.get_documents_count()