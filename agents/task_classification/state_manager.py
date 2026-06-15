"""
状态管理器 - 专门负责管理对话状态的流转

职责：
1. 维护当前对话状态（CLASSIFY, APPOINTMENT, CONSULT等）
2. 管理状态转换逻辑
3. 提供状态查询和重置功能
4. 确保状态转换的正确性和安全性
"""

from config.constants import SharedState, StateEnum
from typing import Optional


class StateManager:
    """状态管理器 - 管理对话流程中的状态转换"""
    
    def __init__(self, shared_state: Optional[SharedState] = None):
        """
        初始化状态管理器
        
        Args:
            shared_state: 共享状态对象，如果为None则创建新的
        """
        self.state = shared_state or SharedState()
    
    def get_current_state(self) -> StateEnum:
        """获取当前状态"""
        return self.state.value or StateEnum.CLASSIFY
    
    def set_state(self, new_state: StateEnum) -> None:
        """设置新状态"""
        old_state = self.state.value
        self.state.value = new_state
        print(f"状态转换: {old_state} -> {new_state}")
    
    def reset_to_classify(self) -> None:
        """重置状态到分类状态"""
        self.set_state(StateEnum.CLASSIFY)
    
    def should_classify(self) -> bool:
        """判断是否应该进行任务分类"""
        current_state = self.get_current_state()
        return current_state == StateEnum.CLASSIFY or current_state is None
    
    def is_in_appointment_flow(self) -> bool:
        """判断是否在预约流程中"""
        return self.get_current_state() == StateEnum.APPOINTMENT
    
    def is_in_consultation_flow(self) -> bool:
        """判断是否在咨询流程中"""
        return self.get_current_state() == StateEnum.CONSULT
    
    def transition_to_appointment(self) -> None:
        """转换到预约状态"""
        self.set_state(StateEnum.APPOINTMENT)
    
    def transition_to_consultation(self) -> None:
        """转换到咨询状态"""
        self.set_state(StateEnum.CONSULT)
    
    def get_state_description(self) -> str:
        """获取当前状态的描述"""
        state = self.get_current_state()
        descriptions = {
            StateEnum.CLASSIFY: "任务分类状态 - 等待识别用户意图",
            StateEnum.APPOINTMENT: "预约流程状态 - 正在处理预约请求",
            StateEnum.CONSULT: "咨询流程状态 - 正在处理咨询请求"
        }
        return descriptions.get(state, "未知状态")
    
    def can_transition_to(self, target_state: StateEnum) -> bool:
        """检查是否可以转换到目标状态"""
        current_state = self.get_current_state()
        
        # 定义允许的状态转换
        allowed_transitions = {
            StateEnum.CLASSIFY: [StateEnum.APPOINTMENT, StateEnum.CONSULT],
            StateEnum.APPOINTMENT: [StateEnum.CLASSIFY],
            StateEnum.CONSULT: [StateEnum.CLASSIFY]
        }
        
        return target_state in allowed_transitions.get(current_state, [])
    
    def force_reset(self) -> None:
        """强制重置状态（用于错误恢复）"""
        print("强制重置状态到分类状态")
        self.reset_to_classify()
