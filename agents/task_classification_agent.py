from dotenv import load_dotenv
from config.model_provider import create_chat_model
from config.constants import SharedState, StateEnum
from .task_classification import (
    TaskClassifier,
    StateManager,
    AgentRouter,
    UnrelatedHandler,
    ClassificationProcessor
)

load_dotenv()


class TaskClassificationAgent:
    """
    任务分类代理主控制器
    
    职责：
    1. 初始化各个分类组件
    2. 提供统一的任务分类接口
    3. 管理与其他Agent的协调
    """
    
    def __init__(self, appointment_agent, consultant_agent):
        # 基础设置
        self.appointment_agent = appointment_agent
        self.consultant_agent = consultant_agent
        
        # 初始化LLM
        self.llm = self._initialize_llm()
        
        # 初始化组件
        self.state_manager = StateManager(SharedState())
        self.task_classifier = TaskClassifier(self.llm)
        self.agent_router = AgentRouter(
            appointment_agent, 
            consultant_agent, 
            self.state_manager
        )
        self.unrelated_handler = UnrelatedHandler(self.state_manager)
        self.classification_processor = ClassificationProcessor(
            self.task_classifier,
            self.state_manager,
            self.agent_router,
            self.unrelated_handler
        )
        
        # 设置回调函数
        self._setup_callbacks()
        
        # 保持向后兼容的state属性
        self.state = self.state_manager.state

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0)
    
    def _setup_callbacks(self):
        """设置Agent的回调函数"""
        if self.appointment_agent and hasattr(self.appointment_agent, 'unrelated_callback'):
            self.appointment_agent.unrelated_callback = self.handle_unrelated
        
        if self.consultant_agent and hasattr(self.consultant_agent, 'set_unrelated_callback'):
            self.consultant_agent.set_unrelated_callback(self.handle_unrelated_async)

    # ===========================================
    # 主要接口方法 - 保持与原版本的兼容性
    # ===========================================
    
    async def classify_task(self, task):
        """分类任务（向后兼容方法）"""
        return await self.classification_processor.process_task_sync(task)

    async def classify_task_stream(self, task: str, memory_context: str = ""):
        """流式分类任务（主要入口）"""
        async for token in self.classification_processor.process_task_stream(task, memory_context):
            yield token

    async def handle_unrelated(self, user_input, memory_context: str = ""):
        """处理无关请求（同步版本）

        ⚠️ 重要修复：历史上此方法会再次调用 process_task_stream 重新分类，
        当 appointment_agent 在解析出 unrelated=True 时把请求回传到这里，
        会形成 分类 → appointment → unrelated → 再次分类 的无限循环。
        现在委托给 unrelated_handler 直接给出一段礼貌拒绝，不再次进入分类器。
        """
        print(f"[DEBUG] 预约机器人转交的请求：{user_input}")
        reply = self.unrelated_handler._get_next_reply()
        return f"[REPLY][归类机器人]{reply}"

    async def handle_unrelated_async(self, user_input, memory_context: str = ""):
        """处理无关请求（异步流版本）

        ⚠️ 与 handle_unrelated 同样的循环修复：不再调 process_task_stream。
        直接 yield 一段礼貌拒绝并结束。
        """
        print(f"[DEBUG] 预约机器人转交的请求：{user_input}")
        reply = self.unrelated_handler._get_next_reply()
        yield "[REPLY][归类机器人]"
        for char in reply:
            yield char

    # ===========================================
    # 扩展功能方法
    # ===========================================
    
    def get_classification_info(self):
        """获取分类系统信息"""
        return self.classification_processor.get_current_state_info()
    
    def reset_conversation(self):
        """重置对话状态"""
        self.classification_processor.reset_conversation()
    
    def set_business_context(self, service_name: str = "推拿服务"):
        """设置业务上下文"""
        self.unrelated_handler.set_business_context(service_name)
