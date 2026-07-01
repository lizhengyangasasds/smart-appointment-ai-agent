"""
反思报告生成器（Agent 驱动版）- 生成各类反思报告和建议

核心功能：
1. 生成单次任务反思报告（Agent 驱动）
2. 生成周期性反思报告（Agent 驱动）
3. 生成用户回访建议
4. 生成策略优化建议（Agent 驱动）

Agent 架构：
- 使用 LLM 生成自然语言报告内容
- 使用 LLM 进行数据解读和洞察提取
- 保留规则引擎作为 fallback
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import logging
import json


# ==================== Agent Prompt 模板 ====================

POST_TASK_REPORT_PROMPT = """你是一个专业的系统分析师。请基于任务评估结果，生成一段反思报告。

评估结果：
{evaluation_result}

反思结果（如果有）：
{reflection_result}

生成要求：
1. 用自然语言描述任务的执行情况
2. 指出关键的成功因素或失败原因
3. 提出具体的改进建议
4. 语气专业但易于理解
5. 字数控制在 100-150 字

返回JSON格式：
{{
    "summary": "任务总结（一句话）",
    "analysis": "详细分析（3-5句话）",
    "key_insight": "核心洞察",
    "improvement_suggestions": ["建议1", "建议2", "建议3"]
}}
"""

PERIODIC_REPORT_PROMPT = """你是一个运营分析专家。请基于以下周期性反思数据，生成一份综合报告。

数据摘要：
{data_summary}

关键指标：
{key_metrics}

发现的模式：
{patterns}

坏case汇总：
{bad_cases_summary}

建议列表：
{recommendations}

当前日期：{current_date}

生成要求：
1. 整体评估系统当前状态
2. 突出最重要的发现
3. 按优先级排列改进建议
4. 提供具体的行动项
5. 用数据支持结论

返回JSON格式：
{{
    "executive_summary": "执行摘要（2-3句话）",
    "key_findings": [
        {{"finding": "发现描述", "impact": "影响程度", "evidence": "证据"}}
    ],
    "priority_actions": [
        {{"action": "行动描述", "priority": "high/medium/low", "expected_impact": "预期效果"}}
    ],
    "risk_alerts": ["风险提示1", "风险提示2"],
    "overall_health_score": 0.0-1.0,
    "trend_assessment": "上升/稳定/下降"
}}
"""

USER_INSIGHT_REPORT_PROMPT = """你是一个用户洞察专家。请分析以下用户数据，生成用户洞察报告。

用户行为数据：
{behavior_data}

用户反馈数据：
{feedback_data}

当前日期：{current_date}

生成要求：
1. 分析用户的价值和潜力
2. 识别用户的偏好和痛点
3. 提出个性化服务建议
4. 预测用户流失风险
5. 给出用户激活策略

返回JSON格式：
{{
    "user_profile": {{
        "value_tier": "高价值/中等价值/低价值/沉睡",
        "engagement_level": "高/中/低",
        "churn_risk": "high/medium/low"
    }},
    "preferences": {{
        "technician": "技师偏好描述",
        "service": "服务偏好描述",
        "time": "时间偏好描述"
    }},
    "pain_points": ["痛点1", "痛点2"],
    "retention_strategy": "留存策略描述",
    "personalized_tips": ["个性化建议1", "个性化建议2"]
}}
"""

DASHBOARD_SUMMARY_PROMPT = """你是一个数据可视化专家。请基于以下数据，生成一份仪表盘摘要。

今日数据：{today_stats}
本周数据：{week_stats}
本月数据：{month_stats}

生成要求：
1. 用简洁的语言描述当前状态
2. 突出需要关注的问题
3. 提供快速可执行的洞察
4. 格式适合前端展示

返回JSON格式：
{{
    "headline": "一句话总结",
    "highlights": ["要点1", "要点2", "要点3"],
    "alerts": [
        {{"level": "warning/critical", "message": "告警消息", "action": "建议行动"}}
    ],
    "trend_indicators": {{
        "users": "up/down/stable",
        "satisfaction": "up/down/stable",
        "efficiency": "up/down/stable"
    }},
    "quick_actions": ["快速行动1", "快速行动2"]
}}
"""


class ReflectionReporter:
    """
    反思报告生成器（Agent 驱动版）
    """

    def __init__(self, llm=None, reflection_repo=None):
        self.llm = llm
        self.reflection_repo = reflection_repo
        self.logger = logging.getLogger(__name__)

        # Agent 配置
        self._agent_config = {
            'use_llm_for_reports': True,        # 使用 LLM 生成报告
            'use_llm_for_insights': True,       # 使用 LLM 提取洞察
            'min_data_for_llm': 5,              # LLM 报告最小数据量
            'fallback_to_rules': True,           # LLM 失败时 fallback
            'cache_reports': True,               # 缓存报告
            'report_cache_ttl': 3600,           # 报告缓存 TTL（1小时）
        }

        # 报告缓存
        self._report_cache: Dict[str, Dict[str, Any]] = {}

    def _call_llm_sync(self, prompt: str, temperature: float = 0.3) -> Optional[str]:
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

    async def _call_llm_async(self, prompt: str, temperature: float = 0.3) -> Optional[str]:
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
                        {"role": "system", "content": "你是一个专业的系统分析师。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"}
                )
                return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"LLM 调用异常: {e}")
            return None

    def generate_post_task_report(
        self,
        session_id: str,
        evaluation_result: Dict[str, Any],
        reflection_result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        生成任务后反思报告（Agent 驱动）

        Args:
            session_id: 会话ID
            evaluation_result: 评估结果
            reflection_result: 反思结果（可选）

        Returns:
            反思报告
        """
        success = evaluation_result.get('success', 0) == 2
        success_rate = evaluation_result.get('success_rate', 0)

        # 确定反思类型
        if success and success_rate >= 0.9:
            reflection_type = "success_analysis"
            title = "成功任务分析"
        elif success:
            reflection_type = "partial_success_analysis"
            title = "部分成功任务分析"
        else:
            reflection_type = "failure_analysis"
            title = "失败任务分析"

        # 检查是否使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_reports']
            and self.llm is not None
            and reflection_result is not None
        )

        if should_use_llm:
            llm_report = self._generate_report_with_agent(evaluation_result, reflection_result)
            reflection_content = llm_report.get('analysis', '')
            actionable_insights = llm_report.get('improvement_suggestions', [])
        else:
            reflection_content = self._generate_reflection_content(evaluation_result, reflection_result)
            actionable_insights = self._extract_actionable_insights(evaluation_result, reflection_result)

        # 构建报告内容
        report = {
            "type": reflection_type,
            "title": title,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "evaluation_summary": {
                "success": success,
                "success_rate": success_rate,
                "success_level": evaluation_result.get('success_level'),
                "turns_count": evaluation_result.get('turns_count'),
                "completion_time": evaluation_result.get('completion_time'),
                "error_type": evaluation_result.get('error_type')
            },
            "reflection_content": reflection_content,
            "actionable_insights": actionable_insights,
            "_generation_method": "agent" if should_use_llm else "rules"
        }

        # 如果有反思结果，添加分析发现
        if reflection_result:
            report["findings"] = reflection_result.get('findings', {})
            report["recommendations"] = reflection_result.get('recommendations', [])

        return report

    def _generate_report_with_agent(
        self,
        evaluation_result: Dict[str, Any],
        reflection_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        使用 Agent（LLM）生成报告

        Args:
            evaluation_result: 评估结果
            reflection_result: 反思结果

        Returns:
            LLM 生成的报告内容
        """
        self.logger.info("使用 Agent 生成任务反思报告")

        try:
            # 构建 Prompt
            prompt = POST_TASK_REPORT_PROMPT.format(
                evaluation_result=json.dumps(evaluation_result, ensure_ascii=False, indent=2),
                reflection_result=json.dumps(reflection_result, ensure_ascii=False, indent=2) if reflection_result else "{}"
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                self.logger.info(f"Agent 生成了报告: {result.get('summary', '')[:50]}...")
                return result

        except Exception as e:
            self.logger.error(f"Agent 报告生成失败: {e}")

        # Fallback
        return {
            'summary': '',
            'analysis': self._generate_reflection_content(evaluation_result, reflection_result),
            'improvement_suggestions': []
        }

    def generate_periodic_report(self, days: int = 7) -> Dict[str, Any]:
        """
        生成周期性反思报告（Agent 驱动）

        Args:
            days: 周期天数

        Returns:
            周期性报告
        """
        if not self.reflection_repo:
            return {"error": "reflection_repo not available"}

        # 获取数据
        recent_reflections = self.reflection_repo.get_recent_reflections(days=days)
        recommendations = self.reflection_repo.get_actionable_recommendations()
        bad_cases = self.reflection_repo.get_all_bad_cases(days=days)

        # 检查是否使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_reports']
            and self.llm is not None
            and len(recent_reflections) >= self._agent_config['min_data_for_llm']
        )

        if should_use_llm:
            llm_report = self._generate_periodic_report_with_agent(
                recent_reflections, recommendations, bad_cases, days
            )
            summary = llm_report.get('executive_summary', '')
            key_metrics = llm_report.get('key_findings', [])
            priority_actions = llm_report.get('priority_actions', [])
        else:
            summary = self._generate_periodic_summary(
                len(recent_reflections), len(bad_cases), recommendations
            )
            key_metrics = self._calculate_key_metrics(recent_reflections)
            priority_actions = self._propose_next_actions(recommendations, bad_cases)

        # 生成报告
        report = {
            "type": "periodic_report",
            "title": f"{days}天周期反思报告",
            "period_days": days,
            "generated_at": datetime.now().isoformat(),
            "summary": summary,
            "key_metrics": key_metrics,
            "patterns_discovered": self._aggregate_patterns(recent_reflections),
            "bad_cases_summary": self._summarize_bad_cases(bad_cases),
            "top_recommendations": recommendations[:5] if recommendations else [],
            "next_actions": priority_actions,
            "_generation_method": "agent" if should_use_llm else "rules"
        }

        return report

    def _generate_periodic_report_with_agent(
        self,
        recent_reflections: List[Dict],
        recommendations: List[Dict],
        bad_cases: List[Dict],
        days: int
    ) -> Dict[str, Any]:
        """
        使用 Agent 生成周期性报告

        Args:
            recent_reflections: 近期反思记录
            recommendations: 建议列表
            bad_cases: 坏case列表
            days: 周期天数

        Returns:
            LLM 生成的报告
        """
        self.logger.info("使用 Agent 生成周期性报告")

        try:
            # 准备数据摘要
            data_summary = {
                'total_reflections': len(recent_reflections),
                'total_bad_cases': len(bad_cases),
                'total_recommendations': len(recommendations),
                'period_days': days
            }

            key_metrics = self._calculate_key_metrics(recent_reflections)
            patterns = self._aggregate_patterns(recent_reflections)
            bad_cases_summary = self._summarize_bad_cases(bad_cases)

            # 构建 Prompt
            prompt = PERIODIC_REPORT_PROMPT.format(
                data_summary=json.dumps(data_summary, ensure_ascii=False),
                key_metrics=json.dumps(key_metrics, ensure_ascii=False, indent=2),
                patterns=json.dumps(patterns, ensure_ascii=False, indent=2),
                bad_cases_summary=json.dumps(bad_cases_summary, ensure_ascii=False, indent=2),
                recommendations=json.dumps(recommendations[:10], ensure_ascii=False, indent=2),
                current_date=datetime.now().strftime('%Y年%m月%d日')
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                self.logger.info(f"Agent 生成了周期性报告: {result.get('executive_summary', '')[:50]}...")
                return result

        except Exception as e:
            self.logger.error(f"Agent 周期性报告生成失败: {e}")

        # Fallback
        return {
            'executive_summary': self._generate_periodic_summary(
                len(recent_reflections), len(bad_cases), recommendations
            ),
            'key_findings': [],
            'priority_actions': []
        }

    def generate_user_insight_report(self, user_id: str = "default_user") -> Dict[str, Any]:
        """
        生成用户洞察报告（Agent 驱动）

        Args:
            user_id: 用户ID

        Returns:
            用户洞察报告
        """
        if not self.reflection_repo:
            return {"error": "reflection_repo not available"}

        # 获取数据
        from db.db_router import DatabaseRouter
        db = DatabaseRouter()
        feedbacks = db.feedback.get_user_feedbacks(user_id=user_id, days=30)
        rating_stats = db.feedback.get_rating_stats(user_id=user_id, days=30)

        from services.user_behavior_service import UserBehaviorService
        behavior_service = UserBehaviorService()
        pattern_analysis = behavior_service.analyze_user_patterns(user_id)

        # 检查是否使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_insights']
            and self.llm is not None
        )

        if should_use_llm:
            llm_insight = self._generate_user_insight_with_agent(
                user_id, pattern_analysis, rating_stats, feedbacks
            )
            personalized_suggestions = llm_insight.get('personalized_tips', [])
        else:
            personalized_suggestions = self._generate_personalized_suggestions(
                pattern_analysis, rating_stats
            )

        # 生成报告
        report = {
            "type": "user_insight",
            "user_id": user_id,
            "generated_at": datetime.now().isoformat(),
            "user_engagement": {
                "total_feedbacks": len(feedbacks),
                "avg_rating": rating_stats.get('avg_rating', 0),
                "rating_distribution": self._analyze_rating_distribution(feedbacks)
            },
            "behavior_patterns": {
                "pattern_type": pattern_analysis.get('pattern', 'unknown'),
                "frequency_analysis": pattern_analysis.get('frequency_analysis', {}),
                "preferred_technician": pattern_analysis.get('preferred_technician'),
                "time_preference": pattern_analysis.get('time_preference', {})
            },
            "personalized_suggestions": personalized_suggestions,
            "_generation_method": "agent" if should_use_llm else "rules"
        }

        return report

    def _generate_user_insight_with_agent(
        self,
        user_id: str,
        pattern_analysis: Dict,
        rating_stats: Dict,
        feedbacks: List[Dict]
    ) -> Dict[str, Any]:
        """
        使用 Agent 生成用户洞察

        Args:
            user_id: 用户ID
            pattern_analysis: 行为模式分析
            rating_stats: 评分统计
            feedbacks: 反馈列表

        Returns:
            LLM 生成的用户洞察
        """
        self.logger.info(f"使用 Agent 为用户 {user_id} 生成洞察")

        try:
            # 准备数据
            behavior_data = {
                'user_id': user_id,
                'pattern': pattern_analysis.get('pattern', 'unknown'),
                'frequency_analysis': pattern_analysis.get('frequency_analysis', {}),
                'preferred_technician': pattern_analysis.get('preferred_technician'),
                'time_preference': pattern_analysis.get('time_preference', {})
            }

            feedback_data = {
                'total_feedbacks': len(feedbacks),
                'avg_rating': rating_stats.get('avg_rating', 0),
                'rating_distribution': self._analyze_rating_distribution(feedbacks)
            }

            # 构建 Prompt
            prompt = USER_INSIGHT_REPORT_PROMPT.format(
                behavior_data=json.dumps(behavior_data, ensure_ascii=False, indent=2),
                feedback_data=json.dumps(feedback_data, ensure_ascii=False, indent=2),
                current_date=datetime.now().strftime('%Y年%m月%d日')
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                self.logger.info(f"Agent 生成了用户洞察: {result.get('user_profile', {}).get('value_tier', '')}")
                return result

        except Exception as e:
            self.logger.error(f"Agent 用户洞察生成失败: {e}")

        # Fallback
        return {
            'personalized_tips': self._generate_personalized_suggestions(pattern_analysis, rating_stats)
        }

    def generate_dashboard_summary(self) -> Dict[str, Any]:
        """
        生成仪表盘摘要（Agent 驱动）

        Returns:
            仪表盘摘要
        """
        if not self.reflection_repo:
            return {"error": "reflection_repo not available"}

        # 获取数据
        daily_stats = self._get_period_stats(1)
        weekly_stats = self._get_period_stats(7)
        monthly_stats = self._get_period_stats(30)

        # 检查是否使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_reports']
            and self.llm is not None
        )

        if should_use_llm:
            llm_summary = self._generate_dashboard_with_agent(daily_stats, weekly_stats, monthly_stats)
            alerts = llm_summary.get('alerts', [])
            quick_insights = llm_summary.get('quick_actions', [])
        else:
            alerts = self._generate_alerts(weekly_stats)
            quick_insights = self._generate_quick_insights(weekly_stats)

        return {
            "type": "dashboard_summary",
            "generated_at": datetime.now().isoformat(),
            "overview": {
                "today": daily_stats,
                "this_week": weekly_stats,
                "this_month": monthly_stats
            },
            "alerts": alerts,
            "quick_insights": quick_insights,
            "_generation_method": "agent" if should_use_llm else "rules"
        }

    def _generate_dashboard_with_agent(
        self,
        daily_stats: Dict,
        weekly_stats: Dict,
        monthly_stats: Dict
    ) -> Dict[str, Any]:
        """
        使用 Agent 生成仪表盘摘要

        Args:
            daily_stats: 今日统计
            weekly_stats: 本周统计
            monthly_stats: 本月统计

        Returns:
            LLM 生成的摘要
        """
        self.logger.info("使用 Agent 生成仪表盘摘要")

        try:
            # 构建 Prompt
            prompt = DASHBOARD_SUMMARY_PROMPT.format(
                today_stats=json.dumps(daily_stats, ensure_ascii=False),
                week_stats=json.dumps(weekly_stats, ensure_ascii=False),
                month_stats=json.dumps(monthly_stats, ensure_ascii=False)
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                self.logger.info(f"Agent 生成了仪表盘摘要: {result.get('headline', '')[:50]}...")
                return result

        except Exception as e:
            self.logger.error(f"Agent 仪表盘摘要生成失败: {e}")

        # Fallback
        return {
            'alerts': self._generate_alerts(weekly_stats),
            'quick_actions': self._generate_quick_insights(weekly_stats)
        }

    def generate_remediation_report(
        self,
        issue_type: str,
        affected_sessions: List[str],
        root_cause: str
    ) -> Dict[str, Any]:
        """
        生成问题修复报告

        Args:
            issue_type: 问题类型
            affected_sessions: 受影响的会话列表
            root_cause: 根本原因

        Returns:
            修复报告
        """
        return {
            "type": "remediation_report",
            "issue_type": issue_type,
            "affected_count": len(affected_sessions),
            "root_cause": root_cause,
            "generated_at": datetime.now().isoformat(),
            "remediation_steps": [
                {
                    "step": 1,
                    "action": f"记录 {len(affected_sessions)} 个受影响会话",
                    "status": "completed"
                },
                {
                    "step": 2,
                    "action": "分析根因",
                    "status": "completed",
                    "result": root_cause
                },
                {
                    "step": 3,
                    "action": "制定修复方案",
                    "status": "in_progress",
                    "suggested_actions": self._suggest_fixes(issue_type)
                },
                {
                    "step": 4,
                    "action": "实施修复",
                    "status": "pending"
                },
                {
                    "step": 5,
                    "action": "验证修复效果",
                    "status": "pending"
                }
            ],
            "prevention_recommendations": self._suggest_prevention(issue_type)
        }

    def generate_dashboard_summary(self) -> Dict[str, Any]:
        """
        生成仪表盘摘要（供前端展示）

        Returns:
            仪表盘摘要
        """
        if not self.reflection_repo:
            return {"error": "reflection_repo not available"}

        # 获取各周期统计数据
        daily_stats = self._get_period_stats(1)
        weekly_stats = self._get_period_stats(7)
        monthly_stats = self._get_period_stats(30)

        return {
            "type": "dashboard_summary",
            "generated_at": datetime.now().isoformat(),
            "overview": {
                "today": daily_stats,
                "this_week": weekly_stats,
                "this_month": monthly_stats
            },
            "alerts": self._generate_alerts(weekly_stats),
            "quick_insights": self._generate_quick_insights(weekly_stats)
        }

    def _generate_reflection_content(
        self,
        evaluation: Dict[str, Any],
        reflection: Dict[str, Any] = None
    ) -> str:
        """生成反思内容描述"""
        parts = []

        # 成功/失败描述
        if evaluation.get('success', 0) == 2:
            parts.append(f"任务成功完成，成功率 {evaluation.get('success_rate', 0):.1%}")
        elif evaluation.get('success', 0) == 1:
            parts.append(f"任务部分完成，成功率 {evaluation.get('success_rate', 0):.1%}")
        else:
            parts.append("任务失败")

        # 错误信息
        if evaluation.get('error_type'):
            parts.append(f"错误类型: {evaluation.get('error_type')}")

        # 反思结果
        if reflection and reflection.get('findings'):
            findings = reflection['findings']
            if isinstance(findings, dict):
                for key, value in findings.items():
                    if value:
                        parts.append(f"{key}: {value}")

        return "；".join(parts)

    def _extract_actionable_insights(
        self,
        evaluation: Dict[str, Any],
        reflection: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """提取可操作的洞察"""
        insights = []

        # 基于评估结果的洞察
        if evaluation.get('turns_count', 0) > 10:
            insights.append({
                "category": "efficiency",
                "insight": f"对话轮数偏多 ({evaluation.get('turns_count')})",
                "action": "考虑优化信息收集流程，减少追问次数"
            })

        if evaluation.get('completion_time', 0) > 120:
            insights.append({
                "category": "performance",
                "insight": f"完成时间偏长 ({evaluation.get('completion_time')}秒)",
                "action": "检查系统响应瓶颈，优化LLM调用"
            })

        # 基于反思结果的洞察
        if reflection and reflection.get('recommendations'):
            for rec in reflection['recommendations']:
                insights.append({
                    "category": rec.get('priority', 'medium'),
                    "insight": rec.get('reason', ''),
                    "action": rec.get('action', '')
                })

        return insights

    def _generate_periodic_summary(
        self,
        reflection_count: int,
        bad_case_count: int,
        recommendations: List
    ) -> str:
        """生成周期总结"""
        parts = [f"共进行 {reflection_count} 次反思"]

        if bad_case_count > 0:
            parts.append(f"发现 {bad_case_count} 个坏case")

        if recommendations:
            high_priority = sum(1 for r in recommendations if r.get('priority') == 'high')
            if high_priority > 0:
                parts.append(f"其中 {high_priority} 个高优先级建议待处理")

        return "，".join(parts) if parts else "暂无数据"

    def _calculate_key_metrics(self, reflections: List[Dict]) -> Dict[str, Any]:
        """计算关键指标"""
        if not reflections:
            return {
                "total_reflections": 0,
                "avg_success_rate": 0,
                "improvement_trend": "no_data"
            }

        total = len(reflections)
        success_count = sum(
            1 for r in reflections
            if r.get('findings', {}).get('success', False)
        )

        return {
            "total_reflections": total,
            "success_rate": success_count / total if total > 0 else 0,
            "avg_success_rate": round(
                sum(r.get('findings', {}).get('success_rate', 0) for r in reflections) / total
                if total > 0 else 0, 3
            ),
            "improvement_trend": self._calculate_trend(reflections)
        }

    def _calculate_trend(self, reflections: List[Dict]) -> str:
        """计算趋势"""
        if len(reflections) < 3:
            return "insufficient_data"

        # 按时间排序
        sorted_refs = sorted(reflections, key=lambda x: x.get('created_at', ''))

        # 比较前半和后半的成功率
        mid = len(sorted_refs) // 2
        first_half = sorted_refs[:mid]
        second_half = sorted_refs[mid:]

        first_rate = sum(r.get('findings', {}).get('success_rate', 0) for r in first_half) / len(first_half)
        second_rate = sum(r.get('findings', {}).get('success_rate', 0) for r in second_half) / len(second_half)

        if second_rate > first_rate + 0.1:
            return "improving"
        elif second_rate < first_rate - 0.1:
            return "declining"
        else:
            return "stable"

    def _aggregate_patterns(self, reflections: List[Dict]) -> List[Dict[str, Any]]:
        """汇总发现的模式"""
        patterns = []

        for ref in reflections:
            discovered = ref.get('patterns_discovered', [])
            if discovered:
                patterns.extend(discovered)

        # 去重
        seen = set()
        unique_patterns = []
        for p in patterns:
            key = p.get('pattern_type', '')
            if key and key not in seen:
                seen.add(key)
                unique_patterns.append(p)

        return unique_patterns[:5]

    def _summarize_bad_cases(self, bad_cases: List[Dict]) -> Dict[str, Any]:
        """汇总坏case"""
        if not bad_cases:
            return {"total": 0, "summary": "无坏case记录"}

        from collections import Counter
        categories = Counter(bc.get('category', 'unknown') for bc in bad_cases)

        return {
            "total": len(bad_cases),
            "by_category": dict(categories.most_common(5)),
            "summary": f"共 {len(bad_cases)} 个坏case，涉及 {len(categories)} 个类别"
        }

    def _propose_next_actions(
        self,
        recommendations: List[Dict],
        bad_cases: List[Dict]
    ) -> List[Dict[str, Any]]:
        """提出下一步行动"""
        actions = []

        # 基于高优先级建议的行动
        for rec in recommendations[:3]:
            if rec.get('priority') == 'high':
                actions.append({
                    "action": rec.get('action', ''),
                    "reason": rec.get('reason', ''),
                    "priority": "high"
                })

        # 基于坏case的行动
        if bad_cases:
            actions.append({
                "action": "复盘最近坏case，制定针对性改进方案",
                "reason": f"发现 {len(bad_cases)} 个需要改进的case",
                "priority": "medium"
            })

        return actions[:5]

    def _analyze_rating_distribution(self, feedbacks: List[Dict]) -> Dict[int, int]:
        """分析评分分布"""
        distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for f in feedbacks:
            rating = f.get('rating')
            if rating and 1 <= rating <= 5:
                distribution[rating] += 1
        return distribution

    def _generate_personalized_suggestions(
        self,
        pattern_analysis: Dict,
        rating_stats: Dict
    ) -> List[str]:
        """生成个性化建议"""
        suggestions = []

        pattern = pattern_analysis.get('pattern', '')
        if pattern == 'occasional_user':
            suggestions.append("用户使用频率较低，建议增加回访提醒频率")
        elif pattern == 'frequent_user':
            suggestions.append("高频用户，可推荐会员优惠或长时套餐")

        if rating_stats.get('avg_rating', 0) < 4:
            suggestions.append("用户满意度有提升空间，建议关注服务细节")

        preferred_tech = pattern_analysis.get('preferred_technician')
        if preferred_tech:
            suggestions.append(f"用户偏好技师 #{preferred_tech}，优先推荐该技师")

        return suggestions

    def _suggest_fixes(self, issue_type: str) -> List[str]:
        """建议修复方案"""
        fixes = {
            'slot_conflict': [
                "优化时间段查询逻辑，先查可用再推荐",
                "实现时间段锁定机制，防止并发冲突",
                "添加相邻时间段自动推荐功能"
            ],
            'parse_error': [
                "增强prompt的输出格式约束",
                "添加JSON解析重试机制",
                "实现输出校验和修复"
            ],
            'timeout': [
                "增加LLM调用超时时间",
                "实现请求队列和限流",
                "添加降级策略"
            ]
        }
        return fixes.get(issue_type, ["进行进一步根因分析"])

    def _suggest_prevention(self, issue_type: str) -> List[str]:
        """建议预防措施"""
        return [
            "增加相关测试用例覆盖",
            "实施监控告警机制",
            "定期进行代码审查",
            "建立反思复盘机制"
        ]

    def _get_period_stats(self, days: int) -> Dict[str, Any]:
        """获取周期统计"""
        if not self.reflection_repo:
            return {}

        reflections = self.reflection_repo.get_recent_reflections(days=days)
        return {
            "total_reflections": len(reflections),
            "total_bad_cases": len([
                r for r in reflections
                if r.get('bad_cases')
            ])
        }

    def _generate_alerts(self, stats: Dict) -> List[Dict[str, Any]]:
        """生成告警"""
        alerts = []

        bad_case_count = stats.get('total_bad_cases', 0)
        if bad_case_count > 5:
            alerts.append({
                "level": "warning",
                "message": f"近期坏case数量较多 ({bad_case_count})",
                "action": "建议进行专项复盘"
            })

        return alerts

    def _generate_quick_insights(self, stats: Dict) -> List[str]:
        """生成快速洞察"""
        insights = []

        total = stats.get('total_reflections', 0)
        if total > 0:
            insights.append(f"过去7天共进行 {total} 次任务反思")

        return insights
