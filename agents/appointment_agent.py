from dotenv import load_dotenv
import time
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
from agents.reflection.evaluator import (
    TaskEvaluator,
    AppointmentSaveFailedError,
    UserCancelledError,
    AppointmentTimeoutError,
)

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
        # 闭环 1：把 initial 坏案例注入 InputParser；后续由 _refresh_reflection_context() 持续刷新
        initial_bad_cases = self._initial_bad_cases_for_parser()
        self.input_parser = InputParser(self.llm, reflection_bad_cases=initial_bad_cases)
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

        # 闭环 3：把 immediate 的 high-priority recommendations 也注入 processor（用于 agent system prompt）
        if initial_bad_cases or True:
            self._apply_recommendations_to_processor()

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

        # 任务评估器（在 reflection_engine 存在时共享 evaluation_repo，
        # 否则注入独立 EvaluationRepository，避免反射系统挂了阻塞主流程）
        try:
            from db.repositories.reflection_repository import EvaluationRepository
            self._evaluator = TaskEvaluator(evaluation_repo=EvaluationRepository())
        except Exception as _e:
            self.logger.warning(f"TaskEvaluator 初始化失败（评估将不可用）: {_e}")
            self._evaluator = None

        # 任务评估上下文（在 run_stream 入口置零，出口消费）
        self._task_start_ts: float = 0.0
        self._task_turns: int = 0
        self._task_session_started: bool = False

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

    # ====================================================================
    # 闭环控制器：把反思系统产出的 insights 实时下发到组件
    # ====================================================================

    def _initial_bad_cases_for_parser(self) -> List[Dict[str, Any]]:
        """构造器阶段：从反思引擎读取当前坏案例（用于初始化 InputParser）。

        即使反思引擎未注入或 DB 无记录，也保证返回 list，保持 InputParser.__init__ 一致性。
        """
        try:
            insights = self.get_insights()
        except Exception as e:
            self.logger.debug(f"[Closed-Loop] 初始化时拉取反思洞察失败: {e}")
            return []

        task_insights = insights.get('appointment_insights') or {}
        bad_cases = task_insights.get('bad_cases') or []
        # fallback：从顶层 recent_bad_cases 里筛 appointment
        if not bad_cases:
            bad_cases = [bc for bc in insights.get('recent_bad_cases', [])
                         if bc.get('task_type') == 'appointment']
        return list(bad_cases or [])

    def _apply_recommendations_to_processor(self) -> None:
        """闭环 3：把 high-priority recommendations 注入 AppointmentProcessor.agent_prompt。

        改进点：让"成功提示生成"的 Agent（agent_executor）知道系统级注意事项，
        例如"周末高峰尽量推荐 A 类技师"、"避免给某技师叠加套餐"。
        """
        try:
            insights = self.get_insights()
        except Exception as e:
            self.logger.debug(f"[Closed-Loop] 注入 recommendations 失败: {e}")
            return

        recs = []
        for key in ('actionable_recommendations', 'recommendations'):
            recs.extend(insights.get(key, []) or [])
        # 提取 high priority
        high_priority = [r for r in recs if r.get('priority') == 'high']
        if not high_priority:
            return

        # 拼成一行说明，注入 agent_prompt 的 system message
        lines = []
        for r in high_priority[:3]:
            title = r.get('title') or r.get('action') or ''
            if isinstance(title, dict):
                title = title.get('note') or title.get('description') or str(title)[:50]
            if title:
                lines.append(f"- {str(title)[:80]}")

        if not lines:
            return

        reflection_note = (
            "\n\n【系统级注意事项（来自反思系统，请在生成温馨提示时遵循）】\n"
            + "\n".join(lines)
        )

        try:
            processor = self.appointment_processor
            if processor and hasattr(processor, 'agent_prompt'):
                # 重建 system prompt：保持原有，叠加反思注意事项
                from langchain_core.prompts import ChatPromptTemplate
                base_system = "你是一个智能助手，可以获取天气信息并生成个性化的预约成功提示。"
                processor.agent_prompt = ChatPromptTemplate.from_messages([
                    ("system", base_system + reflection_note),
                    ("human", "{input}"),
                    ("placeholder", "{agent_scratchpad}"),
                ])
                # 重新 bind agent_executor
                from langchain.agents import create_openai_tools_agent, AgentExecutor
                if processor.llm and getattr(processor, 'weather_agent', None):
                    processor.weather_agent = create_openai_tools_agent(
                        processor.llm, processor.tools, processor.agent_prompt
                    )
                    processor.agent_executor = AgentExecutor(
                        agent=processor.weather_agent,
                        tools=processor.tools,
                        verbose=True
                    )
                self.logger.debug(f"[Closed-Loop] 注入 {len(lines)} 条 high-priority recommendations")
        except Exception as e:
            self.logger.warning(f"[Closed-Loop] agent_prompt 注入失败: {e}")

    def refresh_reflection_loop(self) -> None:
        """在 run_stream / handle_complete_appointment 的关键节点调用，
        刷新 BadCases -> InputParser，Patterns -> TechnicianFinder，Recs -> Agent。

        使用 ReflectionAwareMixin 的 5 分钟缓存，多次调用零成本。
        """
        try:
            # 1) 坏案例 -> InputParser
            bad_cases = self._initial_bad_cases_for_parser()
            self.input_parser.update_reflection_bad_cases(bad_cases)

            # 2) 模式 -> TechnicianFinder
            patterns: List[Dict[str, Any]] = []
            insights = self.get_insights()
            for r in insights.get('recent_reflections', []) or []:
                patterns.extend(r.get('patterns_discovered') or [])
            if not patterns:
                top_insights = insights.get('appointment_insights') or {}
                patterns = top_insights.get('patterns_discovered') or insights.get('patterns_discovered', []) or []
            self.technician_finder.set_reflection_patterns(patterns)

            # 3) 推荐 -> agent_prompt（只在内容变化时刷，避免每次重建）
            self._apply_recommendations_to_processor()

            self.logger.debug(
                f"[Closed-Loop] 反思刷新: bad_cases={len(bad_cases)}, patterns={len(patterns)}"
            )
        except Exception as e:
            self.logger.warning(f"[Closed-Loop] 刷新失败: {e}")

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

        评估口径：
            进入时记录 start_ts / turns_count，所有 return 前都会调
            _evaluate_task_outcome() 落 task_evaluations 行。这一步是被动
            记录，不阻塞用户可见流（即使评估写库失败也只 warn）。
        """
        if user_input is None:
            user_input = input("用户：")

        # 闭环控制器：进入流程前刷新 BadCases/Patterns/Recommendations。
        # ReflectionAwareMixin 内部已有 5 分钟缓存，调用本身是常数次查表，不会增加关键路径延迟。
        # 失败时只是 warn，不会中断预约主流程（向后兼容）。
        self.refresh_reflection_loop()

        # 评估通道：开始计时 + 累计轮数
        self._task_start_ts = time.monotonic()
        self._task_turns = self.chat_history.get_num_messages() // 2  # 一个 human+ai 算一轮
        eval_signal: Dict[str, Any] = {"emitted": False}

        def _record_eval(reason: str = "ok") -> None:
            """供 finally 调用：集中评估当前任务并落库。
            多重调用幂等（evaluator 内部按 success_level 判定）。
            """
            if eval_signal["emitted"]:
                return
            eval_signal["emitted"] = True
            try:
                self._evaluate_task_outcome(reason=reason)
            except Exception as ev_err:
                self.logger.warning(f"[Evaluator] 评估落库失败: {ev_err}")

        # 1. 解析用户输入（内部 JSON，不向用户流式输出，避免英文字段名暴露在聊天界面）
        ai_content = ""
        try:
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
                    _record_eval("unrelated")
                    return

                # 4. 处理预约完成的情况
                if self.finished:
                    # 用语义记忆补充预约历史，使推荐链路感知用户长期偏好
                    # 只补充空字段，本次会话信息优先级高于记忆
                    self._enrich_history_from_memory()

                    recommendation_pending = False
                    eval_reason = "ok"  # 默认成功；如果看到 EVAL_FAILED 信号再覆盖
                    async for token in self.appointment_processor.handle_complete_appointment(
                        self.appointment_history, self.session_id
                    ):
                        if token == "[SIGNAL]recommendation_pending":
                            recommendation_pending = True
                            # 将 finished 设为 False，让预约流程继续
                            self.finished = False
                            continue
                        if isinstance(token, str) and token.startswith("[EVAL_OK]"):
                            eval_reason = "ok"
                            continue
                        if isinstance(token, str) and token.startswith("[EVAL_FAILED]"):
                            # 提取 reason= 部分
                            head = token.split("\n", 1)[0]
                            reason = head.replace("[EVAL_FAILED]", "").strip()
                            if reason.startswith("reason="):
                                reason = reason[len("reason="):]
                            eval_reason = reason or "appointment_failed"
                            continue
                        # 把 "[EVAL_OK]" / "[EVAL_FAILED]" 两种 token 移除，不再 yield 给用户
                        if token in ("[EVAL_OK]", "[EVAL_FAILED]"):
                            continue
                        yield token

                    # 只有在真正完成预约时才重置状态
                    if not recommendation_pending and not self.appointment_history.get('awaiting_confirmation'):
                        self._reset_state_after_appointment()
                    # 推荐等待确认时不记评估（任务尚未结束）
                    if not recommendation_pending and not self.appointment_history.get('awaiting_confirmation'):
                        _record_eval(eval_reason)
                    return

                # 5. 处理信息不完整的情况：未结束，记为 in_progress（不算失败，
                #    等用户补齐或下次评估；此处评估为空 PARTIAL/incomplete）
                async for token in self.appointment_processor.handle_incomplete_info(data, self.appointment_history):
                    yield token
                # 这里不调 _record_eval，因为任务尚未结束；
                # 后续每次 run_stream 都会重新开始计时并累计轮数。
                return

            except Exception as e:
                # 解析异常 / 解析后分发异常：业务失败，记 parse_error
                self.logger.warning(f"[Appointment] parse/dispatch 异常: {e}")
                yield self.message_builder.create_parse_error_message()
                _record_eval("parse_error")
                return

        except Exception as outer_e:
            # 外层异常（input_parser.parse_stream 抛错）：LLM 错误，记 llm_error
            self.logger.error(f"[Appointment] run_stream 外层异常: {outer_e}")
            _record_eval("llm_error")
            return

    def _reset_state_after_appointment(self):
        """预约完成后重置状态"""
        self.reset()
        if self.state:
            from config.constants import StateEnum
            self.state.value = StateEnum.CLASSIFY

    # ====================================================================
    # 评估通道：把每一次 run_stream 的结果落到 task_evaluations
    # ====================================================================

    def _evaluate_task_outcome(self, reason: str = "ok") -> None:
        """落一次 task_evaluations 行。

        调用时机：
        - reason='ok'                    预约全流程成功（保存到 DB）
        - reason='slot_unavailable'      找不到技师档期
        - reason='database_error'        save_appointment 返回 False
        - reason='user_cancelled'        用户拒绝推荐技师
        - reason='parse_error'           input_parser.parse_data 异常
        - reason='llm_error'             LLM 调用异常
        - reason='unrelated'             用户问非预约问题（不算失败，记 PARTIAL）

        该函数不抛异常给外层（包 try/except），评估系统挂了不影响预约主流程。
        """
        if not self._evaluator:
            return

        # 完成时间：用 _task_start_ts 算
        completion_time = (
            time.monotonic() - self._task_start_ts
            if self._task_start_ts > 0 else None
        )

        # 把 reason 转成 evaluator 理解的 error
        err_obj: Any = None
        if reason in ('slot_unavailable', 'database_error'):
            err_obj = AppointmentSaveFailedError(reason=reason, message=reason)
        elif reason == 'user_cancelled':
            err_obj = UserCancelledError()
        elif reason == 'parse_error':
            err_obj = ValueError('parse error in appointment')
        elif reason == 'llm_error':
            err_obj = RuntimeError('llm error in appointment')
        elif reason == 'timeout':
            err_obj = AppointmentTimeoutError()

        # evaluator 的 evaluate_appointment_task 是 async def 但函数体内没有 await，
        # 通过 inspect 不阻塞地直接调用即可
        try:
            import asyncio as _asyncio
            coro = self._evaluator.evaluate_appointment_task(
                session_id=self.session_id,
                appointment_history=self.appointment_history,
                turns_count=self._task_turns,
                completion_time=completion_time,
                error=err_obj,
            )
            try:
                loop = _asyncio.get_event_loop()
                if loop.is_running():
                    # 已有 loop 跑着：在线程里同步跑完
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        result = ex.submit(
                            _asyncio.run,
                            coro
                        ).result(timeout=10)
                else:
                    result = loop.run_until_complete(coro)
            except RuntimeError:
                result = _asyncio.run(coro)

            self.logger.debug(
                f"[Evaluator] 落库 ok reason={reason} "
                f"success_level={result.get('success_level')} "
                f"success_rate={result.get('success_rate')}"
            )
        except Exception as e:
            self.logger.warning(f"[Evaluator] 调用失败: {e}")

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
