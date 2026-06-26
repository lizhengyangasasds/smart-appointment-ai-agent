import uuid
import logging
from typing import Dict, Any, List
from config.model_provider import create_chat_model
from .consultant import (
    KnowledgeRetriever,
    ConsultationClassifier,
    ResponseGenerator,
    ConsultationProcessor
)
from .reflection import ReflectionAwareMixin


class ConsultantAgent(ReflectionAwareMixin):
    """
    咨询机器人主控制器

    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个咨询流程
    4. 应用反思洞察优化咨询质量（闭环）
    """

    def __init__(self, session_id=None, reflection_engine=None):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.shared_state = None
        self.unrelated_callback = None
        self.logger = logging.getLogger(__name__)

        # 初始化反思感知（闭环支持）
        ReflectionAwareMixin.__init__(self, reflection_engine=reflection_engine)

        # 初始化LLM
        self.llm = self._initialize_llm()

        # 初始化组件
        self.knowledge_retriever = KnowledgeRetriever()
        self.consultation_classifier = ConsultationClassifier(self.llm)
        self.response_generator = ResponseGenerator(self.llm)
        self.consultation_processor = ConsultationProcessor(
            self.knowledge_retriever,
            self.consultation_classifier,
            self.response_generator
        )

        # 反思洞察应用状态
        self._insights_applied = False
        self._response_style_hints: Dict[str, Any] = {}
        self._topics_to_emphasize: List[str] = []
        self._topics_to_avoid: List[str] = []

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0.3)

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.knowledge_retriever.initialize()
        print("咨询机器人已启动（数据库RAG模式）")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """异步上下文管理器出口"""
        pass

    def set_shared_state(self, shared_state):
        """设置共享状态"""
        self.shared_state = shared_state

    def set_unrelated_callback(self, callback):
        """设置处理非相关任务的回调函数"""
        self.unrelated_callback = callback

    async def consult(self, user_input: str) -> str:
        """
        基础咨询功能
        
        用于非流式的简单咨询场景
        """
        return await self.consultation_processor.process_consultation(user_input)

    async def consult_stream(self, user_input: str, memory_context: str = ""):
        """
        流式输出咨询结果

        这是主要的咨询入口点，协调各个组件完成咨询流程

        Args:
            user_input: 用户输入
            memory_context: 外部记忆上下文（对话历史摘要+用户画像）
        """
        # 1. 检查是否与咨询相关
        is_consultation = await self.consultation_classifier.is_consultation_related(user_input)

        if not is_consultation:
            # 2. 处理与咨询无关的请求
            async for token in self.consultation_processor.handle_unrelated_request(
                user_input, self.unrelated_callback, self.shared_state
            ):
                yield token
            return

        # 3. 处理咨询相关的请求，传入记忆上下文
        async for token in self.consultation_processor.process_consultation_stream(
            user_input, self.session_id, memory_context
        ):
            yield token

        # 4. 重置状态
        self._reset_state_after_consultation()

    def _reset_state_after_consultation(self):
        """咨询完成后重置状态"""
        if self.shared_state:
            from config.constants import StateEnum
            self.shared_state.value = StateEnum.CLASSIFY

    def apply_insights(self, insights: Dict[str, Any]) -> None:
        """
        应用反思洞察到咨询决策

        根据反思洞察调整：
        1. 回复风格
        2. 强调/避免的话题
        3. 知识检索策略
        """
        self.logger.info("应用反思洞察到咨询 Agent")

        # 1. 获取任务特定的洞察
        task_insights = self.get_task_type_insights('consultation')

        # 2. 提取回复风格提示
        recommendations = task_insights.get('recommendations', [])
        self._response_style_hints = {}

        for rec in recommendations:
            action = rec.get('action', {})
            if action.get('type') == 'prompt':
                self._response_style_hints = action.get('parameters', {})
                break

        # 3. 提取话题调整
        self._topics_to_emphasize = []
        self._topics_to_avoid = []
        bad_cases = task_insights.get('bad_cases', [])

        for bc in bad_cases:
            case_type = bc.get('case_type', '')
            description = bc.get('description', '')

            # 根据坏 case 类型调整话题
            if 'topic_missed' in case_type or 'insufficient_detail' in description.lower():
                # 强调某些话题
                self._topics_to_emphasize.append(description)

            if 'over_info' in case_type or 'irrelevant' in description.lower():
                # 避免某些话题
                self._topics_to_avoid.append(description)

        # 4. 获取推荐策略配置
        strategy_config = self.get_preferred_strategy('consultation_response')
        if strategy_config:
            self.logger.info(f"应用咨询策略: {strategy_config}")

        self._insights_applied = True
        self.logger.debug(
            f"洞察应用完成: {len(self._response_style_hints)} 个回复提示, "
            f"{len(self._topics_to_emphasize)} 个强调话题, "
            f"{len(self._topics_to_avoid)} 个避免话题"
        )

    def get_response_style_hint(self) -> str:
        """
        获取回复风格提示

        Returns:
            回复风格建议
        """
        if not self._insights_applied:
            self.apply_insights(self.get_insights())

        style = self._response_style_hints.get('style', 'professional')

        hints = {
            'professional': '回复要专业、准确，使用规范的术语',
            'friendly': '回复要友好、亲切，使用通俗易懂的语言',
            'detailed': '回复要详细、全面，涵盖各个方面',
            'concise': '回复要简洁明了，直接回答核心问题'
        }

        return hints.get(style, hints['professional'])

    def should_emphasize_topic(self, topic: str) -> bool:
        """
        检查是否应该强调某个话题

        Args:
            topic: 话题关键词

        Returns:
            是否应该强调
        """
        if not self._insights_applied:
            self.apply_insights(self.get_insights())

        for to_emphasize in self._topics_to_emphasize:
            if to_emphasize.lower() in topic.lower():
                return True

        return False

    def should_avoid_topic(self, topic: str) -> bool:
        """
        检查是否应该避免某个话题

        Args:
            topic: 话题关键词

        Returns:
            是否应该避免
        """
        if not self._insights_applied:
            self.apply_insights(self.get_insights())

        for to_avoid in self._topics_to_avoid:
            if to_avoid.lower() in topic.lower():
                return True

        return False

    def validate_knowledge_retrieval(
        self,
        query: str,
        retrieved_docs: List[Dict]
    ) -> Dict[str, Any]:
        """
        使用反思洞察验证知识检索结果

        Args:
            query: 检索查询
            retrieved_docs: 检索到的文档

        Returns:
            验证结果
        """
        # 检查是否有缺失话题
        missing_topics = []
        for topic in self._topics_to_emphasize:
            found = any(topic.lower() in doc.get('content', '').lower()
                       for doc in retrieved_docs)
            if not found:
                missing_topics.append(topic)

        warnings = []
        if missing_topics:
            warnings.append(f"检索结果可能缺少相关话题: {', '.join(missing_topics[:2])}")

        return {
            'valid': len(warnings) == 0,
            'warnings': warnings,
            'missing_topics': missing_topics,
            'suggested_adjustments': {
                'expand_search': len(missing_topics) > 0
            }
        }

    def get_reflection_context_for_prompt(self) -> str:
        """
        获取反思上下文用于提示词

        Returns:
            反思上下文文本
        """
        insights = self.get_insights()

        parts = []

        # 添加回复风格提示
        style_hint = self.get_response_style_hint()
        if style_hint:
            parts.append(style_hint)

        # 添加强调话题
        if self._topics_to_emphasize:
            parts.append(f"注意涵盖: {', '.join(self._topics_to_emphasize[:2])}")

        # 添加避免话题
        if self._topics_to_avoid:
            parts.append(f"避免涉及: {', '.join(self._topics_to_avoid[:2])}")

        return '\n'.join(parts) if parts else ""
