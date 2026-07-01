"""
用户行为分析器（Agent 驱动版）

核心功能：
1. 分析用户最喜欢的技师
2. 分析用户常用的服务项目和时长
3. 判断用户是否需要回访邀请
4. 生成个性化回访消息（Agent 驱动）

Agent 架构：
- 使用 LLM 生成个性化回访消息
- 使用 LLM 分析用户行为模式
- 保留规则引擎作为 fallback
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
import json


# ==================== Agent Prompt 模板 ====================

RETURN_MESSAGE_GENERATION_PROMPT = """你是一个专业的按摩房客服助手。请根据用户的历史偏好，生成一段温馨、个性化的回访消息。

用户历史信息：
{user_info}

技师信息：
{technician_info}

当前日期：{current_date}

生成要求：
1. 语气温馨、专业，像是在关心老朋友
2. 结合用户的历史偏好（技师、服务项目、时长）
3. 适当提及技师的专长或特点
4. 引导用户预约，但不显得过于推销
5. 字数控制在 60-100 字

返回JSON格式：
{{"message": "生成的回访消息..."}}
"""

PATTERN_ANALYSIS_PROMPT = """你是一个用户行为分析师。请分析以下用户预约数据，发现用户的行为模式和偏好。

用户预约历史：
{appointment_history}

用户基本信息：
{user_profile}

分析要求：
1. 用户的预约频率如何？（高频/偶尔/季节性）
2. 用户有什么明显的偏好？（技师/服务类型/时间）
3. 用户的消费习惯是什么？（提前预约/临时预约）
4. 下一次回访的最佳时机是什么时候？
5. 有什么个性化的推荐可以提升用户满意度？

返回JSON格式：
{{
    "pattern_summary": "用户模式总结",
    "preferences": {{
        "technician_preference": "技师偏好描述",
        "service_preference": "服务偏好描述",
        "time_preference": "时间偏好描述",
        "frequency_pattern": "预约频率模式"
    }},
    "next_best_contact": "最佳联系时间",
    "personalized_tips": ["个性化建议1", "个性化建议2"],
    "engagement_strategy": "用户互动策略"
}}
"""

COLD_START_PROMPT = """你是一个新用户引导专家。新用户还没有预约历史，请生成一段欢迎消息，引导用户完成第一次预约。

当前日期：{current_date}

生成要求：
1. 热情友好，欢迎新用户
2. 简要介绍按摩房的服务特点
3. 引导用户说出预约需求
4. 提供1-2个热门推荐
5. 字数控制在 80 字以内

返回JSON格式：
{{"message": "生成的欢迎消息..."}}
"""


class PatternAnalyzer:
    """
    用户行为分析器（Agent 驱动版）

    支持两种模式：
    - Agent 模式：使用 LLM 生成个性化消息和分析
    - 规则模式：使用统计规则进行分析（fallback）
    """

    def __init__(self, behavior_service=None, llm=None):
        self.behavior_service = behavior_service
        self.llm = llm
        self.logger = logging.getLogger(__name__)

        # Agent 配置
        self._agent_config = {
            'use_llm_for_messages': True,      # 使用 LLM 生成消息
            'use_llm_for_analysis': True,       # 使用 LLM 分析模式
            'min_history_for_llm': 2,          # LLM 分析最小历史数
            'fallback_to_rules': True,          # LLM 失败时 fallback
            'cache_messages': True,             # 缓存消息
            'message_cache_ttl': 3600,         # 消息缓存 TTL（1小时）
        }

        # 消息缓存
        self._message_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def behavior_db(self):
        """为了向后兼容，提供 behavior_db 属性"""
        if hasattr(self, 'behavior_service') and self.behavior_service:
            return self.behavior_service.user_behavior_repo
        else:
            return None

    def analyze_user_preferences(self, user_id: str = "default_user") -> Optional[Dict[str, Any]]:
        """分析用户偏好"""
        try:
            if self.behavior_service:
                appointments = self.behavior_service.get_user_behaviors(
                    user_id=user_id,
                    action_type='appointment'
                )
            else:
                appointments = self.behavior_db.get_user_behaviors(
                    user_id=user_id,
                    action_type='appointment'
                )

            if not appointments:
                return None

            technician_counts = {}
            service_counts = {}
            duration_counts = {}

            for appointment in appointments:
                data = appointment.get('action_data', {})
                tech_id = appointment.get('technician_id')
                if tech_id:
                    technician_counts[tech_id] = technician_counts.get(tech_id, 0) + 1

                service = data.get('project')
                if service:
                    service_counts[service] = service_counts.get(service, 0) + 1

                duration = data.get('duration')
                if duration:
                    duration_counts[duration] = duration_counts.get(duration, 0) + 1

            favorite_technician = max(technician_counts, key=technician_counts.get) if technician_counts else None
            favorite_service = max(service_counts, key=service_counts.get) if service_counts else None
            favorite_duration = max(duration_counts, key=duration_counts.get) if duration_counts else None

            return {
                'favorite_technician_id': favorite_technician,
                'favorite_service': favorite_service,
                'favorite_duration': favorite_duration,
                'total_appointments': len(appointments),
                'last_appointment_date': appointments[0]['created_at'] if appointments else None
            }

        except Exception as e:
            self.logger.error(f"分析用户偏好失败: {str(e)}")
            return None

    def should_send_return_reminder(self, user_id: str = "default_user", days_threshold: int = 30) -> bool:
        """判断是否应该发送回访提醒"""
        try:
            preferences = self.analyze_user_preferences(user_id)
            if not preferences or preferences['total_appointments'] < 2:
                return False

            last_appointment = preferences['last_appointment_date']
            if not last_appointment:
                return False

            if isinstance(last_appointment, str):
                last_appointment = datetime.fromisoformat(last_appointment.replace('Z', '+00:00'))

            days_since_last = (datetime.now() - last_appointment).days
            return days_since_last >= days_threshold

        except Exception as e:
            self.logger.error(f"判断回访提醒失败: {str(e)}")
            return False

    def generate_return_message(self, user_id: str = "default_user") -> Optional[str]:
        """
        生成个性化回访消息（Agent 驱动）

        Args:
            user_id: 用户 ID

        Returns:
            个性化回访消息
        """
        # 检查缓存
        cache_key = f"return_message_{user_id}"
        if self._agent_config['cache_messages'] and cache_key in self._message_cache:
            cached = self._message_cache[cache_key]
            cache_age = (datetime.now() - cached.get('_cached_at', datetime.min)).seconds
            if cache_age < self._agent_config['message_cache_ttl']:
                self.logger.info(f"使用缓存的回访消息，缓存年龄: {cache_age}秒")
                return cached.get('message')

        # 分析用户偏好
        preferences = self.analyze_user_preferences(user_id)

        # 判断是否使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_messages']
            and self.llm is not None
            and preferences is not None
            and preferences.get('total_appointments', 0) >= self._agent_config['min_history_for_llm']
        )

        if should_use_llm:
            message = self._generate_message_with_agent(user_id, preferences)
        else:
            message = self._generate_message_with_rules(user_id, preferences)

        # 缓存结果
        if self._agent_config['cache_messages'] and message:
            self._message_cache[cache_key] = {
                'message': message,
                '_cached_at': datetime.now()
            }

        return message

    def _generate_message_with_agent(
        self,
        user_id: str,
        preferences: Dict[str, Any]
    ) -> Optional[str]:
        """
        使用 Agent（LLM）生成回访消息

        Args:
            user_id: 用户 ID
            preferences: 用户偏好

        Returns:
            LLM 生成的回访消息
        """
        self.logger.info(f"使用 Agent 为用户 {user_id} 生成回访消息")

        try:
            # 获取技师信息
            tech_id = preferences.get('favorite_technician_id')
            service = preferences.get('favorite_service', '按摩')
            duration = preferences.get('favorite_duration', 60)

            technician_info = {}
            if tech_id:
                from db import TechnicianDBRouter
                db = TechnicianDBRouter()
                tech_info = db.get_technician_by_id(tech_id)
                if tech_info:
                    technician_info = {
                        'name': tech_info.get('name', '该技师'),
                        'gender': tech_info.get('gender', ''),
                        'strength': tech_info.get('strength', ''),
                        'experience': tech_info.get('experience', '')
                    }

            # 准备用户信息
            user_info = {
                'total_appointments': preferences.get('total_appointments', 0),
                'favorite_service': service,
                'favorite_duration': duration,
                'last_appointment': str(preferences.get('last_appointment_date', ''))
            }

            # 构建 Prompt
            prompt = RETURN_MESSAGE_GENERATION_PROMPT.format(
                user_info=json.dumps(user_info, ensure_ascii=False),
                technician_info=json.dumps(technician_info, ensure_ascii=False),
                current_date=datetime.now().strftime('%Y年%m月%d日')
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                message = result.get('message', '')
                if message:
                    self.logger.info(f"Agent 生成了回访消息: {message[:50]}...")
                    return message

        except Exception as e:
            self.logger.error(f"Agent 消息生成失败: {e}")

        # Fallback 到规则
        return self._generate_message_with_rules(user_id, preferences)

    def _call_llm_sync(self, prompt: str, temperature: float = 0.7) -> Optional[str]:
        """同步调用 LLM"""
        if not self.llm:
            return None

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._call_llm_async(prompt, temperature)
                    )
                    return future.result(timeout=30)
            else:
                return asyncio.run(self._call_llm_async(prompt, temperature))
        except Exception as e:
            self.logger.error(f"同步 LLM 调用失败: {e}")
            return None

    async def _call_llm_async(self, prompt: str, temperature: float = 0.7) -> Optional[str]:
        """异步调用 LLM"""
        if not self.llm:
            return None

        try:
            if hasattr(self.llm, 'ainvoke'):
                response = await self.llm.ainvoke(prompt)
                return response.content if hasattr(response, 'content') else str(response)
            elif hasattr(self.llm, 'invoke'):
                response = self.llm.invoke(prompt)
                return response.content if hasattr(response, 'content') else str(response)
            else:
                response = await self.llm.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "你是一个专业的按摩房客服助手。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"}
                )
                return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"LLM 调用异常: {e}")
            return None

    def _generate_message_with_rules(
        self,
        user_id: str,
        preferences: Optional[Dict[str, Any]]
    ) -> str:
        """使用规则生成回访消息（fallback）"""
        if not preferences:
            return "您好！好久没见了，要不要预约一个按摩放松一下？"

        tech_id = preferences.get('favorite_technician_id')
        service = preferences.get('favorite_service', '按摩')
        duration = preferences.get('favorite_duration', 60)

        if tech_id:
            from db import TechnicianDBRouter
            db = TechnicianDBRouter()
            tech_info = db.get_technician_by_id(tech_id)
            tech_name = tech_info.get('name', '您偏爱的技师') if tech_info else '您偏爱的技师'

            message = f"您好！{tech_name}最近有空档，您之前很喜欢他/她的{service}服务。"
            if duration:
                message += f"按照您习惯的{duration}分钟，"
            message += "要不要预约一下放松一下？"
        else:
            message = f"您好！好久没见了，要不要预约一个{service}服务放松一下？"
            if duration:
                message += f"按您习惯的{duration}分钟怎么样？"

        return message

    def analyze_patterns_with_agent(self, user_id: str = "default_user") -> Dict[str, Any]:
        """
        使用 Agent 分析用户行为模式

        Args:
            user_id: 用户 ID

        Returns:
            Agent 分析结果
        """
        if not self.llm or not self._agent_config['use_llm_for_analysis']:
            return self._analyze_patterns_with_rules(user_id)

        self.logger.info(f"使用 Agent 分析用户 {user_id} 的行为模式")

        try:
            # 获取用户数据
            preferences = self.analyze_user_preferences(user_id)
            if not preferences:
                return {
                    "pattern": "new_user",
                    "summary": "新用户，暂无历史数据",
                    "_method": "rules"
                }

            # 获取预约历史
            appointments = []
            if self.behavior_service:
                appointments = self.behavior_service.get_user_behaviors(
                    user_id=user_id,
                    action_type='appointment'
                )
            elif self.behavior_db:
                appointments = self.behavior_db.get_user_behaviors(
                    user_id=user_id,
                    action_type='appointment'
                )

            # 准备数据
            appointment_history = []
            for appt in appointments[:20]:  # 限制数量
                appointment_history.append({
                    'service': appt.get('action_data', {}).get('project', ''),
                    'duration': appt.get('action_data', {}).get('duration', ''),
                    'technician_id': appt.get('technician_id', ''),
                    'date': str(appt.get('created_at', ''))
                })

            user_profile = {
                'user_id': user_id,
                'total_appointments': preferences.get('total_appointments', 0),
                'favorite_service': preferences.get('favorite_service', ''),
                'favorite_duration': preferences.get('favorite_duration', '')
            }

            # 构建 Prompt
            prompt = PATTERN_ANALYSIS_PROMPT.format(
                appointment_history=json.dumps(appointment_history, ensure_ascii=False, indent=2),
                user_profile=json.dumps(user_profile, ensure_ascii=False, indent=2)
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                result['_method'] = 'agent'
                return result

        except Exception as e:
            self.logger.error(f"Agent 模式分析失败: {e}")

        # Fallback
        return self._analyze_patterns_with_rules(user_id)

    def _analyze_patterns_with_rules(self, user_id: str) -> Dict[str, Any]:
        """使用规则分析用户模式（fallback）"""
        if self.behavior_service:
            return self.behavior_service.analyze_user_patterns(user_id)
        elif self.behavior_db:
            # 简单实现
            preferences = self.analyze_user_preferences(user_id)
            if not preferences:
                return {"pattern": "new_user", "summary": "新用户"}
            return {
                "pattern": "active_user" if preferences.get('total_appointments', 0) > 2 else "occasional_user",
                "summary": f"用户共预约 {preferences.get('total_appointments', 0)} 次",
                "_method": "rules"
            }
        return {"pattern": "unknown", "summary": "无法分析", "_method": "rules"}

    def generate_cold_start_message(self) -> str:
        """
        为新用户生成欢迎消息（Agent 驱动）

        Returns:
            欢迎消息
        """
        if self.llm and self._agent_config['use_llm_for_messages']:
            try:
                prompt = COLD_START_PROMPT.format(
                    current_date=datetime.now().strftime('%Y年%m月%d日')
                )
                response = self._call_llm_sync(prompt, temperature=0.8)
                if response:
                    result = json.loads(response)
                    message = result.get('message', '')
                    if message:
                        return message
            except Exception as e:
                self.logger.error(f"Agent 欢迎消息生成失败: {e}")

        # Fallback
        return "您好！欢迎光临我们的按摩房！请问有什么可以帮您的？我们可以提供专业的推拿按摩服务。"
