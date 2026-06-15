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
            print("✅ 咨询机器人知识库服务已初始化")
    
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
        if relevant_docs:
            print(f"🔍 知识库检索结果 (查询: '{query}'):")
            for i, doc in enumerate(relevant_docs, 1):
                score = doc.get('score', 0)
                category = doc.get('category', '未知')
                content = doc.get('content', '')[:80]
                print(f"  {i}. [相关度:{score:.3f}] [分类:{category}] {content}...")
            print(f"📊 知识库统计: 共检索到 {len(relevant_docs)} 条相关知识")
        else:
            print(f"⚠️ 知识库检索: 未找到与 '{query}' 相关的知识")
