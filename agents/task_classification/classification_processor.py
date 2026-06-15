"""
分类流程处理器 - 协调整个任务分类流程

职责：
1. 协调任务分类器、状态管理器、路由器等组件
2. 实现完整的分类处理流程
3. 处理异常情况和边界场景
4. 提供统一的流程入口
"""

from typing import AsyncGenerator
from .task_classifier import TaskClassifier
from .state_manager import StateManager
from .agent_router import AgentRouter
from .unrelated_handler import UnrelatedHandler


class ClassificationProcessor:
    """分类流程处理器 - 协调完整的任务分类和处理流程"""
    
    def __init__(self, 
                 task_classifier: TaskClassifier,
                 state_manager: StateManager,
                 agent_router: AgentRouter,
                 unrelated_handler: UnrelatedHandler):
        """
        初始化分类流程处理器
        
        Args:
            task_classifier: 任务分类器
            state_manager: 状态管理器
            agent_router: 智能体路由器
            unrelated_handler: 无关请求处理器
        """
        self.task_classifier = task_classifier
        self.state_manager = state_manager
        self.agent_router = agent_router
        self.unrelated_handler = unrelated_handler
    
    async def process_task_stream(self, task: str) -> AsyncGenerator[str, None]:
        """
        流式处理任务分类和路由
        
        Args:
            task: 用户输入的任务内容
            
        Yields:
            str: 流式响应内容
        """
        try:
            # 检查是否需要进行分类
            if self.state_manager.should_classify():
                # 进行任务分类
                category = await self.task_classifier.classify_task(task)
                
                # 根据分类结果路由
                if category == "appointment" and self.agent_router.appointment_agent:
                    async for token in self.agent_router.route_to_appointment(task):
                        yield token
                elif category == "query" and self.agent_router.consultant_agent:
                    async for token in self.agent_router.route_to_consultation(task):
                        yield token
                else:
                    # 不支持的任务类型
                    async for token in self.agent_router.handle_unsupported_task(category):
                        yield token
            else:
                # 根据当前状态继续处理
                async for token in self.agent_router.route_by_state(task):
                    yield token
                    
        except Exception as e:
            # 处理异常情况
            yield f"[ERROR]处理任务时发生错误: {str(e)}"
            self.state_manager.force_reset()
    
    async def process_task_sync(self, task: str) -> str:
        """
        同步处理任务分类和路由（非流式）
        
        Args:
            task: 用户输入的任务内容
            
        Returns:
            str: 处理结果
        """
        try:
            # 检查是否需要进行分类
            if self.state_manager.should_classify():
                category = await self.task_classifier.classify_task(task)
                
                if category == "appointment" and self.agent_router.appointment_agent:
                    self.state_manager.transition_to_appointment()
                    return await self.agent_router.appointment_agent.run(user_input=task)
                elif category == "query" and self.agent_router.consultant_agent:
                    self.state_manager.transition_to_consultation()
                    async with self.agent_router.consultant_agent as agent:
                        return await agent.consult(task)
                else:
                    return "暂不支持该类型任务。请只询问和按摩、预约相关的问题。"
            else:
                # 根据当前状态继续处理
                if self.state_manager.is_in_appointment_flow():
                    return await self.agent_router.appointment_agent.run(user_input=task)
                elif self.state_manager.is_in_consultation_flow():
                    async with self.agent_router.consultant_agent as agent:
                        return await agent.consult(task)
                
        except Exception as e:
            self.state_manager.force_reset()
            return f"处理任务时发生错误: {str(e)}"
    
    def get_current_state_info(self) -> dict:
        """获取当前处理状态信息"""
        return {
            'current_state': self.state_manager.get_current_state(),
            'state_description': self.state_manager.get_state_description(),
            'available_services': self.agent_router.get_available_services(),
            'can_classify': self.state_manager.should_classify()
        }
    
    def reset_conversation(self) -> None:
        """重置对话状态"""
        self.state_manager.force_reset()
        self.unrelated_handler.reset_reply_rotation()
    
    async def handle_unrelated_request(self, user_input: str, async_mode: bool = True):
        """
        处理无关请求
        
        Args:
            user_input: 用户输入
            async_mode: 是否使用异步模式
            
        Returns:
            异步模式返回AsyncGenerator，同步模式返回str
        """
        if async_mode:
            async for token in self.unrelated_handler.handle_unrelated_async(user_input):
                yield token
        else:
            # 对于同步模式，我们需要单独处理
            result = await self.unrelated_handler.handle_unrelated_sync(user_input)
            yield result
