"""
反思引擎 - 协调评估、分析和报告的核心组件

核心功能：
1. 管理反思流程
2. 触发反思机制
3. 协调各组件工作
4. 提供统一的反思接口
5. 支持闭环反馈机制
"""

import asyncio
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
from enum import Enum
import logging

# 闭环组件
from .strategy_updater import StrategyUpdater, StrategyType, StrategyStatus
from .closed_loop_evaluator import ClosedLoopEvaluator, EvaluationResult
from .context_provider import ReflectionContextProvider, ContextFormat


class ReflectionTrigger(Enum):
    """反思触发类型"""
    POST_TASK = "post_task"           # 任务后反思
    PERIODIC = "periodic"             # 周期性反思
    THRESHOLD = "threshold"           # 阈值触发
    MANUAL = "manual"                 # 手动触发
    USER_FEEDBACK = "user_feedback"  # 用户反馈触发
    CLOSED_LOOP = "closed_loop"       # 闭环验证触发


class ReflectionEngine:
    """反思引擎核心"""

    def __init__(
        self,
        evaluator=None,
        analyzer=None,
        reporter=None,
        evaluation_repo=None,
        reflection_repo=None,
        feedback_repo=None,
        llm=None
    ):
        self.evaluator = evaluator
        self.analyzer = analyzer
        self.reporter = reporter
        self.evaluation_repo = evaluation_repo
        self.reflection_repo = reflection_repo
        self.feedback_repo = feedback_repo
        self.llm = llm
        self.logger = logging.getLogger(__name__)

        # 反思缓存，避免重复反思
        self._reflection_cache = {}
        self._cache_ttl = 300  # 5分钟缓存

        # ========== 闭环组件初始化 ==========
        # 策略更新器：基于反思洞察动态调整策略
        # strategy_repo 让 activate/rollback 真正落库（之前只改内存，重启即丢）
        from db.repositories.reflection_repository import StrategyRepository
        self.strategy_updater = StrategyUpdater(
            reflection_repo=reflection_repo,
            llm=llm,
            strategy_repo=StrategyRepository(),
        )

        # 闭环效果验证器：验证策略改进效果
        self.closed_loop_evaluator = ClosedLoopEvaluator(
            evaluation_repo=evaluation_repo,
            reflection_repo=reflection_repo,
            strategy_updater=self.strategy_updater
        )

        # 反思上下文提供者：为 Agent 提供结构化的反思上下文
        self.context_provider = ReflectionContextProvider(
            reflection_engine=self,
            strategy_updater=self.strategy_updater,
            closed_loop_evaluator=self.closed_loop_evaluator,
            llm=llm  # 传递 LLM 给 ContextProvider
        )
        # ===================================

    async def reflect_on_task(
        self,
        session_id: str,
        task_type: str,
        task_result: Dict[str, Any],
        turns_count: int,
        completion_time: float = None,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        对任务执行进行反思

        Args:
            session_id: 会话ID
            task_type: 任务类型
            task_result: 任务结果数据
            turns_count: 对话轮数
            completion_time: 完成时间
            error: 错误信息

        Returns:
            反思结果
        """
        self.logger.info(f"开始反思任务: session={session_id}, type={task_type}")

        # 1. 评估任务
        if task_type == 'appointment':
            evaluation = self.evaluator.evaluate_appointment_task(
                session_id=session_id,
                appointment_history=task_result,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error
            )
        elif task_type == 'consultation':
            evaluation = self.evaluator.evaluate_consultation_task(
                session_id=session_id,
                consultation_data=task_result,
                turns_count=turns_count,
                completion_time=completion_time,
                error=error
            )
        elif task_type == 'classification':
            evaluation = self.evaluator.evaluate_classification_task(
                session_id=session_id,
                classification_data=task_result,
                turns_count=turns_count,
                error=error
            )
        else:
            evaluation = self._generic_evaluation(
                session_id, task_type, task_result, turns_count, completion_time, error
            )

        # 2. 如果需要反思，进行分析
        reflection_result = None
        if evaluation.get('should_reflect'):
            reflection_result = await self._perform_reflection(
                session_id=session_id,
                evaluation_id=evaluation.get('evaluation_id'),
                evaluation=evaluation,
                trigger_type=ReflectionTrigger.POST_TASK
            )

        # 3. 生成报告
        report = self.reporter.generate_post_task_report(
            session_id=session_id,
            evaluation_result=evaluation,
            reflection_result=reflection_result
        )

        self.logger.info(f"任务反思完成: success={evaluation.get('success')}")

        return {
            'evaluation': evaluation,
            'reflection': reflection_result,
            'report': report
        }

    async def _perform_reflection(
        self,
        session_id: str,
        evaluation_id: int,
        evaluation: Dict[str, Any],
        trigger_type: ReflectionTrigger
    ) -> Dict[str, Any]:
        """执行反思"""
        self.logger.info(f"执行反思: trigger={trigger_type.value}")

        # 分析失败任务（带超时保护）
        task_type = evaluation.get('task_type', 'unknown')
        try:
            failed_analysis = await asyncio.wait_for(
                self.analyzer.analyze_failed_tasks(task_type=task_type, days=7),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            self.logger.warning("analyze_failed_tasks 超时，跳过")
            failed_analysis = {"patterns": [], "root_causes": [], "summary": "分析超时"}

        # 发现用户模式（带超时保护）
        try:
            pattern_analysis = await asyncio.wait_for(
                self.analyzer.discover_user_patterns(user_id="default_user", days=30),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            self.logger.warning("discover_user_patterns 超时，跳过")
            pattern_analysis = {"patterns": [], "summary": "分析超时"}

        # 分析坏case（带超时保护）
        try:
            bad_case_analysis = await asyncio.wait_for(
                self.analyzer.analyze_bad_cases(days=30),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            self.logger.warning("analyze_bad_cases 超时，跳过")
            bad_case_analysis = {"total_cases": 0, "cases": [], "summary": "分析超时"}

        # 构建反思结果
        findings = {
            'evaluation_summary': evaluation,
            'failure_analysis': failed_analysis,
            'pattern_analysis': pattern_analysis,
            'bad_case_analysis': bad_case_analysis
        }

        recommendations = failed_analysis.get('recommendations', [])
        if not recommendations:
            recommendations = pattern_analysis.get('insights', [])

        # 修字段名错配：
        # - patterns 来自 pattern_analysis 的结构化统计（time_patterns + task_type_distribution），
        #   不是 failed_analysis["patterns"]（无失败任务时为 []）。
        #   之前从 failed_analysis["patterns"] 取，导致 0% 提取率。
        # - bad_cases 来自 bad_case_analysis["cases"]（DB 数据路径，字符串列表）或
        #   ["typical_cases"]（LLM 兜底路径，dict 列表），二者互斥。
        #   统一成字符串后写入 DB，避免 dict 直接进 JSON 报错。
        _pa = pattern_analysis or {}
        patterns = (
            _pa.get('time_patterns', [])
            + [{"type": k, "description": f"分布: {v}"} for k, v in _pa.get('task_type_distribution', {}).items()]
        )
        _ba = bad_case_analysis or {}
        raw_bad_cases = _ba.get('cases', []) or _ba.get('typical_cases', [])
        bad_cases = [
            bc.get('description', str(bc)) if isinstance(bc, dict) else str(bc)
            for bc in raw_bad_cases
        ]

        # 保存反思日志
        reflection_id = None
        if self.reflection_repo:
            reflection_id = self.reflection_repo.save_reflection(
                session_id=session_id,
                evaluation_id=evaluation_id,
                reflection_type=trigger_type.value,
                findings=findings,
                recommendations=recommendations,
                patterns_discovered=patterns,
                bad_cases=[bc.get('description', '') for bc in bad_cases]
            )

            # 更新评估记录的反思标记
            if evaluation_id and self.evaluation_repo:
                self.evaluation_repo.update_reflection_triggered(evaluation_id)

        return {
            'reflection_id': reflection_id,
            'trigger_type': trigger_type.value,
            'findings': findings,
            'recommendations': recommendations,
            'patterns': patterns,
            'bad_cases': bad_cases
        }

    def trigger_periodic_reflection(self, days: int = 7) -> Dict[str, Any]:
        """
        触发周期性反思。

        注意：仅生成报告并存入 findings_payload。
        若需要写 patterns_discovered / bad_cases / recommendations 字段
        （供 get_insights() 使用），请调用 analyze_and_record()。

        Args:
            days: 周期天数

        Returns:
            周期性反思结果（报告）
        """
        self.logger.info(f"触发周期性反思: days={days}")

        # 生成周期性报告
        report = self.reporter.generate_periodic_report(days=days)

        # 保存反思记录（只写 findings，不含 patterns_discovered / bad_cases /
        # recommendations —— 那些由 analyze_and_record() 写入）
        if self.reflection_repo:
            findings_payload = {
                "report_type": "periodic",
                "period_days": days,
                "summary": report.get("summary"),
                "key_metrics": report.get("key_metrics"),
                "success_rate": (report.get("key_metrics") or {}).get("success_rate") if isinstance(report.get("key_metrics"), dict) else None,
                "bad_cases_summary": report.get("bad_cases_summary"),
                "next_actions": report.get("next_actions"),
                "_legacy_report": report,
            }
            self.reflection_repo.save_reflection(
                session_id=f"periodic_{datetime.now().strftime('%Y%m%d')}",
                reflection_type=ReflectionTrigger.PERIODIC.value,
                findings=findings_payload
            )

        return report

    async def analyze_and_record(self, days: int = 7) -> Dict[str, Any]:
        """
        分析近期评测数据并将结构化洞察写入 reflection_logs。

        这是 engine 写入 patterns_discovered / bad_cases / recommendations
        的唯一入口，供 get_insights() 读取并注入到 Agent prompt。

        内部调用三个 analyzer：
        - analyze_failed_tasks：分析失败任务模式 → recommendations
        - discover_user_patterns：分析用户行为 → patterns_discovered
        - analyze_bad_cases：分析典型坏case → bad_cases

        Args:
            days: 分析天数（默认 7）

        Returns:
            包含 patterns / bad_cases / recommendations 的分析结果
        """
        self.logger.info(f"analyze_and_record: days={days}")

        if self.evaluation_repo is None:
            self.logger.warning("evaluation_repo 未初始化，跳过分析")
            return {"patterns": [], "bad_cases": [], "recommendations": []}

        task_type = "appointment"

        # 1. 分析失败任务 → recommendations
        try:
            failed_analysis = await asyncio.wait_for(
                self.analyzer.analyze_failed_tasks(task_type=task_type, days=days),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            self.logger.warning("analyze_failed_tasks 超时")
            failed_analysis = {"patterns": [], "root_causes": [], "recommendations": [], "summary": "超时"}

        # 2. 发现用户行为模式 → patterns_discovered
        try:
            pattern_analysis = await asyncio.wait_for(
                self.analyzer.discover_user_patterns(user_id="default_user", days=days),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            self.logger.warning("discover_user_patterns 超时")
            pattern_analysis = {"patterns": [], "insights": [], "summary": "超时"}

        # 3. 分析典型坏case → bad_cases
        try:
            bad_case_analysis = await asyncio.wait_for(
                self.analyzer.analyze_bad_cases(days=days),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            self.logger.warning("analyze_bad_cases 超时")
            bad_case_analysis = {"total_cases": 0, "cases": [], "typical_cases": [], "summary": "超时"}

        # 字段提取（与 _perform_reflection 保持一致的字段映射）
        recommendations = failed_analysis.get('recommendations', [])
        if not recommendations:
            recommendations = pattern_analysis.get('insights', [])

        # patterns 来自 pattern_analysis 的结构化统计
        _pa = pattern_analysis or {}
        patterns = (
            _pa.get('time_patterns', [])
            + [{"type": k, "description": f"分布: {v}"} for k, v in _pa.get('task_type_distribution', {}).items()]
        )

        _ba = bad_case_analysis or {}
        raw_bad_cases = _ba.get('cases', []) or _ba.get('typical_cases', [])
        # cases（DB 路径，字符串列表）; typical_cases（LLM 路径，dict 列表）
        bad_cases = [
            bc.get('description', str(bc)) if isinstance(bc, dict) else str(bc)
            for bc in raw_bad_cases
        ]

        # 写入 reflection_logs（供 get_insights 读取）
        findings_payload = {
            "report_type": "analyze_and_record",
            "period_days": days,
            "failure_analysis": failed_analysis,
            "pattern_analysis": pattern_analysis,
            "bad_case_analysis": bad_case_analysis,
            "summary": (
                f"分析 {days} 天内评测数据："
                f"{failed_analysis.get('total_failed', 0)} 个失败任务，"
                f"{_pa.get('total_sessions', 0)} 个会话，"
                f"{len(bad_cases)} 个典型坏case"
            ),
        }
        reflection_id = None
        if self.reflection_repo:
            reflection_id = self.reflection_repo.save_reflection(
                session_id=f"eval_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                reflection_type=ReflectionTrigger.PERIODIC.value,
                findings=findings_payload,
                recommendations=recommendations,
                patterns_discovered=patterns,
                bad_cases=[bc.get('description', str(bc)) for bc in bad_cases],
            )

        self.logger.info(
            f"analyze_and_record 完成: patterns={len(patterns)} "
            f"bad_cases={len(bad_cases)} recommendations={len(recommendations)} "
            f"reflection_id={reflection_id}"
        )

        return {
            "reflection_id": reflection_id,
            "patterns": patterns,
            "bad_cases": bad_cases,
            "recommendations": recommendations,
            "failed_analysis": failed_analysis,
            "pattern_analysis": pattern_analysis,
            "bad_case_analysis": bad_case_analysis,
        }

    def record_user_feedback(
        self,
        session_id: str,
        feedback_type: str,
        rating: int = None,
        content: str = None,
        source: str = "explicit"
    ) -> Optional[int]:
        """
        记录用户反馈

        Args:
            session_id: 会话ID
            feedback_type: 反馈类型
            rating: 评分
            content: 反馈内容
            source: 来源

        Returns:
            反馈记录ID
        """
        if not self.feedback_repo:
            self.logger.warning("feedback_repo not available")
            return None

        feedback_id = self.feedback_repo.save_feedback(
            session_id=session_id,
            feedback_type=feedback_type,
            rating=rating,
            content=content,
            source=source
        )

        # 如果是负面反馈，自动触发反思
        if feedback_type in ['complaint', 'correction'] or (rating and rating <= 2):
            self.logger.info(f"检测到负面反馈，触发反思: feedback_id={feedback_id}")
            # 可以在此异步触发反思

        return feedback_id

    def get_reflection_insights(self, days: int = 7) -> Dict[str, Any]:
        """
        获取反思洞察（供其他Agent使用）

        Args:
            days: 时间范围

        Returns:
            洞察数据
        """
        if not self.reflection_repo:
            return {}

        # 获取最近的反思
        reflections = self.reflection_repo.get_recent_reflections(days=days)

        # 获取可执行的建议
        recommendations = self.reflection_repo.get_actionable_recommendations()

        # 获取坏case
        bad_cases = self.reflection_repo.get_all_bad_cases(days=days)

        return {
            'recent_insights': self._summarize_insights(reflections),
            'actionable_recommendations': recommendations[:3],
            'recent_bad_cases': bad_cases[:5],
            'summary': self._generate_insight_summary(reflections, recommendations)
        }

    def should_trigger_goal_check(self, session_id: str) -> bool:
        """
        检查是否应该触发目标检查

        Args:
            session_id: 会话ID

        Returns:
            是否应该触发
        """
        # 检查缓存
        cache_key = f"goal_check_{session_id}"
        if cache_key in self._reflection_cache:
            cached_time = self._reflection_cache[cache_key]
            if (datetime.now() - cached_time).seconds < self._cache_ttl:
                return False

        # 更新缓存
        self._reflection_cache[cache_key] = datetime.now()

        # 可以添加更多检查逻辑
        return True

    async def generate_remediation_plan(
        self,
        issue_type: str,
        affected_sessions: List[str],
        root_cause: str
    ) -> Dict[str, Any]:
        """
        生成修复计划

        Args:
            issue_type: 问题类型
            affected_sessions: 受影响会话
            root_cause: 根本原因

        Returns:
            修复计划
        """
        return self.reporter.generate_remediation_report(
            issue_type=issue_type,
            affected_sessions=affected_sessions,
            root_cause=root_cause
        )

    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        获取仪表盘数据

        Returns:
            仪表盘数据
        """
        return self.reporter.generate_dashboard_summary()

    def get_task_type_insights(self, task_type: str,
                                days: int = 7) -> Dict[str, Any]:
        """
        获取指定任务类型的洞察（供 ContextProvider 使用）

        Args:
            task_type: 任务类型
            days: 分析窗口天数

        Returns:
            任务类型洞察，包含 patterns / root_causes 等字段
        """
        if not self.analyzer:
            return {"patterns": [], "root_causes": []}

        try:
            import concurrent.futures

            def _sync_call():
                # 在独立事件循环中运行 async 方法
                import asyncio
                return asyncio.run(
                    self.analyzer.analyze_failed_tasks(task_type=task_type, days=days)
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(_sync_call).result(timeout=60)
            return {
                "patterns": result.get("patterns", []),
                "root_causes": result.get("root_causes", []),
                "summary": result.get("summary", ""),
                "total_failed": result.get("total_failed", 0),
            }
        except Exception as e:
            self.logger.warning(f"获取任务类型洞察失败: {e}")
            return {"patterns": [], "root_causes": []}

    def _generic_evaluation(
        self,
        session_id: str,
        task_type: str,
        task_result: Dict,
        turns_count: int,
        completion_time: float,
        error: Exception
    ) -> Dict[str, Any]:
        """通用评估（用于未知任务类型）"""
        success = 0 if error else 1
        success_rate = 0.0 if error else 0.8

        if self.evaluation_repo:
            eval_id = self.evaluation_repo.save_evaluation(
                session_id=session_id,
                task_type=task_type,
                success=success,
                success_rate=success_rate,
                completion_time=completion_time,
                turns_count=turns_count,
                error_type=str(type(error).__name__) if error else None,
                error_message=str(error) if error else None,
                action_data=task_result
            )
        else:
            eval_id = None

        return {
            'evaluation_id': eval_id,
            'success': success,
            'success_level': 'SUCCESS' if success else 'FAILED',
            'success_rate': success_rate,
            'turns_count': turns_count,
            'completion_time': completion_time,
            'should_reflect': error is not None,
            'timestamp': datetime.now().isoformat()
        }

    def _summarize_insights(self, reflections: List[Dict]) -> List[str]:
        """总结洞察"""
        insights = []

        for ref in reflections[-5:]:
            findings = ref.get('findings', {})
            if isinstance(findings, dict):
                summary = findings.get('evaluation_summary', {})
                if summary:
                    insights.append(
                        f"{ref.get('reflection_type')}: "
                        f"成功率 {summary.get('success_rate', 0):.1%}"
                    )

        return insights

    def _generate_insight_summary(
        self,
        reflections: List[Dict],
        recommendations: List[Dict]
    ) -> str:
        """生成洞察总结"""
        parts = []

        if reflections:
            parts.append(f"过去有 {len(reflections)} 次反思记录")

        high_priority = sum(
            1 for r in recommendations
            if r.get('priority') == 'high'
        )
        if high_priority > 0:
            parts.append(f"{high_priority} 个高优先级建议待处理")

        return "，".join(parts) if parts else "暂无洞察数据"

    # ========== 闭环反馈方法 ==========

    def apply_closed_loop_feedback(
        self,
        task_type: str,
        action_taken: Dict[str, Any],
        outcome: str,
        session_id: str = None
    ) -> Dict[str, Any]:
        """
        应用闭环反馈

        将动作结果记录并触发反思，形成完整的反馈闭环

        Args:
            task_type: 任务类型
            action_taken: 采取的动作
            outcome: 结果 (success/failure)
            session_id: 会话 ID

        Returns:
            反馈处理结果
        """
        self.logger.info(f"应用闭环反馈: task={task_type}, outcome={outcome}")

        # 1. 记录动作结果
        evaluation_data = {
            'action': action_taken,
            'outcome': outcome
        }

        # 2. 如果失败，触发反思
        if outcome == 'failure':
            reflection_result = self._trigger_failure_reflection(
                task_type=task_type,
                failed_action=action_taken,
                session_id=session_id
            )
            return {
                'feedback_recorded': True,
                'reflection_triggered': True,
                'reflection_result': reflection_result
            }

        return {
            'feedback_recorded': True,
            'reflection_triggered': False
        }

    def _trigger_failure_reflection(
        self,
        task_type: str,
        failed_action: Dict[str, Any],
        session_id: str = None
    ) -> Dict[str, Any]:
        """触发失败反思"""
        # 分析失败原因
        if hasattr(self.analyzer, 'analyze_failure'):
            analysis = self.analyzer.analyze_failure(
                task_type=task_type,
                failed_action=failed_action
            )
        else:
            analysis = {
                'root_cause': 'unknown',
                'suggested_fix': failed_action.get('suggested_adjustments', {})
            }

        # 生成建议
        if analysis.get('suggested_fix'):
            recommendations = [{
                'type': 'action',
                'title': f"修复 {task_type} 失败",
                'action': {
                    'type': 'prompt' if task_type == 'consultation' else 'matching',
                    'parameters': analysis['suggested_fix']
                },
                'priority': 'high'
            }]

            # 生成新策略
            new_strategies = self.strategy_updater.generate_strategies_from_insights({
                'actionable_recommendations': recommendations,
                'recent_bad_cases': [{
                    'description': f"{task_type} 任务失败",
                    'task_type': task_type,
                    'suggested_fix': analysis.get('suggested_fix', {})
                }]
            })

            # 激活策略
            for strategy in new_strategies:
                self.strategy_updater.activate_strategy(
                    strategy.version_id,
                    strategy.strategy_type
                )

        return {
            'analysis': analysis,
            'strategies_updated': len(new_strategies) if new_strategies else 0
        }

    def validate_action_with_insights(
        self,
        action: Dict[str, Any],
        task_type: str,
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        使用反思洞察验证动作

        Args:
            action: 待验证的动作
            task_type: 任务类型
            context: 上下文

        Returns:
            验证结果
        """
        insights = self.get_reflection_insights(days=7)
        bad_cases = insights.get('recent_bad_cases', [])

        warnings = []
        adjustments = {}

        # 检查动作是否触发了坏 case
        for bc in bad_cases:
            if self._matches_case_pattern(action, bc, context):
                warnings.append(bc.get('description', '未知问题'))
                if bc.get('suggested_fix'):
                    adjustments.update(bc['suggested_fix'])

        return {
            'valid': len(warnings) == 0,
            'warnings': warnings,
            'adjustments': adjustments,
            'requires_adjustment': bool(adjustments)
        }

    def _matches_case_pattern(
        self,
        action: Dict[str, Any],
        bad_case: Dict[str, Any],
        context: Dict[str, Any] = None
    ) -> bool:
        """检查动作是否匹配坏 case 模式"""
        trigger = bad_case.get('trigger', {})

        # 检查动作类型
        if 'action_type' in trigger:
            if action.get('type') != trigger['action_type']:
                return False

        # 检查上下文条件
        if context and 'context' in trigger:
            for key, value in trigger['context'].items():
                if context.get(key) != value:
                    return False

        return True

    def get_context_for_agent(
        self,
        session_id: str,
        task_type: str,
        format: str = "compact"
    ) -> Dict[str, Any]:
        """
        获取 Agent 使用的反思上下文

        Args:
            session_id: 会话 ID
            task_type: 任务类型
            format: 格式 (compact/detailed/actionable)

        Returns:
            反思上下文
        """
        from .context_provider import ContextFormat
        context_format = ContextFormat(format)

        context = self.context_provider.get_context_for_agent(
            session_id=session_id,
            task_type=task_type,
            format=context_format
        )

        return {
            'session_id': context.session_id,
            'task_type': context.task_type,
            'context_text': context.context_text,
            'prompt_injection': context.prompt_injection,
            'confidence': context.confidence,
            'recent_insights': context.recent_insights,
            'recommendations': context.recommendations[:3],
            'bad_cases': context.bad_cases[:2],
            # Agent 生成的内容
            'agent_guidance': context.agent_guidance,
            'do_list': context.do_list,
            'avoid_list': context.avoid_list,
            'specific_suggestions': context.specific_suggestions,
            'generation_method': context.generation_method
        }

    def inject_insights_into_prompt(
        self,
        base_prompt: str,
        session_id: str,
        task_type: str,
        format: str = "compact"
    ) -> str:
        """
        将反思洞察注入到提示词

        Args:
            base_prompt: 基础提示词
            session_id: 会话 ID
            task_type: 任务类型
            format: 格式

        Returns:
            注入后的提示词
        """
        return self.context_provider.inject_context_into_prompt(
            base_prompt=base_prompt,
            session_id=session_id,
            task_type=task_type,
            format=ContextFormat(format)
        )

    def get_active_strategies(self) -> Dict[str, Dict[str, Any]]:
        """获取所有活跃策略"""
        return self.strategy_updater.get_all_active_strategies()

    def update_strategy(
        self,
        strategy_type: StrategyType,
        config: Dict[str, Any],
        trigger_reason: str = ""
    ) -> Optional[str]:
        """
        更新策略

        Args:
            strategy_type: 策略类型
            config: 策略配置
            trigger_reason: 更新原因

        Returns:
            策略版本 ID
        """
        from datetime import datetime

        version_id = f"manual_{strategy_type.value}_{datetime.now().strftime('%H%M%S')}"

        # 创建新策略
        from .strategy_updater import StrategyVersion

        new_strategy = StrategyVersion(
            version_id=version_id,
            strategy_type=strategy_type,
            name=f"手动更新: {trigger_reason[:50]}" if trigger_reason else "手动更新策略",
            config=config,
            priority=10,
            trigger_reason=trigger_reason or "手动更新",
            created_by="manual",
            status=StrategyStatus.PENDING
        )

        # 添加到策略更新器
        self.strategy_updater._strategies[strategy_type.value].append(new_strategy)

        # 激活策略
        self.strategy_updater.activate_strategy(version_id, strategy_type)

        # 记录策略版本供效果评估使用
        self.closed_loop_evaluator.record_strategy_version(version_id)

        return version_id

    def evaluate_strategy_effectiveness(
        self,
        strategy_version_id: str,
        task_type: str
    ) -> Dict[str, Any]:
        """
        评估策略有效性

        Args:
            strategy_version_id: 策略版本 ID
            task_type: 任务类型

        Returns:
            评估结果
        """
        return self.closed_loop_evaluator.auto_evaluate_and_adjust(
            strategy_version_id=strategy_version_id,
            task_type=task_type
        )

    def run_closed_loop_cycle(self, task_type: str = None) -> Dict[str, Any]:
        """
        运行完整的闭环周期

        包括：
        1. 获取反思洞察
        2. 生成策略更新
        3. 评估策略效果
        4. 自动调整

        Args:
            task_type: 任务类型（可选，不指定则评估所有）

        Returns:
            闭环周期结果
        """
        self.logger.info(f"运行闭环周期: task_type={task_type}")

        # 1. 获取洞察
        insights = self.get_reflection_insights(days=7)

        # 2. 生成策略更新
        new_strategies = self.strategy_updater.generate_strategies_from_insights(insights)

        strategy_results = []
        for strategy in new_strategies:
            # 激活策略
            self.strategy_updater.activate_strategy(
                strategy.version_id,
                strategy.strategy_type
            )

            # 记录策略版本
            self.closed_loop_evaluator.record_strategy_version(strategy.version_id)

            strategy_results.append({
                'version_id': strategy.version_id,
                'type': strategy.strategy_type.value,
                'name': strategy.name
            })

        # 3. 评估最近策略的效果
        evaluation_results = []
        active_strategies = self.get_active_strategies()

        for st_type, strategy_info in active_strategies.items():
            if task_type and not self._strategy_matches_task(st_type, task_type):
                continue

            result = self.closed_loop_evaluator.evaluate_strategy_improvement(
                strategy_version_id=strategy_info.get('version_id', ''),
                task_type=self._task_type_from_strategy(st_type)
            )

            evaluation_results.append({
                'strategy_type': st_type,
                'evaluation': result.evaluation.value,
                'improvement_rate': result.improvement_rate,
                'recommendation': result.recommendation
            })

        return {
            'insights_generated': len(insights.get('recent_insights', [])),
            'strategies_updated': len(strategy_results),
            'strategy_details': strategy_results,
            'evaluation_results': evaluation_results,
            'timestamp': datetime.now().isoformat()
        }

    def _strategy_matches_task(self, strategy_type: str, task_type: str) -> bool:
        """检查策略类型是否匹配任务类型"""
        mapping = {
            'matching': 'appointment',
            'recommendation': 'appointment',
            'prompt': 'consultation',
            'routing': 'classification'
        }
        return mapping.get(strategy_type) == task_type

    def _task_type_from_strategy(self, strategy_type: str) -> str:
        """从策略类型推断任务类型"""
        mapping = {
            'matching': 'appointment',
            'recommendation': 'appointment',
            'prompt': 'consultation',
            'routing': 'classification'
        }
        return mapping.get(strategy_type, 'general')
