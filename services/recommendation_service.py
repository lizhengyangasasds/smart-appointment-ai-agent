"""
推荐调度服务（Agent 驱动版）

职责：
1. 定时生成用户行为推荐（Agent 驱动）
2. 管理推荐调度任务
3. 提供手动触发推荐功能

Agent 架构：
- 使用 LLM 分析用户数据生成个性化推荐
- 使用 LLM 决定最佳推荐时机和内容
- 保留规则引擎作为 fallback
"""

import asyncio
import schedule
import time
import threading
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging


# ==================== Agent Prompt 模板 ====================

RECOMMENDATION_GENERATION_PROMPT = """你是一个专业的按摩房营销顾问。请根据用户数据，生成个性化的服务推荐。

用户数据：
{user_data}

当前日期：{current_date}

推荐场景：{scenario}

生成要求：
1. 推荐应该符合用户的历史偏好
2. 考虑用户的预约频率和时间模式
3. 推荐应该有时效性（结合当前日期、节日、季节）
4. 提供具体的推荐理由
5. 如果有新技师或优惠，也应该考虑

返回JSON格式：
{{
    "recommendations": [
        {{
            "type": "recommendation_type",
            "title": "推荐标题",
            "content": "推荐内容描述",
            "reason": "推荐理由",
            "urgency": "high/medium/low",
            "action": "建议的行动"
        }}
    ],
    "best_contact_time": "最佳联系时间",
    "message_tone": "消息语气建议",
    "summary": "推荐总结"
}}
"""

USER_SEGMENTATION_PROMPT = """你是一个用户分群专家。请根据用户的行为数据，将用户分群并给出针对性的营销策略。

用户行为数据：
{behavior_data}

当前日期：{current_date}

分析要求：
1. 将用户分成不同的群体（如：高价值用户、沉睡用户、潜在用户等）
2. 为每个群体制定针对性的营销策略
3. 识别最有价值的推荐时机
4. 发现潜在的流失风险用户

返回JSON格式：
{{
    "segments": [
        {{
            "name": "用户群体名称",
            "description": "群体特征描述",
            "count": 用户数量,
            "characteristics": ["特征1", "特征2"],
            "marketing_strategy": "营销策略描述",
            "retention_priority": "high/medium/low"
        }}
    ],
    "at_risk_users": ["有流失风险的用户ID列表"],
    "high_value_users": ["高价值用户ID列表"],
    "cold_start_users": ["需要激活的新用户ID列表"],
    "overall_insights": ["整体洞察1", "整体洞察2"]
}}
"""

CAMPAIGN_GENERATION_PROMPT = """你是一个营销策划专家。请基于当前的运营数据和反思洞察，设计一个营销活动方案。

运营数据：
{operations_data}

反思洞察：
{reflection_insights}

当前日期：{current_date}

设计要求：
1. 活动目标明确
2. 目标用户清晰
3. 活动时间合理
4. 激励机制有效
5. 效果可衡量

返回JSON格式：
{{
    "campaign": {{
        "name": "活动名称",
        "objective": "活动目标",
        "target_audience": "目标受众",
        "duration": "活动时间",
        "activities": ["活动内容1", "活动内容2"],
        "incentives": ["激励措施1", "激励措施2"],
        "expected_outcomes": ["预期效果1", "预期效果2"],
        "kpis": ["关键指标1", "关键指标2"]
    }},
    "risk_assessment": "风险评估",
    "implementation_notes": "实施注意事项"
}}
"""


class RecommendationService:
    """
    推荐调度服务类（Agent 驱动版）
    """

    def __init__(self, llm=None):
        # 延迟导入避免循环依赖
        self._behavior_agent = None
        self.llm = llm
        self.is_running = False
        self.scheduler_thread = None
        self.logger = logging.getLogger(__name__)

        # Agent 配置
        self._agent_config = {
            'use_llm_for_recommendations': True,  # 使用 LLM 生成推荐
            'use_llm_for_segmentation': True,      # 使用 LLM 进行用户分群
            'min_users_for_llm': 5,               # LLM 分群最小用户数
            'fallback_to_rules': True,             # LLM 失败时 fallback
            'cache_recommendations': True,          # 缓存推荐
            'recommendation_cache_ttl': 7200,     # 推荐缓存 TTL（2小时）
        }

        # 推荐缓存
        self._recommendation_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def behavior_agent(self):
        """懒加载用户行为服务"""
        if self._behavior_agent is None:
            from services.user_behavior_service import UserBehaviorService
            self._behavior_agent = UserBehaviorService()
        return self._behavior_agent

    def _call_llm_sync(self, prompt: str, temperature: float = 0.5) -> Optional[str]:
        """同步调用 LLM"""
        if not self.llm:
            return None

        try:
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

    async def _call_llm_async(self, prompt: str, temperature: float = 0.5) -> Optional[str]:
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
                        {"role": "system", "content": "你是一个专业的营销顾问。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"}
                )
                return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"LLM 调用异常: {e}")
            return None

    def generate_recommendations_job(self) -> Optional[List[Dict[str, Any]]]:
        """
        定时生成推荐的任务（Agent 驱动）

        Returns:
            生成的推荐列表
        """
        try:
            self.logger.info("开始执行定时推荐生成任务...")

            # 检查是否使用 LLM
            should_use_llm = (
                self._agent_config['use_llm_for_recommendations']
                and self.llm is not None
            )

            if should_use_llm:
                recommendations = self._generate_recommendations_with_agent()
            else:
                recommendations = self._generate_recommendations_with_rules()

            if recommendations:
                self.logger.info(f"成功生成 {len(recommendations)} 条推荐:")
                for rec in recommendations:
                    self.logger.info(f"- {rec.get('type')}: {rec.get('title', '')[:50]}...")
                return recommendations
            else:
                self.logger.info("本次没有生成新的推荐")
                return None

        except Exception as e:
            self.logger.error(f"定时推荐生成任务失败: {str(e)}")
            return None

    def _generate_recommendations_with_agent(self) -> List[Dict[str, Any]]:
        """
        使用 Agent（LLM）生成推荐

        Returns:
            LLM 生成的推荐列表
        """
        self.logger.info("使用 Agent 生成推荐")

        # 检查缓存
        cache_key = f"recommendations_{datetime.now().strftime('%Y%m%d%H')}"
        if self._agent_config['cache_recommendations'] and cache_key in self._recommendation_cache:
            cached = self._recommendation_cache[cache_key]
            cache_age = (datetime.now() - cached.get('_cached_at', datetime.min)).seconds
            if cache_age < self._agent_config['recommendation_cache_ttl']:
                self.logger.info(f"使用缓存的推荐，缓存年龄: {cache_age}秒")
                return cached.get('recommendations', [])

        try:
            # 获取用户数据
            user_data = self._collect_user_data_for_recommendations()

            if len(user_data.get('users', [])) < self._agent_config['min_users_for_llm']:
                self.logger.info(f"用户数量不足，使用规则生成推荐")
                return self._generate_recommendations_with_rules()

            # 构建 Prompt
            prompt = RECOMMENDATION_GENERATION_PROMPT.format(
                user_data=json.dumps(user_data, ensure_ascii=False, indent=2),
                current_date=datetime.now().strftime('%Y年%m月%d日'),
                scenario=user_data.get('scenario', '日常推荐')
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                recommendations = result.get('recommendations', [])

                # 缓存结果
                if self._agent_config['cache_recommendations']:
                    self._recommendation_cache[cache_key] = {
                        'recommendations': recommendations,
                        '_cached_at': datetime.now()
                    }

                return recommendations

        except json.JSONDecodeError as e:
            self.logger.error(f"LLM 返回格式错误: {e}")
        except Exception as e:
            self.logger.error(f"Agent 推荐生成失败: {e}")

        # Fallback
        return self._generate_recommendations_with_rules()

    def _collect_user_data_for_recommendations(self) -> Dict[str, Any]:
        """收集用于推荐的用户数据"""
        try:
            # 获取所有用户的行为数据
            from db.db_router import DatabaseRouter
            db = DatabaseRouter()
            all_behaviors = db.user_behavior.get_all_behaviors(days_back=30)

            # 按用户分组
            user_data_map = {}
            for behavior in all_behaviors:
                user_id = behavior.get('user_id', 'unknown')
                if user_id not in user_data_map:
                    user_data_map[user_id] = {
                        'user_id': user_id,
                        'appointments': [],
                        'last_activity': None,
                        'total_appointments': 0
                    }

                if behavior.get('action_type') == 'appointment':
                    user_data_map[user_id]['appointments'].append(behavior)
                    user_data_map[user_id]['total_appointments'] += 1

            # 获取技师信息
            from services.appointment_service import AppointmentService
            appt_service = AppointmentService()
            all_technicians = appt_service.get_all_technicians()

            return {
                'users': list(user_data_map.values())[:50],  # 限制数量
                'technicians': [{'id': t.get('id'), 'name': t.get('name'), 'gender': t.get('gender')} for t in all_technicians],
                'scenario': self._get_recommendation_scenario()
            }

        except Exception as e:
            self.logger.error(f"收集用户数据失败: {e}")
            return {'users': [], 'technicians': [], 'scenario': '日常推荐'}

    def _get_recommendation_scenario(self) -> str:
        """获取当前推荐场景"""
        now = datetime.now()
        weekday = now.weekday()
        hour = now.hour

        # 根据时间判断场景
        if weekday >= 5:  # 周末
            return "周末休闲推荐"
        elif hour < 12:  # 上午
            return "上午活力推荐"
        elif hour < 18:  # 下午
            return "下午放松推荐"
        else:  # 晚上
            return "晚间舒缓推荐"

    def _generate_recommendations_with_rules(self) -> List[Dict[str, Any]]:
        """
        使用规则生成推荐（fallback）

        Returns:
            规则生成的推荐列表
        """
        self.logger.info("使用规则生成推荐")

        recommendations = []

        # 获取高价值用户进行回访提醒
        try:
            from db.db_router import DatabaseRouter
            db = DatabaseRouter()
            all_behaviors = db.user_behavior.get_all_behaviors(days_back=60)

            # 找出超过30天未预约的用户
            from datetime import timedelta
            threshold_date = datetime.now() - timedelta(days=30)

            inactive_users = set()
            for behavior in all_behaviors:
                if behavior.get('action_type') == 'appointment':
                    created_at = behavior.get('created_at')
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        except:
                            continue

                    if created_at < threshold_date:
                        inactive_users.add(behavior.get('user_id'))

            # 为沉睡用户生成推荐
            for user_id in list(inactive_users)[:10]:
                recommendations.append({
                    'type': 'return_reminder',
                    'title': f'回访提醒 - 用户 {user_id}',
                    'content': f'用户 {user_id} 已超过30天未预约',
                    'reason': '用户活跃度下降，需要回访',
                    'urgency': 'medium',
                    'action': '发送回访消息'
                })

        except Exception as e:
            self.logger.error(f"规则推荐生成失败: {e}")

        return recommendations

    def segment_users_with_agent(self) -> Dict[str, Any]:
        """
        使用 Agent 进行用户分群

        Returns:
            用户分群结果
        """
        if not self.llm or not self._agent_config['use_llm_for_segmentation']:
            return self._segment_users_with_rules()

        self.logger.info("使用 Agent 进行用户分群")

        try:
            # 收集用户数据
            user_data = self._collect_user_data_for_recommendations()

            if len(user_data.get('users', [])) < self._agent_config['min_users_for_llm']:
                return self._segment_users_with_rules()

            # 构建 Prompt
            prompt = USER_SEGMENTATION_PROMPT.format(
                behavior_data=json.dumps(user_data, ensure_ascii=False, indent=2),
                current_date=datetime.now().strftime('%Y年%m月%d日')
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                result['_method'] = 'agent'
                return result

        except Exception as e:
            self.logger.error(f"Agent 用户分群失败: {e}")

        # Fallback
        return self._segment_users_with_rules()

    def _segment_users_with_rules(self) -> Dict[str, Any]:
        """使用规则进行用户分群（fallback）"""
        try:
            from db.db_router import DatabaseRouter
            db = DatabaseRouter()
            all_behaviors = db.user_behavior.get_all_behaviors(days_back=30)

            # 简单分群
            user_stats = {}
            for behavior in all_behaviors:
                user_id = behavior.get('user_id', 'unknown')
                if user_id not in user_stats:
                    user_stats[user_id] = {
                        'total': 0,
                        'appointments': 0,
                        'consultations': 0
                    }
                user_stats[user_id]['total'] += 1
                if behavior.get('action_type') == 'appointment':
                    user_stats[user_id]['appointments'] += 1
                elif behavior.get('action_type') == 'consultation':
                    user_stats[user_id]['consultations'] += 1

            # 分类用户
            high_value = [u for u, s in user_stats.items() if s['appointments'] >= 3]
            occasional = [u for u, s in user_stats.items() if 0 < s['appointments'] < 3]
            new_users = [u for u, s in user_stats.items() if s['total'] == 0]

            return {
                'segments': [
                    {'name': '高价值用户', 'count': len(high_value), 'characteristics': ['预约>=3次']},
                    {'name': '偶尔用户', 'count': len(occasional), 'characteristics': ['预约1-2次']},
                    {'name': '新用户', 'count': len(new_users), 'characteristics': ['暂无行为数据']}
                ],
                'high_value_users': high_value,
                'cold_start_users': new_users,
                '_method': 'rules'
            }

        except Exception as e:
            self.logger.error(f"规则用户分群失败: {e}")
            return {'segments': [], '_method': 'rules'}

    def generate_campaign_with_agent(
        self,
        operations_data: Dict[str, Any],
        reflection_insights: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        使用 Agent 设计营销活动

        Args:
            operations_data: 运营数据
            reflection_insights: 反思洞察

        Returns:
            活动方案
        """
        if not self.llm:
            return {
                'campaign': None,
                'error': 'LLM not available',
                '_method': 'unavailable'
            }

        self.logger.info("使用 Agent 设计营销活动")

        try:
            # 构建 Prompt
            prompt = CAMPAIGN_GENERATION_PROMPT.format(
                operations_data=json.dumps(operations_data, ensure_ascii=False, indent=2),
                reflection_insights=json.dumps(reflection_insights, ensure_ascii=False, indent=2),
                current_date=datetime.now().strftime('%Y年%m月%d日')
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt, temperature=0.6)

            if response:
                result = json.loads(response)
                result['_method'] = 'agent'
                return result

        except Exception as e:
            self.logger.error(f"Agent 活动设计失败: {e}")

        return {
            'campaign': None,
            'error': str(e),
            '_method': 'agent_failed'
        }

    def start_scheduler(self) -> bool:
        """启动定时任务调度器"""
        if self.is_running:
            self.logger.warning("调度器已经在运行中")
            return False

        try:
            # 设置定时任务
            schedule.every().day.at("09:00").do(self.generate_recommendations_job)
            schedule.every().day.at("14:00").do(self.generate_recommendations_job)
            schedule.every().day.at("19:00").do(self.generate_recommendations_job)
            schedule.every(2).hours.do(self.generate_recommendations_job)

            self.is_running = True

            def run_scheduler():
                self.logger.info("推荐调度器已启动")
                while self.is_running:
                    schedule.run_pending()
                    time.sleep(60)
                self.logger.info("推荐调度器已停止")

            self.scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
            self.scheduler_thread.start()
            return True

        except Exception as e:
            self.logger.error(f"启动推荐调度器失败: {str(e)}")
            return False

    def stop_scheduler(self) -> bool:
        """停止定时任务调度器"""
        try:
            self.is_running = False
            schedule.clear()
            self.logger.info("推荐调度器已停止")
            return True
        except Exception as e:
            self.logger.error(f"停止推荐调度器失败: {str(e)}")
            return False

    def run_immediate_check(self) -> Optional[List[Dict[str, Any]]]:
        """立即执行一次推荐检查（用于测试或手动触发）"""
        self.logger.info("执行立即推荐检查...")
        return self.generate_recommendations_job()

    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        return {
            "is_running": self.is_running,
            "thread_alive": self.scheduler_thread.is_alive() if self.scheduler_thread else False,
            "next_job": str(schedule.next_run()) if schedule.jobs else None,
            "total_jobs": len(schedule.jobs),
            "llm_available": self.llm is not None,
            "agent_mode": self._agent_config['use_llm_for_recommendations']
        }

# 测试用的手动运行函数
if __name__ == "__main__":
    print("启动推荐调度器测试...")
    service = RecommendationService()
    service.start_scheduler()
    
    try:
        # 运行10分钟用于测试
        time.sleep(600)
    except KeyboardInterrupt:
        print("收到中断信号，停止调度器...")
    finally:
        service.stop_scheduler()
