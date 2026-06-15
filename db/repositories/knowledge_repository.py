from typing import List, Dict, Any, Optional
from datetime import datetime
from ..base.interfaces import BaseKnowledgeRepository
from ..base.session_manager import SessionManager
from ..models import KnowledgeDocument


class KnowledgeRepository(BaseKnowledgeRepository):
    """
    知识库数据访问对象
    
    职责：
    1. 知识文档的CRUD操作
    2. 文档搜索和分类
    3. 文档状态管理
    """
    
    def __init__(self, session_manager: SessionManager):
        """
        初始化知识库数据仓库
        
        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def add_document(self, content: str, category: str, keywords: Optional[List[str]] = None, 
                    embedding: Optional[List[float]] = None) -> int:
        """
        添加知识文档
        
        Args:
            content: 文档内容
            category: 文档分类
            keywords: 关键词列表
            embedding: 嵌入向量
            
        Returns:
            新创建的文档ID
        """
        with self.session_manager.session_scope() as session:
            document = KnowledgeDocument(
                content=content,
                category=category,
                keywords=keywords,
                embedding=embedding
            )
            session.add(document)
            session.flush()
            return document.id

    def get_document(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """
        获取指定文档
        
        Args:
            doc_id: 文档ID
            
        Returns:
            文档信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return None
                
            return self._document_to_dict(document)

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """
        获取所有文档
        
        Args:
            include_inactive: 是否包含已删除的文档
            
        Returns:
            文档信息列表
        """
        with self.session_manager.session_scope() as session:
            query = session.query(KnowledgeDocument)
            
            if not include_inactive:
                query = query.filter(KnowledgeDocument.is_active == 1)
                
            documents = query.all()
            return [self._document_to_dict(doc) for doc in documents]

    def update_document(self, doc_id: int, content: Optional[str] = None, category: Optional[str] = None, 
                       keywords: Optional[List[str]] = None, embedding: Optional[List[float]] = None) -> bool:
        """
        更新文档
        
        Args:
            doc_id: 文档ID
            content: 新内容
            category: 新分类
            keywords: 新关键词
            embedding: 新嵌入向量
            
        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return False
            
            if content is not None:
                document.content = content
            if category is not None:
                document.category = category
            if keywords is not None:
                document.keywords = keywords
            if embedding is not None:
                document.embedding = embedding
            
            document.updated_at = datetime.utcnow()
            return True

    def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """
        删除文档（支持软删除）
        
        Args:
            doc_id: 文档ID
            soft_delete: 是否软删除
            
        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return False
            
            if soft_delete:
                document.is_active = 0
                document.updated_at = datetime.utcnow()
            else:
                session.delete(document)
            
            return True

    def search_documents_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        按分类搜索文档
        
        Args:
            category: 文档分类
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.category == category,
                KnowledgeDocument.is_active == 1
            ).all()
            
            return [self._document_to_dict(doc) for doc in documents]

    def search_documents_by_keywords(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        按关键词搜索文档
        
        Args:
            keywords: 关键词列表
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1
            ).all()
            
            # 简单的关键词匹配
            matched_docs = []
            for doc in documents:
                doc_keywords = doc.keywords or []
                if any(keyword in doc_keywords for keyword in keywords):
                    matched_docs.append(self._document_to_dict(doc))
            
            return matched_docs

    def search_documents_by_content(self, search_text: str) -> List[Dict[str, Any]]:
        """
        按内容搜索文档
        
        Args:
            search_text: 搜索文本
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.content.contains(search_text),
                KnowledgeDocument.is_active == 1
            ).all()
            
            return [self._document_to_dict(doc) for doc in documents]

    def get_all_categories(self) -> List[str]:
        """
        获取所有分类
        
        Returns:
            分类列表
        """
        with self.session_manager.session_scope() as session:
            categories = session.query(KnowledgeDocument.category).filter(
                KnowledgeDocument.is_active == 1
            ).distinct().all()
            
            return [cat[0] for cat in categories]

    def get_documents_count(self) -> int:
        """
        获取文档总数
        
        Returns:
            活跃文档数量
        """
        with self.session_manager.session_scope() as session:
            return session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1
            ).count()

    def get_documents_by_category_count(self) -> Dict[str, int]:
        """
        获取各分类的文档数量
        
        Returns:
            分类和文档数量的字典
        """
        with self.session_manager.session_scope() as session:
            from sqlalchemy import func
            
            result = session.query(
                KnowledgeDocument.category,
                func.count(KnowledgeDocument.id).label('count')
            ).filter(
                KnowledgeDocument.is_active == 1
            ).group_by(KnowledgeDocument.category).all()
            
            return {category: count for category, count in result}

    def _document_to_dict(self, document: KnowledgeDocument) -> Dict[str, Any]:
        """将文档对象转换为字典"""
        return {
            'id': document.id,
            'content': document.content,
            'category': document.category,
            'keywords': document.keywords,
            'embedding': document.embedding,
            'created_at': document.created_at,
            'updated_at': document.updated_at,
            'is_active': bool(document.is_active)
        }
