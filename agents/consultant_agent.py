import uuid
from config.model_provider import create_chat_model
from .consultant import (
    KnowledgeRetriever,
    ConsultationClassifier,
    ResponseGenerator,
    ConsultationProcessor
)


class ConsultantAgent:
    """
    咨询机器人主控制器
    
    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个咨询流程
    """
    
    def __init__(self, session_id=None):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.shared_state = None
        self.unrelated_callback = None
        
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

    async def consult_stream(self, user_input: str):
        """
        流式输出咨询结果
        
        这是主要的咨询入口点，协调各个组件完成咨询流程
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
        
        # 3. 处理咨询相关的请求
        async for token in self.consultation_processor.process_consultation_stream(
            user_input, self.session_id
        ):
            yield token
        
        # 4. 重置状态
        self._reset_state_after_consultation()

    def _reset_state_after_consultation(self):
        """咨询完成后重置状态"""
        if self.shared_state:
            from config.constants import StateEnum
            self.shared_state.value = StateEnum.CLASSIFY
