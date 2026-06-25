"""
反思分析器 - 分析任务执行结果，发现模式和坏case

核心功能：
1. 分析失败任务的根因
2. 发现用户行为模式
3. 识别坏case
4. 生成改进建议
"""

from typing import Dict, Any, List, Optional
from collections import Counter
import logging


class ReflectionAnalyzer:
    """反思分析器"""

    def __init__(self, evaluation_repo=None, reflection_repo=None, llm=None):
        self.evaluation_repo = evaluation_repo
        self.reflection_repo = reflection_repo
        self.llm = llm
        self.logger = logging.getLogger(__name__)

    def analyze_failed_tasks(self, task_type: str = None, days: int = 7) -> Dict[str, Any]:
        """
        分析失败任务，找出根因

        Args:
            task_type: 任务类型（可选）
            days: 分析最近多少天的数据

        Returns:
            分析结果
        """
        if not self.evaluation_repo:
            return {"error": "evaluation_repo not available"}

        failed_evaluations = self.evaluation_repo.get_failed_evaluations(
            task_type=task_type,
            days=days
        )

        if not failed_evaluations:
            return {
                "total_failed": 0,
                "patterns": [],
                "root_causes": [],
                "summary": "没有失败任务记录"
            }

        # 统计错误类型分布
        error_types = Counter(e.get('error_type') for e in failed_evaluations)
        error_type_patterns = self._analyze_error_patterns(list(error_types.keys()))

        # 分析失败模式
        failure_patterns = self._identify_failure_patterns(failed_evaluations)

        # 生成根因分析
        root_causes = self._generate_root_causes(error_types, failed_evaluations)

        return {
            "total_failed": len(failed_evaluations),
            "error_type_distribution": dict(error_types),
            "patterns": failure_patterns,
            "root_causes": root_causes,
            "recommendations": self._generate_recommendations(root_causes, failure_patterns)
        }

    def discover_user_patterns(self, user_id: str = "default_user", days: int = 30) -> Dict[str, Any]:
        """
        发现用户行为模式

        Args:
            user_id: 用户ID
            days: 分析时间范围

        Returns:
            用户行为模式
        """
        if not self.evaluation_repo:
            return {"error": "evaluation_repo not available"}

        # 获取用户相关的评估记录
        evaluations = self.evaluation_repo.get_recent_evaluations(days=days)

        # 统计任务类型分布
        task_types = Counter(e.get('task_type') for e in evaluations)

        # 分析成功率趋势
        success_rates = []
        for eval_data in evaluations:
            success_rates.append({
                'date': eval_data.get('created_at', '')[:10],
                'success_rate': eval_data.get('success_rate', 0),
                'task_type': eval_data.get('task_type')
            })

        # 分析对话轮数分布
        turns_distribution = [e.get('turns_count', 0) for e in evaluations]
        avg_turns = sum(turns_distribution) / len(turns_distribution) if turns_distribution else 0

        # 发现时间模式
        time_patterns = self._analyze_time_patterns(evaluations)

        return {
            "total_sessions": len(set(e.get('session_id') for e in evaluations)),
            "task_type_distribution": dict(task_types),
            "avg_turns": round(avg_turns, 2),
            "success_rate_trend": success_rates[-10:] if len(success_rates) > 10 else success_rates,
            "time_patterns": time_patterns,
            "insights": self._generate_pattern_insights(task_types, avg_turns, time_patterns)
        }

    def analyze_bad_cases(self, days: int = 30) -> Dict[str, Any]:
        """
        分析坏case

        Args:
            days: 分析时间范围

        Returns:
            坏case分析结果
        """
        if not self.reflection_repo:
            return {"error": "reflection_repo not available"}

        bad_cases = self.reflection_repo.get_all_bad_cases(days=days)

        if not bad_cases:
            return {
                "total_cases": 0,
                "cases": [],
                "summary": "没有记录的坏case"
            }

        # 分类坏case类型
        case_categories = Counter(bc.get('category', 'unknown') for bc in bad_cases)

        # 分析典型坏case
        typical_cases = self._analyze_typical_bad_cases(bad_cases)

        return {
            "total_cases": len(bad_cases),
            "category_distribution": dict(case_categories),
            "typical_cases": typical_cases,
            "improvement_suggestions": self._generate_case_improvements(typical_cases)
        }

    def generate_periodic_reflection(self, days: int = 7) -> Dict[str, Any]:
        """
        生成周期性反思报告

        Args:
            days: 反思周期

        Returns:
            反思报告
        """
        # 获取各类型任务统计
        appointment_stats = self._get_task_stats('appointment', days)
        consultation_stats = self._get_task_stats('consultation', days)
        classification_stats = self._get_task_stats('classification', days)

        # 分析整体趋势
        overall_trend = self._analyze_overall_trend(
            [appointment_stats, consultation_stats, classification_stats]
        )

        # 发现的问题
        issues = self._identify_issues(
            appointment_stats, consultation_stats, classification_stats
        )

        # 生成改进计划
        improvement_plan = self._generate_improvement_plan(issues)

        return {
            "period_days": days,
            "timestamp": self._get_current_timestamp(),
            "task_statistics": {
                "appointment": appointment_stats,
                "consultation": consultation_stats,
                "classification": classification_stats
            },
            "overall_trend": overall_trend,
            "issues_found": issues,
            "improvement_plan": improvement_plan,
            "summary": self._generate_summary(appointment_stats, consultation_stats, overall_trend)
        }

    def _analyze_error_patterns(self, error_types: List[str]) -> List[Dict[str, Any]]:
        """分析错误模式"""
        patterns = []
        error_counter = Counter(error_types)

        for error_type, count in error_counter.most_common(5):
            pattern = {
                "error_type": error_type,
                "count": count,
                "description": self._get_error_description(error_type),
                "severity": "high" if count > 5 else "medium" if count > 2 else "low"
            }
            patterns.append(pattern)

        return patterns

    def _identify_failure_patterns(self, evaluations: List[Dict]) -> List[Dict[str, Any]]:
        """识别失败模式"""
        patterns = []

        # 按会话分组
        session_groups = {}
        for e in evaluations:
            session_id = e.get('session_id', '')
            if session_id not in session_groups:
                session_groups[session_id] = []
            session_groups[session_id].append(e)

        # 分析每个会话的失败模式
        for session_id, session_evals in session_groups.items():
            if len(session_evals) > 3:
                patterns.append({
                    "session_id": session_id,
                    "type": "multi_turn_failure",
                    "description": f"会话 {session_id[:8]} 连续失败 {len(session_evals)} 次",
                    "severity": "high"
                })

        return patterns

    def _generate_root_causes(self, error_types: Counter,
                            evaluations: List[Dict]) -> List[Dict[str, Any]]:
        """生成根因分析"""
        causes = []

        # 基于错误类型推断根因
        if error_types.get('timeout') > 0:
            causes.append({
                "cause": "LLM响应超时",
                "impact": "high",
                "suggestion": "考虑增加超时时间或优化LLM调用"
            })

        if error_types.get('slot_unavailable') > 0:
            causes.append({
                "cause": "时间段冲突",
                "impact": "medium",
                "suggestion": "优化时间段推荐逻辑，提前检测可用性"
            })

        if error_types.get('parse_error') > 0:
            causes.append({
                "cause": "LLM输出格式错误",
                "impact": "high",
                "suggestion": "改进prompt，减少JSON解析错误"
            })

        # 分析缺失字段模式
        missing_fields_counter = Counter()
        for e in evaluations:
            action_data = e.get('action_data', {})
            for field in ['gender', 'start_time', 'duration', 'project']:
                if not action_data.get(field):
                    missing_fields_counter[field] += 1

        if missing_fields_counter:
            most_missing = missing_fields_counter.most_common(1)
            if most_missing:
                field, count = most_missing[0]
                causes.append({
                    "cause": f"'{field}' 字段获取困难",
                    "impact": "medium",
                    "suggestion": f"改进 {field} 相关的意图理解或追问策略"
                })

        return causes

    def _generate_recommendations(self, root_causes: List[Dict],
                                patterns: List[Dict]) -> List[Dict[str, Any]]:
        """生成改进建议"""
        recommendations = []

        for cause in root_causes:
            recommendations.append({
                "priority": cause.get('impact', 'low'),
                "action": cause.get('suggestion'),
                "reason": f"根因: {cause.get('cause')}"
            })

        # 基于模式的建议
        for pattern in patterns:
            if pattern.get('type') == 'multi_turn_failure':
                recommendations.append({
                    "priority": "high",
                    "action": "实现会话恢复机制，保留未完成预约信息",
                    "reason": "发现多次连续失败会话"
                })

        return sorted(recommendations, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get('priority', 'low'), 3))

    def _analyze_time_patterns(self, evaluations: List[Dict]) -> Dict[str, Any]:
        """分析时间模式"""
        hours = []
        weekdays = []

        for e in evaluations:
            created_at = e.get('created_at', '')
            if created_at:
                try:
                    hour = int(created_at[11:13]) if len(created_at) > 13 else 0
                    weekday = self._get_weekday(created_at[:10])
                    hours.append(hour)
                    weekdays.append(weekday)
                except:
                    pass

        hour_counter = Counter(hours)
        weekday_counter = Counter(weekdays)

        return {
            "peak_hours": hour_counter.most_common(3) if hour_counter else [],
            "peak_weekdays": weekday_counter.most_common(3) if weekday_counter else [],
            "hour_distribution": dict(hour_counter),
            "weekday_distribution": dict(weekday_counter)
        }

    def _generate_pattern_insights(self, task_types: Counter, avg_turns: float,
                                  time_patterns: Dict) -> List[str]:
        """生成模式洞察"""
        insights = []

        if task_types.get('appointment', 0) > task_types.get('consultation', 0) * 2:
            insights.append("用户主要使用预约功能，咨询功能使用率较低")

        if avg_turns > 8:
            insights.append("平均对话轮数偏高，可能存在意图理解或信息获取效率问题")

        if time_patterns.get('peak_hours'):
            peak = time_patterns['peak_hours'][0]
            insights.append(f"高峰期在 {peak[0]}:00 左右，可针对性优化该时段的响应")

        if not insights:
            insights.append("用户行为模式正常，未发现明显异常")

        return insights

    def _analyze_typical_bad_cases(self, bad_cases: List[Dict]) -> List[Dict[str, Any]]:
        """分析典型坏case"""
        # 按类别分组
        categories = {}
        for bc in bad_cases:
            cat = bc.get('category', 'unknown')
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(bc)

        # 提取每个类别的典型case
        typical = []
        for cat, cases in categories.items():
            # 取该类别最新的case作为典型
            typical.append({
                "category": cat,
                "count": len(cases),
                "example": cases[0] if cases else {},
                "description": f"{cat} 类问题共 {len(cases)} 例"
            })

        return sorted(typical, key=lambda x: x.get('count', 0), reverse=True)[:5]

    def _generate_case_improvements(self, typical_cases: List[Dict]) -> List[str]:
        """生成case改进建议"""
        improvements = []

        category_improvements = {
            'slot_conflict': "优化时间冲突检测逻辑，支持自动推荐相邻时间段",
            'technician_preference': "改进技师偏好匹配算法，优先推荐高满意度技师",
            'parse_error': "增强输出格式校验，提供fallback回复机制",
            'timeout': "实现请求重试和降级策略",
            'misunderstanding': "优化意图识别prompt，增加Few-shot示例"
        }

        for case in typical_cases:
            cat = case.get('category')
            if cat in category_improvements:
                improvements.append(category_improvements[cat])

        return improvements

    def _get_task_stats(self, task_type: str, days: int) -> Dict[str, Any]:
        """获取任务统计"""
        if not self.evaluation_repo:
            return {}

        return self.evaluation_repo.get_success_rate_stats(task_type=task_type, days=days)

    def _analyze_overall_trend(self, stats_list: List[Dict]) -> Dict[str, Any]:
        """分析整体趋势"""
        valid_stats = [s for s in stats_list if s.get('total', 0) > 0]

        if not valid_stats:
            return {"trend": "no_data", "description": "没有足够数据进行分析"}

        total_tasks = sum(s.get('total', 0) for s in valid_stats)
        total_success = sum(s.get('success', 0) for s in valid_stats)
        overall_rate = total_success / total_tasks if total_tasks > 0 else 0

        return {
            "total_tasks": total_tasks,
            "overall_success_rate": round(overall_rate, 3),
            "trend": "improving" if overall_rate > 0.8 else "stable" if overall_rate > 0.6 else "declining",
            "description": self._get_trend_description(overall_rate)
        }

    def _identify_issues(self, appointment_stats: Dict, consultation_stats: Dict,
                        classification_stats: Dict) -> List[Dict[str, Any]]:
        """识别系统问题"""
        issues = []

        # 检查各类型任务的问题
        for name, stats in [("预约", appointment_stats),
                           ("咨询", consultation_stats),
                           ("分类", classification_stats)]:
            if stats.get('total', 0) > 0:
                rate = stats.get('success_rate', 0)
                if rate < 0.6:
                    issues.append({
                        "type": name,
                        "issue": f"{name}成功率过低",
                        "severity": "high",
                        "current_rate": rate,
                        "target_rate": 0.8
                    })
                elif rate < 0.8:
                    issues.append({
                        "type": name,
                        "issue": f"{name}成功率有待提升",
                        "severity": "medium",
                        "current_rate": rate,
                        "target_rate": 0.8
                    })

        return issues

    def _generate_improvement_plan(self, issues: List[Dict]) -> List[Dict[str, Any]]:
        """生成改进计划"""
        plan = []

        for issue in issues:
            priority = "P0" if issue.get('severity') == 'high' else "P1"
            plan.append({
                "priority": priority,
                "task": f"优化{issue.get('type')}流程",
                "target": f"将成功率从 {issue.get('current_rate', 0):.1%} 提升到 {issue.get('target_rate', 0.8):.1%}",
                "action": self._suggest_action(issue.get('type'))
            })

        return sorted(plan, key=lambda x: 0 if x['priority'] == 'P0' else 1)

    def _suggest_action(self, task_type: str) -> str:
        """建议改进动作"""
        actions = {
            "预约": "分析预约流失环节，优化信息收集流程",
            "咨询": "扩充知识库内容，提升问答匹配度",
            "分类": "优化分类prompt，增加明确示例"
        }
        return actions.get(task_type, "进行根因分析")

    def _generate_summary(self, appointment_stats: Dict, consultation_stats: Dict,
                         trend: Dict) -> str:
        """生成总结"""
        parts = []

        if appointment_stats.get('total', 0) > 0:
            rate = appointment_stats.get('success_rate', 0)
            parts.append(f"预约任务{appointment_stats.get('total', 0)}个，成功率{rate:.1%}")

        if consultation_stats.get('total', 0) > 0:
            rate = consultation_stats.get('success_rate', 0)
            parts.append(f"咨询任务{consultation_stats.get('total', 0)}个，成功率{rate:.1%}")

        if trend.get('trend') != 'no_data':
            parts.append(f"整体趋势{trend.get('description')}")

        return "；".join(parts) if parts else "暂无数据"

    def _get_error_description(self, error_type: str) -> str:
        """获取错误类型描述"""
        descriptions = {
            'timeout': 'LLM响应超时',
            'slot_unavailable': '预约时间段不可用',
            'parse_error': 'JSON解析失败',
            'database_error': '数据库操作错误',
            'llm_error': 'LLM调用错误',
            'unknown_error': '未知错误'
        }
        return descriptions.get(error_type, error_type)

    def _get_weekday(self, date_str: str) -> str:
        """获取星期几"""
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        try:
            from datetime import datetime
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return weekdays[dt.weekday()]
        except:
            return "未知"

    def _get_current_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()

    def _get_trend_description(self, rate: float) -> str:
        """获取趋势描述"""
        if rate >= 0.9:
            return "优秀"
        elif rate >= 0.8:
            return "良好"
        elif rate >= 0.6:
            return "一般，需改进"
        else:
            return "较差，需重点优化"
