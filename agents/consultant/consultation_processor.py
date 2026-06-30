"""
咨询流程处理器

负责协调整个咨询流程
"""

from typing import AsyncGenerator, Dict, Any
from .knowledge_retriever import KnowledgeRetriever
from .consultation_classifier import ConsultationClassifier
from .response_generator import ResponseGenerator


class ConsultationProcessor:
    """咨询流程处理器"""
    
    def __init__(self, knowledge_retriever: KnowledgeRetriever, 
                 consultation_classifier: ConsultationClassifier,
                 response_generator: ResponseGenerator):
        self.knowledge_retriever = knowledge_retriever
        self.consultation_classifier = consultation_classifier
        self.response_generator = response_generator
    
    async def process_consultation(self, user_input: str) -> str:
        """处理标准咨询"""
        # 1. 检索知识
        knowledge_docs = await self.knowledge_retriever.search_knowledge(user_input, top_k=3)
        
        # 2. 生成响应
        response = await self.response_generator.generate_response(user_input, knowledge_docs)
        
        return response
    
    async def process_consultation_stream(
        self, user_input: str, session_id: str, memory_context: str = ""
    ) -> AsyncGenerator[str, None]:
        """处理流式咨询"""
        full_response = []  # 收集完整响应

        try:
            # 1. 检索知识
            knowledge_docs = await self.knowledge_retriever.search_knowledge(user_input, top_k=3)

            # 2. 生成响应，传入记忆上下文
            async for token in self.response_generator.generate_response_stream(
                user_input, knowledge_docs, memory_context
            ):
                full_response.append(token)
                yield token

            # 3. 获取完整响应内容用于记录（去除流式输出的前缀标签）
            full_response_text = ''.join(full_response)
            response_content = self._clean_response_content(full_response_text)

            # 4. 记录用户行为（包含完整的评估数据）
            await self._record_consultation_behavior(
                user_input, knowledge_docs, session_id, response_content
            )

        except Exception as e:
            yield f"[REPLY][咨询机器人]抱歉，处理您的问题时出现了错误：{str(e)}"
    
    async def handle_unrelated_request(
        self,
        user_input: str,
        unrelated_callback,
        shared_state,
        memory_context: str = "",
    ) -> AsyncGenerator[str, None]:
        """处理与咨询无关的请求"""
        if shared_state:
            from config.constants import StateEnum
            shared_state.value = StateEnum.CLASSIFY

        yield self.response_generator.create_unrelated_message()

        if unrelated_callback:
            async for token in unrelated_callback(user_input, memory_context):
                yield token
    
    async def _record_consultation_behavior(
        self,
        user_input: str,
        knowledge_docs: list,
        session_id: str,
        response_content: str = None
    ):
        """
        记录咨询行为（增强版）

        记录内容包括：
        - question: 用户问题
        - knowledge_docs_used: 使用的文档数量
        - doc_scores: 每条文档的相似度分数
        - categories: 文档分类列表
        - user_id: 从 session_id 解析的用户ID
        - session_id: 会话ID
        - response_content: 回答内容（用于端到端质量评估）
        """
        try:
            from agents.user_behavior_agent import UserBehaviorAgent
            behavior_agent = UserBehaviorAgent()

            # 从 session_id 解析 user_id（格式：user_id-session_id 或 user_id）
            user_id = self._extract_user_id(session_id)

            # 提取每条文档的分数
            doc_scores = [
                doc.get('score', 0.0) for doc in knowledge_docs
            ] if knowledge_docs else []

            # 提取文档分类列表
            categories = list(set(
                doc.get('category', 'unknown') for doc in knowledge_docs
            )) if knowledge_docs else []

            # 提取文档ID列表（用于追踪具体使用了哪些文档）
            doc_ids = [
                doc.get('id') for doc in knowledge_docs
            ] if knowledge_docs else []

            action_data = {
                'question': user_input,
                'knowledge_docs_used': len(knowledge_docs),
                'doc_scores': doc_scores,  # 每条文档的相似度分数
                'doc_ids': doc_ids,  # 文档ID列表
                'max_score': max(doc_scores) if doc_scores else 0.0,  # 最高分
                'avg_score': sum(doc_scores) / len(doc_scores) if doc_scores else 0.0,  # 平均分
                'categories': categories,
                'user_id': user_id,  # 用户ID关联
                'response_content': response_content,  # 回答内容（用于生成质量评估）
                'response_length': len(response_content) if response_content else 0,  # 回答长度
                'timestamp': self._get_timestamp()
            }

            behavior_agent.record_behavior(
                action_type='consultation',
                action_data=action_data,
                session_id=session_id
            )

            # 打印记录摘要（方便调试）
            self._log_behavior_record(action_data)

        except Exception as behavior_error:
            print(f"记录咨询行为失败：{behavior_error}")

    def _extract_user_id(self, session_id: str) -> str:
        """
        从 session_id 提取 user_id

        session_id 格式示例：
        - "user123-session456" -> "user123"
        - "user123" -> "user123"
        - "default_session" -> "default_user"

        Args:
            session_id: 会话ID

        Returns:
            用户ID
        """
        if not session_id:
            return "default_user"

        # 如果包含 '-'，取第一部分作为 user_id
        if '-' in session_id:
            parts = session_id.split('-', 1)
            user_id = parts[0]
            # 过滤掉可能的空值或纯数字session部分
            if user_id and not user_id.isdigit():
                return user_id

        # 如果 session_id 本身看起来像有效的 user_id
        if session_id and not session_id.startswith('default'):
            return session_id

        return "default_user"

    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _log_behavior_record(self, action_data: dict):
        """记录行为数据的摘要日志"""
        doc_count = action_data.get('knowledge_docs_used', 0)
        avg_score = action_data.get('avg_score', 0.0)
        max_score = action_data.get('max_score', 0.0)
        user_id = action_data.get('user_id', 'unknown')

        print(f"📝 咨询行为记录: user={user_id}, docs={doc_count}, "
              f"avg_score={avg_score:.3f}, max_score={max_score:.3f}")

    def _clean_response_content(self, full_response: str) -> str:
        """
        清理响应内容，去除流式输出的前缀标签

        Args:
            full_response: 完整的流式响应

        Returns:
            清理后的纯文本响应
        """
        if not full_response:
            return ""

        # 去除 [REPLY][咨询机器人] 前缀
        import re
        cleaned = re.sub(r'\[REPLY\]\[咨询机器人\]', '', full_response)

        # 去除 [THOUGHT] 开头的标签
        cleaned = re.sub(r'\[THOUGHT\]\[.*?\]', '', cleaned)

        return cleaned.strip()
