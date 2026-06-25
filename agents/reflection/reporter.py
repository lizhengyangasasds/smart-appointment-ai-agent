"""
反思报告生成器 - 生成各类反思报告和建议

核心功能：
1. 生成单次任务反思报告
2. 生成周期性反思报告
3. 生成用户回访建议
4. 生成策略优化建议
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import logging


class ReflectionReporter:
    """反思报告生成器"""

    def __init__(self, llm=None, reflection_repo=None):
        self.llm = llm
        self.reflection_repo = reflection_repo
        self.logger = logging.getLogger(__name__)

    def generate_post_task_report(
        self,
        session_id: str,
        evaluation_result: Dict[str, Any],
        reflection_result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        生成任务后反思报告

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
            "reflection_content": self._generate_reflection_content(
                evaluation_result, reflection_result
            ),
            "actionable_insights": self._extract_actionable_insights(
                evaluation_result, reflection_result
            )
        }

        # 如果有反思结果，添加分析发现
        if reflection_result:
            report["findings"] = reflection_result.get('findings', {})
            report["recommendations"] = reflection_result.get('recommendations', [])

        return report

    def generate_periodic_report(self, days: int = 7) -> Dict[str, Any]:
        """
        生成周期性反思报告

        Args:
            days: 周期天数

        Returns:
            周期性报告
        """
        if not self.reflection_repo:
            return {"error": "reflection_repo not available"}

        # 获取最近的反思记录
        recent_reflections = self.reflection_repo.get_recent_reflections(days=days)

        # 获取可执行的建议
        recommendations = self.reflection_repo.get_actionable_recommendations()

        # 获取坏case
        bad_cases = self.reflection_repo.get_all_bad_cases(days=days)

        # 生成报告
        report = {
            "type": "periodic_report",
            "title": f"{days}天周期反思报告",
            "period_days": days,
            "generated_at": datetime.now().isoformat(),
            "summary": self._generate_periodic_summary(
                len(recent_reflections), len(bad_cases), recommendations
            ),
            "key_metrics": self._calculate_key_metrics(recent_reflections),
            "patterns_discovered": self._aggregate_patterns(recent_reflections),
            "bad_cases_summary": self._summarize_bad_cases(bad_cases),
            "top_recommendations": recommendations[:5] if recommendations else [],
            "next_actions": self._propose_next_actions(recommendations, bad_cases)
        }

        return report

    def generate_user_insight_report(self, user_id: str = "default_user") -> Dict[str, Any]:
        """
        生成用户洞察报告（用于个性化服务优化）

        Args:
            user_id: 用户ID

        Returns:
            用户洞察报告
        """
        if not self.reflection_repo:
            return {"error": "reflection_repo not available"}

        # 获取用户反馈
        from db.db_router import DatabaseRouter
        db = DatabaseRouter()
        feedbacks = db.feedback.get_user_feedbacks(user_id=user_id, days=30)
        rating_stats = db.feedback.get_rating_stats(user_id=user_id, days=30)

        # 获取用户行为分析
        from services.user_behavior_service import UserBehaviorService
        behavior_service = UserBehaviorService()
        pattern_analysis = behavior_service.analyze_user_patterns(user_id)

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
            "personalized_suggestions": self._generate_personalized_suggestions(
                pattern_analysis, rating_stats
            )
        }

        return report

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
