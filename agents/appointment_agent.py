from dotenv import load_dotenv
import uuid
import logging
from typing import Dict, Any, List
from langchain_core.chat_history import InMemoryChatMessageHistory
from config.model_provider import create_chat_model
from .appointment import (
    InputParser,
    TechnicianFinder,
    AppointmentProcessor,
    MessageBuilder,
    AppointmentDatabase
)
from .reflection import ReflectionAwareMixin

load_dotenv()


class AppointmentAgent(ReflectionAwareMixin):
    """
    预约机器人主控制器

    职责：
    1. 初始化各个组件
    2. 管理会话状态
    3. 协调整个预约流程
    4. 应用反思洞察优化预约决策（闭环）
    """

    def __init__(self, session_id=None, unrelated_callback=None, reflection_engine=None,
                 semantic_memory=None):
        # 基础设置
        self.session_id = session_id or str(uuid.uuid4())
        self.unrelated_callback = unrelated_callback
        self.state = None
        self.logger = logging.getLogger(__name__)

        # 初始化反思感知（闭环支持）
        ReflectionAwareMixin.__init__(self, reflection_engine=reflection_engine)

        # 初始化LLM
        self.llm = self._initialize_llm()

        # 初始化组件
        self.input_parser = InputParser(self.llm)
        self.technician_finder = TechnicianFinder(llm=self.llm)
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

        # 反思洞察应用状态
        self._insights_applied = False
        self._matching_hints: Dict[str, Any] = {}
        self._avoid_patterns: List[str] = []

        # 语义记忆服务（从外部注入，用于用户画像驱动的技师推荐）
        # 由 ChatHandler 在初始化时传入，AppointmentAgent 本身不持有 DB 依赖
        self.semantic_memory = semantic_memory

    def _enrich_history_from_memory(self) -> None:
        """
        用语义记忆补充 appointment_history，使推荐链路感知用户长期偏好。

        只补充 appointment_history 中尚为空的字段，保留本次会话中
        用户已明确提供的信息不被覆盖。
        """
        h = self.appointment_history
        if not h:
            return

        if not self.semantic_memory:
            return

        prefs = self.semantic_memory.get_preferences(
            session_id=self.session_id,
            user_id=None
        )
        if not prefs:
            return
        if not h.get("technician_name") or h.get("technician_name") == "未知":
            if "preferred_technician" in prefs:
                h["technician_name"] = prefs["preferred_technician"]
                self.logger.debug(f"[Memory] 补充偏好技师: {prefs['preferred_technician']}")

        # 偏好时长
        if not h.get("duration") or h.get("duration") == "未知":
            if "duration_preference" in prefs:
                h["duration"] = prefs["duration_preference"]

        # 偏好项目
        if not h.get("project") or h.get("project") == "未知":
            if "project_preference" in prefs:
                h["project"] = prefs["project_preference"]

        # 偏好性别
        if not h.get("gender") or h.get("gender") == "未知":
            if "technician_gender" in prefs:
                h["gender"] = prefs["technician_gender"]

        # 偏好专长（用户对技师"风格"的描述）
        if not h.get("preference") or h.get("preference") == "未知":
            if "strength_preference" in prefs:
                h["preference"] = prefs["strength_preference"]

    def apply_insights(self, insights: Dict[str, Any]) -> None:
        """
        应用反思洞察到预约决策

        根据反思洞察调整：
        1. 技师匹配策略
        2. 推荐优先级
        3. 避免的问题模式
        """
        self.logger.info("应用反思洞察到预约 Agent")

        # 1. 获取任务特定的洞察
        task_insights = self.get_task_type_insights('appointment')

        # 2. 提取匹配提示
        recommendations = task_insights.get('recommendations', [])
        self._matching_hints = {}

        for rec in recommendations:
            action = rec.get('action', {})
            if action.get('type') == 'matching':
                self._matching_hints = action.get('parameters', {})
                break

        # 3. 提取需要避免的模式
        self._avoid_patterns = []
        bad_cases = task_insights.get('bad_cases', [])

        for bc in bad_cases:
            trigger = bc.get('trigger', {})
            if trigger.get('task_type') == 'appointment':
                pattern_id = bc.get('case_id', bc.get('description', ''))
                self._avoid_patterns.append(pattern_id)

        # 4. 获取推荐策略配置
        strategy_config = self.get_preferred_strategy('appointment_matching')
        if strategy_config:
            self.logger.info(f"应用匹配策略: {strategy_config}")

        self._insights_applied = True
        self.logger.debug(
            f"洞察应用完成: {len(self._matching_hints)} 个匹配提示, "
            f"{len(self._avoid_patterns)} 个避免模式"
        )

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

    async def run_stream(self, user_input=None, memory_context: str = ""):
        """
        流式处理用户预约请求的主函数

        这是整个预约流程的入口点，协调各个组件完成预约

        Args:
            user_input: 用户输入
            memory_context: 从 MemoryManager 注入的外部上下文（对话历史摘要+用户画像）
        """
        if user_input is None:
            user_input = input("用户：")

        # 1. 解析用户输入（内部 JSON，不向用户流式输出，避免英文字段名暴露在聊天界面）
        ai_content = ""
        for token in self.input_parser.parse_stream(user_input, self.chat_history, memory_context):
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
                    user_input, self.unrelated_callback, self.state, memory_context
                ):
                    yield token
                return
            
            # 4. 处理预约完成的情况
            if self.finished:
                # 用语义记忆补充预约历史，使推荐链路感知用户长期偏好
                # 只补充空字段，本次会话信息优先级高于记忆
                self._enrich_history_from_memory()

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

    def validate_technician_recommendation(
        self,
        technician: Dict[str, Any],
        user_preference: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        使用反思洞察验证技师推荐

        Args:
            technician: 推荐的技师信息
            user_preference: 用户偏好

        Returns:
            验证结果 {valid, warnings, adjustments}
        """
        # 构建动作上下文
        action = {
            'type': 'technician_recommendation',
            'technician_id': technician.get('id'),
            'technician_gender': technician.get('gender'),
            'technician_expertise': technician.get('expertise', [])
        }

        context = {
            'user_preference': user_preference,
            'appointment_history': self.appointment_history
        }

        # 使用基类方法验证
        return self.validate_action_against_insights(action, context)

    def adjust_matching_strategy(self, base_candidates: List[Dict]) -> List[Dict]:
        """
        根据反思洞察调整匹配策略

        Args:
            base_candidates: 基础候选技师列表

        Returns:
            调整后的候选列表
        """
        if not self._insights_applied:
            self.apply_insights(self.get_insights())

        # 如果有避免模式，检查并过滤候选
        adjusted = []
        for candidate in base_candidates:
            if self._is_pattern_avoided(candidate):
                self.logger.debug(f"跳过避免的技师: {candidate.get('name')}")
                continue
            adjusted.append(candidate)

        # 如果全部被过滤，返回原列表
        if not adjusted:
            return base_candidates

        # 应用匹配提示调整排序
        if self._matching_hints:
            adjusted = self._apply_matching_hints(adjusted)

        return adjusted

    def _is_pattern_avoided(self, candidate: Dict) -> bool:
        """检查候选是否匹配避免模式"""
        for pattern in self._avoid_patterns:
            # 简单的模式匹配逻辑
            if 'gender_mismatch' in pattern.lower():
                if self.appointment_history.get('gender') == 'female' and candidate.get('gender') == 'male':
                    return True
            if 'overbooked' in pattern.lower():
                if candidate.get('booking_count', 0) > 10:
                    return True

        return False

    def _apply_matching_hints(self, candidates: List[Dict]) -> List[Dict]:
        """应用匹配提示调整排序"""
        hints = self._matching_hints

        if not hints:
            return candidates

        # 根据提示调整权重
        def score_candidate(c: Dict) -> float:
            score = c.get('similarity_score', 0.5)

            # 应用自定义权重
            if 'similarity_weight' in hints:
                score *= hints['similarity_weight']

            if 'experience_weight' in hints:
                experience = c.get('experience_years', 1)
                score += hints['experience_weight'] * experience

            return score

        return sorted(candidates, key=score_candidate, reverse=True)

    def get_reflection_context_for_prompt(self) -> str:
        """
        获取反思上下文用于提示词

        Returns:
            反思上下文文本
        """
        insights = self.get_insights()

        parts = []

        # 添加洞察摘要
        if insights.get('summary'):
            parts.append(insights['summary'])

        # 添加高优先级建议
        recommendations = insights.get('actionable_recommendations', [])
        high_priority = [r for r in recommendations if r.get('priority') == 'high']

        if high_priority:
            tips = [r.get('title', '') for r in high_priority[:2]]
            parts.append(f"建议: {'; '.join(tips)}")

        return '\n'.join(parts) if parts else ""
