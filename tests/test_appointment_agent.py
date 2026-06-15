"""
AppointmentAgent 功能测试

测试预约代理的核心功能：
1. 解析用户预约请求 
2. 管理预约状态和信息收集
3. 处理无关请求
4. 完成预约流程
"""

import pytest
import asyncio
from agents.appointment_agent import AppointmentAgent


class TestAppointmentAgentCoreFeatures:
    """测试预约代理核心功能"""
    
    def test_should_extract_user_info_from_natural_language(self):
        """
        测试：预约代理应该能从自然语言中提取预约信息
        
        用户说："我想预约明天下午2点的按摩，女技师"
        应该提取到：
        - 时间: 明天下午2点 
        - 项目: 按摩  
        - 性别偏好: 女
        
        这个测试验证整个自然语言处理流程：
        用户输入 -> LLM处理 -> JSON解析 -> 结果验证
        """
        agent = AppointmentAgent()
        
        user_input = "我想预约明天下午2点的按摩，女技师"
        
        # 使用真实的解析流程：通过LLM处理用户输入
        from langchain_core.chat_history import InMemoryChatMessageHistory
        chat_history = InMemoryChatMessageHistory()
        
        # 模拟流式解析过程，获取LLM的完整响应
        ai_content = ""
        for token in agent.input_parser.parse_stream(user_input, chat_history):
            ai_content += token
        
        # 解析LLM返回的JSON
        result = agent.input_parser.parse_data(ai_content)
        
        # 验证解析结果包含预期信息
        assert result["project"] == "按摩", f"应该提取到按摩项目，但得到：{result['project']}"
        assert result["gender"] == "女", f"应该提取到女技师偏好，但得到：{result['gender']}"
        
        # 验证时间信息（明天下午2点应该被转换为标准格式）
        start_time = result["start_time"]
        assert start_time != "未知", f"应该提取到时间信息，但得到：{start_time}"
        assert "14:00" in start_time, f"时间应该转换为14:00格式，但得到：{start_time}"
        
        # 验证这不是无关请求
        assert result["unrelated"] == False, "预约请求不应该被标记为无关"
    
    def test_should_track_appointment_state_correctly(self):
        """
        测试：预约代理应该正确跟踪预约状态
        
        用户分步骤提供信息：
        1. "我要预约按摩" -> 应该记录项目=按摩，其他为空
        2. "明天下午2点" -> 应该记录时间，保持项目=按摩
        3. "女技师" -> 应该记录性别偏好，保持之前信息
        """
        agent = AppointmentAgent()
        
        # 检查初始状态
        assert agent.appointment_history["project"] is None
        assert agent.appointment_history["start_time"] is None 
        assert agent.appointment_history["gender"] is None
        
        # 这个测试可能会失败，因为我们需要验证状态更新逻辑
        # 但这正是我们要测试的功能是否正确工作
        
        # 模拟第一次输入
        data1 = {"project": "按摩"}
        agent.appointment_processor.update_history_from_data(agent.appointment_history, data1)
        
        assert agent.appointment_history["project"] == "按摩"
        assert agent.appointment_history["start_time"] is None  # 应该保持为空
        
        # 模拟第二次输入  
        data2 = {"start_time": "明天下午2点"}
        agent.appointment_processor.update_history_from_data(agent.appointment_history, data2)
        
        assert agent.appointment_history["project"] == "按摩"  # 应该保持
        assert agent.appointment_history["start_time"] == "明天下午2点"
    
    def test_should_identify_unrelated_requests(self):
        """
        测试：预约代理应该能识别与预约无关的请求
        
        用户说："今天天气怎么样？"
        应该识别为无关请求，而不是尝试解析预约信息
        """
        agent = AppointmentAgent()
        
        unrelated_input = "今天天气怎么样？"
        
        # 使用真实的解析流程：通过LLM处理用户输入
        from langchain_core.chat_history import InMemoryChatMessageHistory
        chat_history = InMemoryChatMessageHistory()
        
        # 模拟流式解析过程，获取LLM的完整响应
        ai_content = ""
        for token in agent.input_parser.parse_stream(unrelated_input, chat_history):
            ai_content += token
        
        # 解析LLM返回的JSON
        result = agent.input_parser.parse_data(ai_content)
        
        # 应该被标记为无关请求
        assert result.get("unrelated", False) == True, f"应该识别为无关请求，但得到：{result}"
    
    def test_should_complete_appointment_when_all_info_collected(self):
        """
        测试：当收集到所有必需信息时，应该完成预约
        
        提供完整信息：时间、项目、性别偏好等
        应该标记 finished=True
        """
        agent = AppointmentAgent()
        
        # 提供完整的预约信息
        complete_data = {
            "start_time": "明天下午2点",
            "project": "按摩", 
            "gender": "女",
            "duration": "60分钟"
        }
        
        # 更新预约历史
        finished = agent.appointment_processor.update_history_from_data(
            agent.appointment_history, 
            complete_data
        )
        
        # 应该标记为完成（这个测试可能会失败，需要检查完成逻辑）
        assert finished == True, f"提供完整信息后应该完成预约，但finished={finished}"
        assert agent.appointment_history["start_time"] == "明天下午2点"
        assert agent.appointment_history["project"] == "按摩"
    
    @pytest.mark.asyncio
    async def test_should_handle_incomplete_info_gracefully(self):
        """
        测试：当信息不完整时，应该引导用户补充
        
        只提供部分信息时，应该询问缺失的信息
        """
        agent = AppointmentAgent()
        
        # 只提供项目，缺少时间等信息
        incomplete_data = {"project": "按摩"}
        
        finished = agent.appointment_processor.update_history_from_data(
            agent.appointment_history,
            incomplete_data  
        )
        
        # 不应该完成预约
        assert finished == False, "信息不完整时不应该完成预约"
        
        # 应该能处理不完整信息（不抛出异常）
        try:
            response_tokens = []
            async for token in agent.appointment_processor.handle_incomplete_info(incomplete_data):
                response_tokens.append(token)
            
            response = "".join(response_tokens)
            
            # 应该包含引导性问题（这个断言可能会失败，但能看到实际输出）
            assert len(response) > 0, "应该返回引导信息"
            assert "时间" in response or "什么时候" in response, f"应该询问时间信息，但得到：{response}"
            
        except Exception as e:
            pytest.fail(f"处理不完整信息时出错：{e}")


class TestAppointmentAgentEdgeCases:
    """测试边界情况和错误处理"""
    
    def test_should_handle_invalid_input(self):
        """
        测试：应该处理无效输入而不崩溃
        """
        agent = AppointmentAgent()
        
        # 测试空输入
        try:
            result = agent.input_parser.parse_data("")
            # 不应该崩溃，应该有某种处理方式
        except Exception as e:
            # 如果抛出异常，至少应该是可预期的异常类型
            assert isinstance(e, (ValueError, TypeError)), f"应该是可预期的异常类型，但得到：{type(e)}"
    
    def test_should_reset_state_properly(self):
        """
        测试：应该正确重置预约状态
        """
        agent = AppointmentAgent()
        
        # 设置一些状态
        agent.appointment_history["project"] = "按摩"
        agent.appointment_history["start_time"] = "明天"
        agent.finished = True
        
        # 重置
        agent.reset()
        
        # 应该回到初始状态
        assert agent.appointment_history["project"] is None
        assert agent.appointment_history["start_time"] is None
        assert agent.finished == False
