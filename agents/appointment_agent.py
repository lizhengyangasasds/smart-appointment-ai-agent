from dotenv import load_dotenv
import uuid
from langchain_core.chat_history import InMemoryChatMessageHistory
from config.model_provider import create_chat_model
from .appointment import (
    InputParser, 
    TechnicianFinder, 
    AppointmentProcessor, 
    MessageBuilder, 
    AppointmentDatabase
)

load_dotenv()


class AppointmentAgent:
    """
    预约机器人主控制器
    
    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个预约流程
    """
    
    def __init__(self, session_id=None, unrelated_callback=None):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.unrelated_callback = unrelated_callback
        self.state = None
        
        # 初始化LLM
        self.llm = self._initialize_llm()
        
        # 初始化组件
        self.input_parser = InputParser(self.llm)
        self.technician_finder = TechnicianFinder()
        self.message_builder = MessageBuilder()
        self.appointment_database = AppointmentDatabase()
        self.appointment_processor = AppointmentProcessor(
            self.input_parser, 
            self.technician_finder,
            self.message_builder, 
            self.appointment_database,
            self.llm
        )
        
        # 会话管理
        self.chats_by_session_id = {}
        self.chat_history = self._get_chat_history(self.session_id)
        
        # 预约状态
        self.reset()

    def _initialize_llm(self):
        """初始化通用聊天模型"""
        return create_chat_model(temperature=0)

    def _get_chat_history(self, session_id: str) -> InMemoryChatMessageHistory:
        """获取或创建会话历史记录"""
        chat_history = self.chats_by_session_id.get(session_id)
        if chat_history is None:
            chat_history = InMemoryChatMessageHistory()
            self.chats_by_session_id[session_id] = chat_history
        return chat_history
    
    def reset(self):
        """重置预约历史和状态"""
        self.appointment_history = {
            "gender": None,
            "start_time": None,
            "duration": None,
            "project": None,
            "preference": None,
            "technician": None,
            "technician_name": None
        }
        self.finished = False
        self.chat_history.clear()

    def set_shared_state(self, shared_state):
        """设置共享状态"""
        self.state = shared_state

    async def run_stream(self, user_input=None):
        """
        流式处理用户预约请求的主函数
        
        这是整个预约流程的入口点，协调各个组件完成预约
        """
        if user_input is None:
            user_input = input("用户：")
        
        # 1. 解析用户输入（内部 JSON，不向用户流式输出，避免英文字段名暴露在聊天界面）
        ai_content = ""
        for token in self.input_parser.parse_stream(user_input, self.chat_history):
            ai_content += token

        try:
            # 2. 解析AI返回的数据
            data = self.input_parser.parse_data(ai_content)
            self.finished = self.appointment_processor.update_history_from_data(self.appointment_history, data)
            
            # 3. 处理与预约无关的请求
            # 如果正在等待用户确认推荐技师，不要转交给归类机器人
            if data.get("unrelated", False) and not self.appointment_history.get('awaiting_confirmation'):
                # 注意：这里不清空预约历史，保留用户已输入的信息
                # 只设置状态为CLASSIFY，让系统转交给其他机器人处理
                if self.state:
                    from config.constants import StateEnum
                    self.state.value = StateEnum.CLASSIFY
                
                async for token in self.appointment_processor.handle_unrelated_request(
                    user_input, self.unrelated_callback, self.state
                ):
                    yield token
                return
            
            # 4. 处理预约完成的情况
            if self.finished:
                recommendation_pending = False
                async for token in self.appointment_processor.handle_complete_appointment(
                    self.appointment_history, self.session_id
                ):
                    # 检查是否有推荐等待确认
                    if token == "[SIGNAL]recommendation_pending":
                        recommendation_pending = True
                        # 将 finished 设为 False，让预约流程继续
                        self.finished = False
                        continue
                    yield token
                
                # 只有在真正完成预约时才重置状态
                if not recommendation_pending and not self.appointment_history.get('awaiting_confirmation'):
                    self._reset_state_after_appointment()
                return
            
            # 5. 处理信息不完整的情况
            async for token in self.appointment_processor.handle_incomplete_info(data, self.appointment_history):
                yield token
                
        except Exception as e:
            yield self.message_builder.create_parse_error_message()

    def _reset_state_after_appointment(self):
        """预约完成后重置状态"""
        self.reset()
        if self.state:
            from config.constants import StateEnum
            self.state.value = StateEnum.CLASSIFY
