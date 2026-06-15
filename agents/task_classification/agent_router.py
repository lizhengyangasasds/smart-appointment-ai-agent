"""
智能体路由器 - 专门负责根据分类结果将请求路由到对应的Agent

职责：
1. 接收分类结果，决定使用哪个Agent处理
2. 协调各个Agent之间的调用
3. 管理Agent的初始化和状态同步
4. 提供统一的Agent调用接口
"""

from typing import Any, AsyncGenerator
from .state_manager import StateManager


class AgentRouter:
    """智能体路由器 - 根据任务类型路由到对应的处理Agent"""
    
    def __init__(self, appointment_agent: Any, consultant_agent: Any, state_manager: StateManager):
        """
        初始化路由器
        
        Args:
            appointment_agent: 预约处理Agent
            consultant_agent: 咨询处理Agent
            state_manager: 状态管理器
        """
        self.appointment_agent = appointment_agent
        self.consultant_agent = consultant_agent
        self.state_manager = state_manager
        
        # 设置Agent的共享状态
        self._setup_agent_states()
    
    def _setup_agent_states(self):
        """设置各Agent的共享状态"""
        if self.appointment_agent and hasattr(self.appointment_agent, 'set_shared_state'):
            self.appointment_agent.set_shared_state(self.state_manager.state)
        
        if self.consultant_agent and hasattr(self.consultant_agent, 'set_shared_state'):
            self.consultant_agent.set_shared_state(self.state_manager.state)
    
    async def route_to_appointment(self, task: str) -> AsyncGenerator[str, None]:
        """
        路由到预约Agent处理
        
        Args:
            task: 用户任务内容
            
        Yields:
            str: 流式响应内容
        """
        if not self.appointment_agent:
            yield "[ERROR]预约服务暂时不可用"
            return
        
        # 转换状态
        self.state_manager.transition_to_appointment()
        
        # 生成思考提示
        yield "[THOUGHT][归类机器人] 归类机器人：我发现这是一个预约任务，我将转给预约机器人处理。"
        
        # 调用预约Agent
        try:
            async for token in self.appointment_agent.run_stream(user_input=task):
                yield token
        except Exception as e:
            yield f"[ERROR]预约处理失败: {str(e)}"
            self.state_manager.reset_to_classify()
    
    async def route_to_consultation(self, task: str) -> AsyncGenerator[str, None]:
        """
        路由到咨询Agent处理
        
        Args:
            task: 用户任务内容
            
        Yields:
            str: 流式响应内容
        """
        if not self.consultant_agent:
            yield "[ERROR]咨询服务暂时不可用"
            return
        
        # 转换状态
        self.state_manager.transition_to_consultation()
        
        # 生成思考提示
        yield "[THOUGHT][归类机器人] 归类机器人：我发现这是一个咨询任务，我将转给咨询机器人处理。"
        
        # 调用咨询Agent
        try:
            async with self.consultant_agent as agent:
                async for token in agent.consult_stream(task):
                    yield token
        except Exception as e:
            yield f"[ERROR]咨询处理失败: {str(e)}"
            self.state_manager.reset_to_classify()
    
    async def handle_unsupported_task(self, category: str) -> AsyncGenerator[str, None]:
        """
        处理不支持的任务类型
        
        Args:
            category: 任务分类结果
            
        Yields:
            str: 回复内容
        """
        reply = "暂不支持该类型任务。请只询问和按摩、预约相关的问题。"
        yield "[REPLY][归类机器人]"
        for char in reply:
            yield char
    
    async def route_by_state(self, task: str) -> AsyncGenerator[str, None]:
        """
        根据当前状态路由任务（用于状态持续的场景）
        
        Args:
            task: 用户任务内容
            
        Yields:
            str: 流式响应内容
        """
        if self.state_manager.is_in_appointment_flow():
            async for token in self.appointment_agent.run_stream(user_input=task):
                yield token
        elif self.state_manager.is_in_consultation_flow():
            async with self.consultant_agent as agent:
                async for token in agent.consult_stream(task):
                    yield token
        else:
            # 状态异常，重置并提示
            self.state_manager.reset_to_classify()
            yield "[ERROR]会话状态异常，已重置。请重新开始对话。"
    
    def get_available_services(self) -> list:
        """获取可用的服务列表"""
        services = []
        if self.appointment_agent:
            services.append("预约服务")
        if self.consultant_agent:
            services.append("咨询服务")
        return services
