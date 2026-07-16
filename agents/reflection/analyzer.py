"""
反思分析器 - 分析任务执行结果，发现模式和坏case

核心功能：
1. 分析失败任务的根因（Agent 驱动）
2. 发现用户行为模式
3. 识别坏case
4. 生成改进建议

Agent 架构：
- 使用 LLM 进行深度根因分析
- 保留规则引擎作为快速 fallback
- 混合模式：简单问题用规则，复杂问题用 LLM
"""

import json
from typing import Dict, Any, List, Optional, Union
from collections import Counter
from datetime import datetime
import logging

from .utils import _safe_dumps


# ==================== Agent Prompt 模板 ====================

ROOT_CAUSE_ANALYSIS_PROMPT = """你是一个专业的系统分析师，专注于分析 AI 对话系统的失败案例。

请分析以下按摩房预约系统的失败任务记录，找出根本原因并给出改进建议。

失败任务记录：
{failed_tasks}

系统上下文：
- 分析时间范围：{days}天
- 任务类型：{task_type}
- 总失败数：{total_failed}

分析要求：
1. 这些失败任务之间有什么共同模式？
2. 根本原因是什么？（不要只看表面错误类型，要深入分析）
3. 失败的原因链是什么？（如：用户行为 → 系统响应 → 失败）
4. 如果你是系统设计者，会从哪些方面改进？
5. 给出3-5个具体的、可执行的改进建议。

返回JSON格式：
{{
    "patterns": [
        {{
            "type": "模式类型",
            "description": "模式描述",
            "confidence": 0.0-1.0,
            "evidence": ["支持证据1", "支持证据2"]
        }}
    ],
    "root_causes": [
        {{
            "cause": "原因描述",
            "confidence": 0.0-1.0,
            "impact": "high/medium/low",
            "suggestion": "具体改进建议",
            "reasoning": "推理过程"
        }}
    ],
    "recommendations": [
        {{
            "priority": "high/medium/low",
            "action": "具体行动描述",
            "expected_impact": "预期改进效果",
            "implementation_hint": "实现提示"
        }}
    ],
    "summary": "总结分析结论"
}}
"""

USER_PATTERN_DISCOVERY_PROMPT = """你是一个用户行为分析师。请分析以下用户行为数据，发现用户模式和偏好。

用户行为数据：
{user_data}

分析要求：
1. 用户的主要行为模式是什么？
2. 用户偏好有什么特点？
3. 哪些因素会影响用户的预约决策？
4. 用户在什么情况下容易放弃？
5. 如何个性化优化用户体验？

返回JSON格式：
{{
    "patterns": [
        {{
            "type": "行为模式类型",
            "description": "模式描述",
            "confidence": 0.0-1.0,
            "frequency": "出现频率"
        }}
    ],
    "preferences": {{
        "time_preference": "时间偏好描述",
        "technician_preference": "技师偏好描述",
        "service_preference": "服务偏好描述"
    }},
    "pain_points": ["用户痛点列表"],
    "personalization_suggestions": ["个性化建议列表"],
    "summary": "总结分析结论"
}}
"""

BAD_CASE_DEEP_ANALYSIS_PROMPT = """你是一个质量保证专家。请深入分析以下坏case，找出问题的本质并提出解决方案。

坏case记录：
{bad_cases}

坏case上下文：
- 时间范围：{days}天
- 总数：{total_cases}

分析要求：
1. 这些坏case的根本原因是什么？
2. 它们之间有什么共同点？
3. 如何从系统层面预防这类问题？
4. 如果要设计一个规则来避免这类问题，应该怎么做？
5. 给出具体的预防策略。

返回JSON格式：
{{
    "root_cause_analysis": {{
        "primary_cause": "主要原因",
        "secondary_causes": ["次要原因列表"],
        "trigger_conditions": ["触发条件列表"],
        "confidence": 0.0-1.0
    }},
    "prevention_strategies": [
        {{
            "strategy": "预防策略描述",
            "trigger": "何时触发",
            "implementation": "如何实现",
            "effectiveness": "预期效果"
        }}
    ],
    "typical_cases": [
        {{
            "description": "典型案例描述",
            "category": "问题类别",
            "fix": "修复建议"
        }}
    ],
    "system_improvements": ["系统级改进建议"],
    "summary": "总结分析结论"
}}
"""


class ReflectionAnalyzer:
    """反思分析器（Agent 驱动版）"""

    def __init__(self, evaluation_repo=None, reflection_repo=None, llm=None):
        self.evaluation_repo = evaluation_repo
        self.reflection_repo = reflection_repo
        self.llm = llm
        self.logger = logging.getLogger(__name__)

        # Agent 配置
        self._agent_config = {
            'use_llm_for_complex_cases': True,  # 复杂案例使用 LLM
            'min_samples_for_llm': 5,             # LLM 分析最小样本量
            'fallback_to_rules': True,            # LLM 失败时 fallback 到规则
            'cache_llm_results': True,            # 缓存 LLM 结果
            'llm_cache_ttl': 3600,               # 缓存 TTL（秒）
        }

        # LLM 结果缓存
        self._llm_cache: Dict[str, Dict[str, Any]] = {}

    async def analyze_failed_tasks(self, task_type: str = None, days: int = 7) -> Dict[str, Any]:
        """
        分析失败任务，找出根因（Agent 驱动）

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

        # 检查是否应该使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_complex_cases']
            and len(failed_evaluations) >= self._agent_config['min_samples_for_llm']
            and self.llm is not None
        )

        if should_use_llm:
            return await self._analyze_with_agent(failed_evaluations, task_type, days)
        else:
            return self._analyze_with_rules(failed_evaluations, task_type, days)

    async def _analyze_with_agent(
        self,
        evaluations: List[Dict],
        task_type: Optional[str],
        days: int
    ) -> Dict[str, Any]:
        """
        使用 Agent（LLM）进行深度根因分析

        Args:
            evaluations: 失败评估记录
            task_type: 任务类型
            days: 分析天数

        Returns:
            Agent 分析结果
        """
        self.logger.info(f"使用 Agent 分析 {len(evaluations)} 个失败案例")

        # 检查缓存
        cache_key = f"root_cause_{task_type}_{days}_{len(evaluations)}"
        if self._agent_config['cache_llm_results'] and cache_key in self._llm_cache:
            cached = self._llm_cache[cache_key]
            cached_at = cached.get('_cached_at')
            if cached_at is not None:
                cache_age = (datetime.now() - cached_at).total_seconds()
                if cache_age < self._agent_config['llm_cache_ttl']:
                    self.logger.info(f"使用缓存的分析结果，缓存年龄: {cache_age:.0f}秒")
                    return cached

        try:
            # 准备数据
            failed_tasks = self._prepare_failed_tasks_data(evaluations)

            # 构建 Prompt
            prompt = ROOT_CAUSE_ANALYSIS_PROMPT.format(
                failed_tasks=_safe_dumps(failed_tasks, ensure_ascii=False, indent=2),
                days=days,
                task_type=task_type or "all",
                total_failed=len(evaluations)
            )

            # 调用 LLM（直接 await，不需要 asyncio.run）
            response = await self._call_llm(prompt)

            # 解析结果
            if response:
                result = json.loads(response)
                result['_analysis_method'] = 'agent'
                result['_total_failed'] = len(evaluations)

                # 补充统计数据
                error_types = Counter(e.get('error_type') for e in evaluations)
                result['error_type_distribution'] = dict(error_types)

                # 缓存结果
                if self._agent_config['cache_llm_results']:
                    result['_cached_at'] = datetime.now()
                    self._llm_cache[cache_key] = result

                return result
            else:
                self.logger.warning("LLM 调用失败，fallback 到规则引擎")
                return self._analyze_with_rules(evaluations, task_type, days)

        except json.JSONDecodeError as e:
            self.logger.error(
                f"LLM 返回格式错误: {e}，原始响应前200字符: {response[:200] if response else 'None'!r}"
            )
            return self._analyze_with_rules(evaluations, task_type, days)
        except Exception as e:
            self.logger.error(f"Agent 分析失败: {e}")
            if self._agent_config['fallback_to_rules']:
                return self._analyze_with_rules(evaluations, task_type, days)
            else:
                return {"error": str(e), "_analysis_method": "agent_failed"}

    async def _call_llm(self, prompt: str, temperature: float = 0.3) -> Optional[str]:
        """
        调用 LLM

        Args:
            prompt: 提示词
            temperature: 温度参数

        Returns:
            LLM 响应内容
        """
        if not self.llm:
            return None

        try:
            # 使用 LangChain 或直接调用
            if hasattr(self.llm, 'ainvoke'):
                # LangChain 异步调用（带超时保护）
                import asyncio
                response = await asyncio.wait_for(
                    self.llm.ainvoke(prompt),
                    timeout=30.0
                )
                return response.content if hasattr(response, 'content') else str(response)
            elif hasattr(self.llm, 'invoke'):
                # LangChain 同步调用
                response = self.llm.invoke(prompt)
                return response.content if hasattr(response, 'content') else str(response)
            else:
                # 原始 OpenAI API（带超时保护）
                import asyncio
                response = await asyncio.wait_for(
                    self.llm.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": "你是一个专业的系统分析师。"},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=temperature,
                        response_format={"type": "json_object"}
                    ),
                    timeout=30.0
                )
                return response.choices[0].message.content
        except asyncio.TimeoutError:
            self.logger.warning("LLM 调用超时（30s）")
            return None
        except Exception as e:
            self.logger.error(f"LLM 调用异常: {e}")
            return None

    def _prepare_failed_tasks_data(self, evaluations: List[Dict]) -> List[Dict[str, Any]]:
        """
        准备失败任务数据（限制 token 数量）

        Args:
            evaluations: 原始评估记录

        Returns:
            精简后的数据
        """
        # 限制数量，避免 token 过多
        max_samples = 30
        samples = evaluations[-max_samples:] if len(evaluations) > max_samples else evaluations

        prepared = []
        for e in samples:
            prepared.append({
                'session_id': e.get('session_id', '')[:8],
                'task_type': e.get('task_type', ''),
                'success_rate': e.get('success_rate', 0),
                'turns_count': e.get('turns_count', 0),
                'completion_time': e.get('completion_time', 0),
                'error_type': e.get('error_type', ''),
                'error_message': e.get('error_message', ''),
                'created_at': e.get('created_at', ''),
                'action_data': e.get('action_data', {})
            })

        return prepared

    def _analyze_with_rules(
        self,
        evaluations: List[Dict],
        task_type: Optional[str],
        days: int
    ) -> Dict[str, Any]:
        """
        使用规则引擎分析（fallback 模式）

        Args:
            evaluations: 失败评估记录
            task_type: 任务类型
            days: 分析天数

        Returns:
            规则引擎分析结果
        """
        # 统计错误类型分布
        error_types = Counter(e.get('error_type') for e in evaluations)
        error_type_patterns = self._analyze_error_patterns(list(error_types.keys()))

        # 分析失败模式
        failure_patterns = self._identify_failure_patterns(evaluations)

        # 生成根因分析
        root_causes = self._generate_root_causes(error_types, evaluations)

        return {
            "total_failed": len(evaluations),
            "error_type_distribution": dict(error_types),
            "patterns": failure_patterns,
            "root_causes": root_causes,
            "recommendations": self._generate_recommendations(root_causes, failure_patterns),
            "_analysis_method": "rules"
        }

    async def discover_user_patterns(self, user_id: str = "default_user", days: int = 30) -> Dict[str, Any]:
        """
        发现用户行为模式（Agent 驱动）

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

        if not evaluations:
            return {
                "total_sessions": 0,
                "task_type_distribution": {},
                "avg_turns": 0,
                "insights": [],
                "summary": "暂无用户行为数据"
            }

        # 检查是否应该使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_complex_cases']
            and len(evaluations) >= self._agent_config['min_samples_for_llm']
            and self.llm is not None
        )

        if should_use_llm:
            return await self._discover_patterns_with_agent(evaluations, user_id, days)
        else:
            return self._discover_patterns_with_rules(evaluations, days)

    async def _discover_patterns_with_agent(
        self,
        evaluations: List[Dict],
        user_id: str,
        days: int
    ) -> Dict[str, Any]:
        """
        使用 Agent 发现用户模式

        Args:
            evaluations: 评估记录
            user_id: 用户ID
            days: 分析天数

        Returns:
            Agent 分析结果
        """
        self.logger.info(f"使用 Agent 发现用户模式，分析 {len(evaluations)} 条记录")

        # 检查缓存
        cache_key = f"user_patterns_{user_id}_{days}_{len(evaluations)}"
        if self._agent_config['cache_llm_results'] and cache_key in self._llm_cache:
            cached = self._llm_cache[cache_key]
            cached_at = cached.get('_cached_at')
            if cached_at is not None:
                cache_age = (datetime.now() - cached_at).total_seconds()
                if cache_age < self._agent_config['llm_cache_ttl']:
                    self.logger.info(f"使用缓存的用户模式结果，缓存年龄: {cache_age:.0f}秒")
                    return cached

        try:
            # 准备数据
            user_data = self._prepare_user_pattern_data(evaluations)

            # 构建 Prompt
            prompt = USER_PATTERN_DISCOVERY_PROMPT.format(
                user_data=_safe_dumps(user_data, ensure_ascii=False, indent=2)
            )

            # 调用 LLM
            response = await self._call_llm(prompt)

            if response:
                result = json.loads(response)
                result['_analysis_method'] = 'agent'

                # 补充统计数据
                result['total_sessions'] = len(set(e.get('session_id') for e in evaluations))
                result['task_type_distribution'] = dict(Counter(e.get('task_type') for e in evaluations))
                result['avg_turns'] = round(sum(e.get('turns_count', 0) for e in evaluations) / len(evaluations), 2)

                # 缓存
                if self._agent_config['cache_llm_results']:
                    result['_cached_at'] = datetime.now()
                    self._llm_cache[cache_key] = result

                return result
            else:
                return self._discover_patterns_with_rules(evaluations, days)

        except Exception as e:
            self.logger.error(f"Agent 用户模式分析失败: {e}")
            return self._discover_patterns_with_rules(evaluations, days)

    def _prepare_user_pattern_data(self, evaluations: List[Dict]) -> Dict[str, Any]:
        """准备用户模式数据"""
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

        # 分析时间模式
        time_patterns = self._analyze_time_patterns(evaluations)

        return {
            "total_interactions": len(evaluations),
            "task_type_distribution": dict(task_types),
            "success_rate_trend": success_rates[-20:],
            "avg_turns": round(avg_turns, 2),
            "turns_distribution": {
                "min": min(turns_distribution) if turns_distribution else 0,
                "max": max(turns_distribution) if turns_distribution else 0,
                "avg": round(avg_turns, 2)
            },
            "time_patterns": time_patterns
        }

    def _discover_patterns_with_rules(self, evaluations: List[Dict], days: int) -> Dict[str, Any]:
        """使用规则发现用户模式"""
        task_types = Counter(e.get('task_type') for e in evaluations)
        success_rates = []
        for eval_data in evaluations:
            success_rates.append({
                'date': eval_data.get('created_at', '')[:10],
                'success_rate': eval_data.get('success_rate', 0),
                'task_type': eval_data.get('task_type')
            })

        turns_distribution = [e.get('turns_count', 0) for e in evaluations]
        avg_turns = sum(turns_distribution) / len(turns_distribution) if turns_distribution else 0
        time_patterns = self._analyze_time_patterns(evaluations)

        return {
            "total_sessions": len(set(e.get('session_id') for e in evaluations)),
            "task_type_distribution": dict(task_types),
            "avg_turns": round(avg_turns, 2),
            "success_rate_trend": success_rates[-10:] if len(success_rates) > 10 else success_rates,
            "time_patterns": time_patterns,
            "insights": self._generate_pattern_insights(task_types, avg_turns, time_patterns),
            "_analysis_method": "rules"
        }

    def _sync_call_llm(self, prompt: str, temperature: float = 0.3) -> Optional[str]:
        """同步调用 LLM"""
        import asyncio

        async def _async_call():
            return await self._call_llm(prompt, temperature)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有事件循环中：在子线程创建独立事件循环运行异步方法
                import concurrent.futures

                async def _wrapper():
                    return await _async_call()

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(asyncio.run, _wrapper())
                    return future.result(timeout=30)
            else:
                return asyncio.run(_async_call())
        except Exception as e:
            self.logger.error(f"同步 LLM 调用失败: {e}")
            return None

    async def analyze_bad_cases(self, days: int = 30) -> Dict[str, Any]:
        """
        分析坏case（Agent 驱动）

        数据源优先级：
        1. task_evaluations 表的成功==0 行（首要数据源，最可靠 — 鸡生蛋修复）
        2. reflection_logs 表的 bad_cases 字段（次要数据源，作为历史补充）

        修复背景：
        旧实现只从 reflection_logs.bad_cases 读，但 reflection_logs.bad_cases
        本身是由本方法写回去的 → 第一次跑永远是空，提取率 0%。
        改成优先从 task_evaluations 读 → 每次跑评测都有新失败行进来。

        Args:
            days: 分析时间范围

        Returns:
            坏case分析结果
        """
        bad_cases: List[Dict[str, Any]] = []

        # 数据源 1：task_evaluations 的失败行（最可靠）
        if self.evaluation_repo:
            try:
                failed_evals = self.evaluation_repo.get_failed_evaluations(
                    task_type="appointment", days=days, limit=50
                )
                for ev in failed_evals:
                    bad_cases.append({
                        "case_id": f"eval_{ev.get('id')}",
                        "session_id": ev.get("session_id"),
                        "task_type": ev.get("task_type"),
                        "error_type": ev.get("error_type"),
                        "error_message": ev.get("error_message"),
                        "description": (
                            f"[{ev.get('error_type') or 'unknown'}] "
                            f"{ev.get('error_message') or 'no message'}"
                        )[:200],
                        "created_at": ev.get("created_at"),
                        "source": "task_evaluations",
                    })
            except Exception as e:
                self.logger.warning(f"从 task_evaluations 读失败行失败: {e}")

        # 数据源 2：reflection_logs.bad_cases（历史补充）
        if self.reflection_repo:
            try:
                historical = self.reflection_repo.get_all_bad_cases(days=days)
                for bc in historical:
                    if isinstance(bc, dict):
                        bc.setdefault("source", "reflection_logs")
                        bad_cases.append(bc)
            except Exception as e:
                self.logger.warning(f"从 reflection_logs 读坏case失败: {e}")

        if not bad_cases:
            return {
                "total_cases": 0,
                "cases": [],
                "summary": "没有记录的坏case（task_evaluations 和 reflection_logs 都为空）",
            }

        # 兜底：把所有元素统一为 dict（防御历史 reflection_logs 里写过字符串的情况）
        normalized: List[Dict[str, Any]] = []
        for bc in bad_cases:
            if isinstance(bc, dict):
                normalized.append(bc)
            else:
                normalized.append({
                    "description": str(bc)[:200],
                    "category": "unknown",
                    "source": "unstructured",
                })
        bad_cases = normalized

        # 检查是否应该使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_complex_cases']
            and len(bad_cases) >= 3
            and self.llm is not None
        )

        if should_use_llm:
            return await self._analyze_bad_cases_with_agent(bad_cases, days)
        else:
            return self._analyze_bad_cases_with_rules(bad_cases, days)

    async def _analyze_bad_cases_with_agent(
        self,
        bad_cases: List[Dict],
        days: int
    ) -> Dict[str, Any]:
        """
        使用 Agent 分析坏case

        Args:
            bad_cases: 坏case 列表
            days: 分析天数

        Returns:
            Agent 分析结果
        """
        self.logger.info(f"使用 Agent 分析 {len(bad_cases)} 个坏case")

        # 检查缓存
        cache_key = f"bad_cases_{days}_{len(bad_cases)}"
        if self._agent_config['cache_llm_results'] and cache_key in self._llm_cache:
            cached = self._llm_cache[cache_key]
            cached_at = cached.get('_cached_at')
            if cached_at is not None:
                cache_age = (datetime.now() - cached_at).total_seconds()
                if cache_age < self._agent_config['llm_cache_ttl']:
                    self.logger.info(f"使用缓存的坏case分析结果")
                    return cached

        try:
            # 准备数据
            prepared_cases = self._prepare_bad_cases_data(bad_cases)

            # 构建 Prompt
            prompt = BAD_CASE_DEEP_ANALYSIS_PROMPT.format(
                bad_cases=_safe_dumps(prepared_cases, ensure_ascii=False, indent=2),
                days=days,
                total_cases=len(bad_cases)
            )

            # 调用 LLM（直接 await）
            response = await self._call_llm(prompt)

            if response:
                result = json.loads(response)
                result['_analysis_method'] = 'agent'

                # 缓存
                if self._agent_config['cache_llm_results']:
                    result['_cached_at'] = datetime.now()
                    self._llm_cache[cache_key] = result

                return result
            else:
                return self._analyze_bad_cases_with_rules(bad_cases, days)

        except json.JSONDecodeError as e:
            self.logger.error(
                f"LLM 返回格式错误: {e}，原始响应前200字符: {response[:200] if response else 'None'!r}"
            )
            return self._analyze_bad_cases_with_rules(bad_cases, days)
        except Exception as e:
            self.logger.error(f"Agent 坏case分析失败: {e}")
            return self._analyze_bad_cases_with_rules(bad_cases, days)

    def _prepare_bad_cases_data(self, bad_cases: List[Dict]) -> List[Dict[str, Any]]:
        """准备坏case数据"""
        prepared = []
        for bc in bad_cases[:20]:  # 限制数量
            prepared.append({
                'description': bc.get('description', ''),
                'category': bc.get('category', ''),
                'task_type': bc.get('task_type', ''),
                'trigger': bc.get('trigger', {}),
                'suggested_fix': bc.get('suggested_fix', {}),
                'created_at': bc.get('created_at', '')
            })
        return prepared

    def _analyze_bad_cases_with_rules(
        self,
        bad_cases: List[Dict],
        days: int
    ) -> Dict[str, Any]:
        """使用规则分析坏case"""
        case_categories = Counter(bc.get('category', 'unknown') for bc in bad_cases)
        typical_cases = self._analyze_typical_bad_cases(bad_cases)

        return {
            "total_cases": len(bad_cases),
            "category_distribution": dict(case_categories),
            "typical_cases": typical_cases,
            "improvement_suggestions": self._generate_case_improvements(typical_cases),
            "_analysis_method": "rules"
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

        # 基于错误类型推断根因（Counter.get() 返回 None 而非 0，需要提供默认值）
        if error_types.get('timeout', 0) > 0:
            causes.append({
                "cause": "LLM响应超时",
                "impact": "high",
                "suggestion": "考虑增加超时时间或优化LLM调用"
            })

        if error_types.get('slot_unavailable', 0) > 0:
            causes.append({
                "cause": "时间段冲突",
                "impact": "medium",
                "suggestion": "优化时间段推荐逻辑，提前检测可用性"
            })

        if error_types.get('parse_error', 0) > 0:
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
