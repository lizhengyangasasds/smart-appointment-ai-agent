"""
预约处理器

负责协调整个预约流程
"""

import os
import json
import asyncio
import aiohttp
from typing import Dict, Any, AsyncGenerator
from .input_parser import InputParser
from .technician_finder import TechnicianFinder
from .message_builder import MessageBuilder
from .appointment_database import AppointmentDatabase
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.tools import BaseTool
from langchain_core.prompts import ChatPromptTemplate


class WeatherMCPTool(BaseTool):
    """Weather API 工具"""
    name: str = "get_current_weather"
    description: str = "获取指定城市的当前天气信息"
    
    def __init__(self):
        super().__init__()
        # 将 API key 和 URL 定义为类属性而不是实例属性
        self._api_key = os.getenv("OPENWEATHER_API_KEY")
        self._base_url = "https://api.openweathermap.org/data/2.5/weather"
    
    async def _get_weather_data(self, city: str = "Beijing") -> str:
        """异步获取天气数据"""
        if not self._api_key:
            return "北京今天天气晴朗，温度适宜，建议您注意防晒"
        
        try:
            params = {
                "q": city,
                "appid": self._api_key,
                "units": "metric",
                "lang": "zh_cn"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self._base_url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # 提取天气信息
                        temp = data["main"]["temp"]
                        feels_like = data["main"]["feels_like"]
                        description = data["weather"][0]["description"]
                        humidity = data["main"]["humidity"]
                        wind_speed = data.get("wind", {}).get("speed", 0)
                        
                        return f"北京当前天气：{description}，气温{temp}°C（体感{feels_like}°C），湿度{humidity}%，风速{wind_speed}m/s"
                    else:
                        return "北京今天天气晴朗，温度适宜，建议您出行注意防晒"
        except Exception as e:
            return f"北京今天天气宜人，温度适中，适合出行"
    
    def _run(self, city: str = "Beijing") -> str:
        """同步版本 - 不推荐使用"""
        return asyncio.run(self._get_weather_data(city))
    
    async def _arun(self, city: str = "Beijing") -> str:
        """异步版本"""
        return await self._get_weather_data(city)


class AppointmentProcessor:
    """预约处理器"""
    
    def __init__(self, input_parser: InputParser, technician_finder: TechnicianFinder,
                 message_builder: MessageBuilder, appointment_database: AppointmentDatabase, llm=None):
        self.input_parser = input_parser
        self.technician_finder = technician_finder
        self.message_builder = message_builder
        self.appointment_database = appointment_database
        self.llm = llm
        
        # 初始化天气工具和 agent
        if self.llm:
            self.weather_tool = WeatherMCPTool()
            self.tools = [self.weather_tool]
            
            # 创建 agent prompt
            self.agent_prompt = ChatPromptTemplate.from_messages([
                ("system", "你是一个智能助手，可以获取天气信息并生成个性化的预约成功提示。"),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}"),
            ])
            
            # 创建 agent
            self.weather_agent = create_openai_tools_agent(self.llm, self.tools, self.agent_prompt)
            self.agent_executor = AgentExecutor(agent=self.weather_agent, tools=self.tools, verbose=True)
    
    def update_history_from_data(self, appointment_history: Dict[str, Any], data: Dict[str, Any]) -> bool:
        """从解析数据更新预约历史"""
        # 检查是否在等待用户确认推荐技师
        if appointment_history.get('awaiting_confirmation'):
            return self._handle_recommendation_response(appointment_history, data)

        # 只更新有值的字段，避免覆盖之前的信息
        for key in ["duration", "gender", "start_time", "project", "technician_name"]:
            if data.get(key) and data[key] != "未知":
                appointment_history[key] = data[key]

        # preference特殊处理
        if data.get("preference") and data["preference"] != "未知":
            appointment_history["preference"] = data["preference"]

        # 检查是否收集齐所有必需信息
        # 必需信息：时间、项目、时长
        # 如果指定了"真实技师名"，则不需要性别；否则性别也是必需的
        # 注意：technician_name 可能被 LLM 误填为描述性短语（如"手劲大的女技师"），
        # 这种情况下应按"未指定技师"处理，让 gender 仍为必填。
        required_fields = ["start_time", "project", "duration"]
        technician_name = appointment_history.get("technician_name")

        # 判断是否为真实姓名（避免被描述性短语误导）
        real_name_provided = bool(
            technician_name
            and technician_name != "未知"
            and self._looks_like_real_name(technician_name)
        )

        if not real_name_provided:
            # 没有指定真实姓名，需要性别来筛选
            required_fields.append("gender")

        has_all_required = all(
            appointment_history.get(field) and appointment_history[field] != "未知"
            for field in required_fields
        )

        # 如果信息完整，但是指定了真实姓名且不可用，则进入推荐流程
        if has_all_required and real_name_provided:
            # 检查指定技师是否可用，如果不可用则进入推荐流程
            # 这个检查留到 handle_complete_appointment 中进行
            pass

        return has_all_required

    @staticmethod
    def _looks_like_real_name(name: str) -> bool:
        """
        静态包装：复用 TechnicianFinder 的姓名校验，避免描述性短语被误识别为技师名。
        """
        try:
            from .technician_finder import _looks_like_real_name as _check
            return _check(name)
        except Exception:
            # 兜底：粗略规则
            if not name or not isinstance(name, str):
                return False
            return 2 <= len(name.strip()) <= 4 and all('\u4e00' <= ch <= '\u9fff' for ch in name.strip())

    def _handle_recommendation_response(self, appointment_history: Dict[str, Any], data: Dict[str, Any]) -> bool:
        """处理用户对推荐技师的回应"""
        user_response = data.get('confirmation', '').lower()
        
        # 判断用户是否同意推荐
        positive_responses = ['是', '好', '可以', '同意', '确定', 'yes', 'ok', '行']
        negative_responses = ['不', '不要', '不行', '不同意', '换', 'no']
        
        is_positive = any(pos in user_response for pos in positive_responses)
        is_negative = any(neg in user_response for neg in negative_responses)
        
        if is_positive and not is_negative:
            # 用户同意推荐，更新技师信息
            recommended_tech = appointment_history.get('recommended_technician')
            if recommended_tech:
                appointment_history['confirmed_technician'] = recommended_tech
                appointment_history['awaiting_confirmation'] = False
                return True  # 表示可以进行预约
        elif is_negative:
            # 用户拒绝推荐
            appointment_history['recommendation_declined'] = True
            appointment_history['awaiting_confirmation'] = False
            return True  # 表示需要处理拒绝情况
        
        # 用户回应不明确，继续等待
        # 这里返回 False，表示信息还不完整，需要继续等待用户输入
        return False
    
    # 兜底：在预约机器人内检测用户输入是否含"咨询/价格/项目/技师介绍"等
    # 关键词，若有则跳过归类机器人，直接让步给咨询机器人。
    # （这是 Q4 修复的 appointment 端兜底，配合 agent_router._CROSS_BUSINESS_KEYWORDS 双向补齐）
    _CONSULT_FALLBACK_KEYWORDS = (
        "咨询", "问问", "想问", "了解", "介绍一下", "介绍下",
        "价格", "多少钱", "怎么收费",
        "项目", "套餐", "有什么服务",
        "营业时间", "几点开门", "几点关门",
        "地址", "在哪", "在哪里",
        "功效", "有什么用", "适合",
    )

    async def handle_unrelated_request(
        self,
        user_input: str,
        unrelated_callback,
        state,
        memory_context: str = "",
    ) -> AsyncGenerator[str, None]:
        """处理与预约无关的请求

        ⚠️ 关键：unrelated_callback 是 async 函数，同步调用拿到的是 coroutine，
        必须 await 拿到结果。如果它返回字符串（直接 yield）或 async gen（yield token），
        都要正确处理。历史上这里直接 yield coroutine 对象导致 callback 永不执行。

        兜底分支：当本实例注入了 consultant_fallback_callback，且用户输入命中
        咨询关键词时，直接让步给咨询机器人，避免被归类机器人"无法处理"拒掉。
        """
        import inspect
        # appointment 端兜底：判断用户是不是其实想咨询
        if getattr(self, 'consultant_fallback_callback', None):
            if any(kw in (user_input or "") for kw in self._CONSULT_FALLBACK_KEYWORDS):
                yield "[THOUGHT][预约机器人]检测到用户输入为咨询类问题，让步给咨询机器人处理\n"
                yield "[REPLY][预约机器人]您的问题更像是咨询服务，让我为您转接到咨询机器人。\n"
                yield "[CONSULT_FALLBACK]"
                try:
                    result = self.consultant_fallback_callback(user_input, memory_context)
                    if inspect.iscoroutine(result):
                        result = await result
                    if hasattr(result, '__aiter__'):
                        async for token in result:
                            yield token
                    elif result:
                        yield result
                    return
                except Exception as e:
                    print(f"consultant_fallback_callback 失败: {e}")
                    yield f"[ERROR]转接咨询时发生错误: {str(e)}\n"
                    # 不立即兜底到归类机器人，下面继续走归类兜底

        if unrelated_callback:
            try:
                yield "[REPLY][预约机器人]和预约信息无关，已交给归类机器人处理\n"
                result = unrelated_callback(user_input, memory_context)
                if inspect.iscoroutine(result):
                    result = await result
                if hasattr(result, '__aiter__'):
                    async for token in result:
                        yield token
                else:
                    yield result
            except Exception as e:
                yield f"[ERROR]处理请求时发生错误: {str(e)}\n"
                yield self.message_builder.create_unrelated_message()
        else:
            yield self.message_builder.create_unrelated_message()
    
    async def handle_complete_appointment(self, appointment_history: Dict[str, Any],
                                        session_id: str) -> AsyncGenerator[str, None]:
        """处理预约信息完整的情况"""
        # 检查是否用户拒绝了推荐 → 业务失败信号，agent 端会写 FAILED(user_cancelled)
        if appointment_history.get('recommendation_declined'):
            reply = self.message_builder.create_recommendation_declined_message(self.llm)
            yield f"[REPLY][预约机器人]{reply}"
            yield "[EVAL_FAILED]reason=user_cancelled"
            # 清理状态
            appointment_history.pop('recommendation_declined', None)
            appointment_history.pop('recommended_technician', None)
            appointment_history.pop('original_technician', None)
            return

        # 检查是否用户确认了推荐技师
        if appointment_history.get('confirmed_technician'):
            tech = appointment_history['confirmed_technician']
            # 标记为推荐技师用于成功消息显示
            tech['is_recommendation'] = True
            tech['original_technician'] = appointment_history.get('original_technician')
            result = await self._process_successful_appointment(tech, appointment_history, session_id)
            # _process_successful_appointment 已经返回带 [EVAL_OK]/[EVAL_FAILED] 前缀
            yield f"[REPLY][预约机器人]{result}"
            # 清理状态
            appointment_history.pop('confirmed_technician', None)
            appointment_history.pop('recommended_technician', None)
            appointment_history.pop('original_technician', None)
            return

        # 检查是否在等待用户确认推荐技师
        if appointment_history.get('awaiting_confirmation'):
            # 用户回应不明确，重新询问（不算失败，继续等待）
            yield f"[REPLY][预约机器人]\n机器人：请您明确回复\"是\"或\"不\"，我好为您安排预约。\n"
            return

        # 收集思考过程
        thought_msgs = []
        def collect_thoughts(msg):
            thought_msgs.append(msg)

        tech = self.technician_finder.find_technician_with_thought(appointment_history, collect_thoughts)

        # 输出所有思考过程
        for msg in thought_msgs:
            yield msg

        technician_name = appointment_history.get("technician_name")

        if tech:
            # 检查是否是需要确认的推荐
            if tech.get('requires_confirmation'):
                original_tech = tech.get('original_technician')
                recommended_tech = tech.get('recommended_technician')

                # 生成推荐消息
                recommendation_msg = self.message_builder.create_technician_recommendation_message(
                    original_tech, recommended_tech, appointment_history, self.llm
                )
                yield f"[REPLY][预约机器人]{recommendation_msg}"

                # 将推荐信息存储在预约历史中，等待用户确认
                appointment_history['recommended_technician'] = recommended_tech
                appointment_history['original_technician'] = original_tech
                appointment_history['awaiting_confirmation'] = True

                # 重要：告诉调用方这个预约还没有真正完成，需要继续等待用户输入
                yield "[SIGNAL]recommendation_pending"
                return
            else:
                # 正常预约流程
                result = await self._process_successful_appointment(tech, appointment_history, session_id)
                yield f"[REPLY][预约机器人]{result}"
        else:
            # 找不到任何技师档期：业务失败信号
            # 关键：调用前再校验一次 technician_name，避免 LLM 误填的服务项目名
            # （如"按摩服务"）进数据库查不到、抛出"未找到名为X的技师"的语义矛盾回复。
            # 校验失败或为"未知"时，传 None 让 message_builder 走通用"没合适技师"分支。
            from agents.appointment.input_parser import InputParser as _IP
            if technician_name and technician_name != "未知" and not _IP._looks_like_real_name(technician_name):
                # 同步把 appointment_history 也清掉，避免后续链路再读到脏数据
                appointment_history["technician_name"] = "未知"
                technician_name = None
            reply = self.message_builder.create_appointment_failure_message(technician_name)
            yield f"[REPLY][预约机器人]{reply}"
            yield "[EVAL_FAILED]reason=slot_unavailable"
    
    async def _process_successful_appointment(self, tech: Dict[str, Any],
                                          appointment_history: Dict[str, Any], session_id: str) -> str:
        """处理预约成功的情况，并结合北京天气生成温馨提示

        返回值约定：appointment_agent 会解析首行前缀以决定评估结果：
          "[EVAL_OK]<正文>"           —— 保存成功，agent 写入 SUCCESS 评估
          "[EVAL_FAILED]reason=...<失败消息>" —— 保存失败，agent 写入 FAILED 评估
        """
        start_time, end_time, duration_min = self.technician_finder.parse_time_and_duration(
            appointment_history["start_time"],
            appointment_history["duration"]
        )
        # 保存预约到数据库
        success = self.appointment_database.save_appointment(
            tech["id"], start_time, end_time, appointment_history, session_id
        )
        if success:
            # 更新内存中的忙碌时段
            self.appointment_database.update_memory_schedule(tech["id"], start_time, end_time)
            # 使用 LLM agent 生成结合北京天气的温馨提示
            if self.llm and hasattr(self, 'agent_executor'):
                prompt = f"请获取北京今天的天气信息，然后结合天气情况为用户生成一段温馨的预约成功提示。技师姓名：{tech['name']}，性别：{tech['gender']}。请根据天气给出合适的建议和关怀。"
                try:
                    result = await self.agent_executor.ainvoke({"input": prompt})
                    agent_output = result.get("output", "")
                    body = f"\n机器人：已为您预约技师：{tech['name']}，性别：{tech['gender']}。预约成功！\n{agent_output}\n"
                    return f"[EVAL_OK]\n{body}"
                except Exception as e:
                    print(f"Agent调用失败: {e}")
                    return f"[EVAL_OK]\n{self.message_builder.create_appointment_success_message(tech)}"
            else:
                return f"[EVAL_OK]\n{self.message_builder.create_appointment_success_message(tech)}"
        else:
            # save_appointment 返回 False：让 agent 写入 FAILED 评估
            # reason=database_error 是默认值；上游可以通过 appointment_database.save_appointment
            # 抛 AppointmentSaveFailedError(reason='slot_unavailable') 提供更精确的原因
            return f"[EVAL_FAILED]reason=database_error\n{self.message_builder.create_save_failure_message()}"
    
    async def handle_incomplete_info(self, data: Dict[str, Any], appointment_history: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """处理信息不完整的情况"""
        # 确定缺失的信息
        missing = []
        technician_name = appointment_history.get("technician_name")
        
        # 基本必需信息
        if not appointment_history.get("start_time") or appointment_history.get("start_time") == "未知":
            missing.append("start_time")
        if not appointment_history.get("project") or appointment_history.get("project") == "未知":
            missing.append("project")
        if not appointment_history.get("duration") or appointment_history.get("duration") == "未知":
            missing.append("duration")
        
        # 如果没有指定技师名，则需要性别
        if not technician_name or technician_name == "未知":
            if not appointment_history.get("gender") or appointment_history.get("gender") == "未知":
                missing.append("gender")
        
        reply = self.message_builder.create_missing_info_questions(missing)
        yield f"[THOUGHT][预约机器人]用户的预约信息不完整，缺少：{', '.join(missing)}，我需要询问用户补充这些信息"
        yield f"[REPLY][预约机器人]{reply}"
