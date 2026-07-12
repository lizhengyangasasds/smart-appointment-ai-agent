"""
知识检索器

负责从知识库中检索相关信息
"""

from typing import List, Dict, Any
from services.knowledge_service import KnowledgeService


class KnowledgeRetriever:
    """知识检索器"""
    
    def __init__(self):
        self.knowledge_service = KnowledgeService()
        self.kb_initialized = False
    
    async def initialize(self):
        """初始化知识库服务"""
        if not self.kb_initialized:
            await self.knowledge_service.initialize()
            self.kb_initialized = True
            import logging as _logging
            _logging.getLogger(__name__).info("Consultation knowledge base initialized")
    
    async def search_knowledge(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """搜索相关知识"""
        # 确保知识库已初始化
        if not self.kb_initialized:
            await self.initialize()
        
        # 搜索相关知识
        relevant_docs = await self.knowledge_service.search(query, top_k=top_k)
        
        # 记录检索日志
        self._log_search_results(query, relevant_docs)
        
        return relevant_docs or []
    
    def _log_search_results(self, query: str, relevant_docs: List[Dict[str, Any]]):
        """记录搜索结果日志"""
        import logging as _logging
        _log = _logging.getLogger(__name__)
        if relevant_docs:
            _log.info(f"[kb-search] query={query!r} hits={len(relevant_docs)}")
            for i, doc in enumerate(relevant_docs, 1):
                score = doc.get('score', 0)
                category = doc.get('category', '未知')
                content = (doc.get('content') or '')[:80]
                _log.info(f"  hit#{i} score={score:.3f} category={category} content={content!r}")
        else:
            _log.info(f"[kb-search] query={query!r} hits=0")
