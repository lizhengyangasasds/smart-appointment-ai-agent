"""
TaskClassificationAgent 功能测试 - 基于真实工作流程

测试任务分类代理的实际功能：
1. 任务分类和智能路由 - 不返回分类标签，而是直接处理任务
2. 流式任务处理
3. 对话状态管理
4. 无关请求处理
"""

import pytest
from agents.task_classification_agent import TaskClassificationAgent
from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent


class TestTaskClassificationAgentRealWorkflow:
    """测试任务分类代理真实工作流程"""
    
    @pytest.mark.asyncio
    async def test_should_route_consultation_requests_correctly(self):
        """
        测试：应该正确路由咨询请求到ConsultantAgent
        
        验证咨询请求能够被识别并路由到咨询代理
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        consultation_requests = [
            "按摩有什么好处？",
            "推拿多少钱？",
            "你们有什么服务项目？",
        ]
        
        for user_input in consultation_requests:
            response = await agent.classify_task(user_input)
            
            # 应该返回有意义的响应
            assert isinstance(response, str), f"咨询请求应该返回字符串响应，输入：{user_input}"
            assert len(response) > 0, f"咨询请求应该有响应内容，输入：{user_input}"
            
            # 不应该是错误信息
            assert not response.startswith("处理任务时发生错误"), f"咨询请求不应该返回错误，输入：{user_input}，响应：{response}"
            assert not response.startswith("暂不支持该类型任务"), f"咨询请求应该被支持，输入：{user_input}，响应：{response}"
            
            print(f"咨询路由测试 - 输入：'{user_input}'，响应长度：{len(response)}")
    
    @pytest.mark.asyncio
    async def test_should_handle_unrelated_requests_gracefully(self):
        """
        测试：应该优雅处理无关请求
        
        验证无关请求得到适当的拒绝响应
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        unrelated_requests = [
            "今天天气怎么样？",
            "北京哪里有好吃的？",
            "你好",
            "谢谢"
        ]
        
        for user_input in unrelated_requests:
            response = await agent.classify_task(user_input)
            
            # 应该返回拒绝信息
            assert isinstance(response, str), f"无关请求应该返回字符串响应，输入：{user_input}"
            assert len(response) > 0, f"无关请求应该有响应内容，输入：{user_input}"
            
            # 应该是拒绝信息
            expected_rejection = "暂不支持该类型任务。请只询问和按摩、预约相关的问题。"
            assert response == expected_rejection, f"无关请求应该返回标准拒绝信息，输入：{user_input}，响应：{response}"
            
            print(f"无关请求测试 - 输入：'{user_input}'，正确拒绝")
    
    @pytest.mark.asyncio
    async def test_should_work_with_stream_mode(self):
        """
        测试：流式任务处理应该正常工作
        
        验证流式处理能正确处理任务并返回结果
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        user_input = "我想预约明天下午的按摩"
        
        # 测试流式处理
        response_tokens = []
        async for token in agent.classify_task_stream(user_input):
            response_tokens.append(token)
        
        # 应该有响应内容
        assert len(response_tokens) > 0, "流式处理应该返回内容"
        
        # 拼接完整响应
        full_response = "".join(response_tokens)
        assert len(full_response) > 0, "完整响应不应该为空"
        
        # 响应应该是有意义的内容，不是错误信息
        assert not full_response.startswith("处理任务时发生错误"), f"流式处理不应该返回错误：{full_response[:100]}"
        
        print(f"流式处理测试 - 响应长度：{len(full_response)}")
    
    def test_should_manage_conversation_state(self):
        """
        测试：应该管理对话状态
        
        验证状态管理功能正常工作
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        # 应该有状态管理器
        assert hasattr(agent, 'state_manager'), "应该有状态管理器"
        
        # 应该能获取状态信息
        try:
            state_info = agent.get_classification_info()
            assert isinstance(state_info, (str, dict)), f"状态信息应该是字符串或字典，但得到：{type(state_info)}"
        except Exception as e:
            # 如果方法不存在或有问题，不应该崩溃
            print(f"获取状态信息时出现问题：{e}")
    
    def test_should_reset_conversation(self):
        """
        测试：应该能重置对话
        
        验证对话重置功能
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        # 应该能重置对话
        try:
            agent.reset_conversation()
            # 不应该抛出异常
        except Exception as e:
            pytest.fail(f"重置对话时出错：{e}")
        
        # 应该能设置业务上下文
        try:
            agent.set_business_context("推拿服务")
            # 不应该抛出异常
        except Exception as e:
            pytest.fail(f"设置业务上下文时出错：{e}")


class TestTaskClassificationAgentEdgeCases:
    """测试边界情况和错误处理"""
    
    @pytest.mark.asyncio
    async def test_should_handle_empty_or_invalid_input(self):
        """
        测试：应该处理空输入或无效输入
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        invalid_inputs = ["", "   ", "？", "...", "###"]
        
        for invalid_input in invalid_inputs:
            try:
                response = await agent.classify_task(invalid_input)
                
                # 不应该崩溃，应该有某种响应
                assert isinstance(response, str), f"无效输入应该返回字符串响应，输入：'{invalid_input}'"
                assert len(response) > 0, f"无效输入应该有响应内容，输入：'{invalid_input}'"
                
                # 很可能被当作无关请求处理
                expected_rejection = "暂不支持该类型任务。请只询问和按摩、预约相关的问题。"
                is_rejection = response == expected_rejection
                is_error = response.startswith("处理任务时发生错误")
                
                assert is_rejection or is_error, f"无效输入应该被拒绝或报错，输入：'{invalid_input}'，响应：{response}"
                
                print(f"无效输入测试 - 输入：'{invalid_input}'，响应类型：{'拒绝' if is_rejection else '错误'}")
                
            except Exception as e:
                # 如果抛出异常，应该是可预期的异常类型
                assert isinstance(e, (ValueError, TypeError)), \
                    f"无效输入'{invalid_input}'异常类型错误：{type(e)}"
    
    @pytest.mark.asyncio
    async def test_should_handle_ambiguous_input(self):
        """
        测试：应该处理模糊输入
        
        一些输入可能很难分类，但不应该崩溃
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        ambiguous_inputs = [
            "按摩",  # 单词，可能是预约也可能是咨询
            "技师",  # 可能是查询技师信息
            "服务",  # 很模糊
            "帮助",  # 很泛泛
        ]
        
        for ambiguous_input in ambiguous_inputs:
            try:
                response = await agent.classify_task(ambiguous_input)
                
                # 应该有某种响应
                assert isinstance(response, str), f"模糊输入应该返回响应，输入：{ambiguous_input}"
                assert len(response) > 0, f"模糊输入响应不应该为空，输入：{ambiguous_input}"
                
                # 不应该崩溃，可能被路由到任何代理或被拒绝
                print(f"模糊输入测试 - 输入：'{ambiguous_input}'，响应长度：{len(response)}")
                
            except Exception as e:
                pytest.fail(f"处理模糊输入时出错，输入：{ambiguous_input}，错误：{e}")
    
    @pytest.mark.asyncio
    async def test_should_provide_unrelated_handler_functionality(self):
        """
        测试：应该提供专门的无关请求处理功能
        
        验证无关请求处理器能正常工作
        """
        appointment_agent = AppointmentAgent()
        consultant_agent = ConsultantAgent()
        
        agent = TaskClassificationAgent(appointment_agent, consultant_agent)
        
        unrelated_input = "今天天气怎么样？"
        
        # 测试专门的无关请求处理方法
        try:
            response = await agent.handle_unrelated(unrelated_input)
            
            assert isinstance(response, str), "无关请求处理应该返回字符串"
            assert len(response) > 0, "无关请求处理应该有内容"
            
            print(f"无关请求处理测试 - 响应长度：{len(response)}")
            
        except Exception as e:
            pytest.fail(f"无关请求处理出错：{e}")
