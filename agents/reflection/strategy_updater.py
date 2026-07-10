"""
策略更新器 - 基于反思结果动态调整 Agent 策略

核心功能：
1. 从反思洞察中提取可执行的策略调整（Agent 驱动）
2. 维护策略版本历史
3. 提供策略查询接口
4. 支持策略回滚

Agent 架构：
- 使用 LLM 根据反思洞察生成具体策略参数
- 保留规则引擎作为快速 fallback
- 混合模式：简单策略用规则，复杂策略用 LLM
"""

from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import logging
import asyncio

from .utils import _make_json_safe, _safe_dumps


# ==================== Agent Prompt 模板 ====================

STRATEGY_GENERATION_PROMPT = """你是一个专业的 AI 系统策略优化专家。请基于反思洞察，为按摩房预约系统生成具体的策略调整。

反思洞察：
{insights}

当前系统策略配置：
{current_strategies}

分析要求：
1. 根据反思洞察，最需要优化的是哪些策略？
2. 如何量化这些策略的改进目标？
3. 给出具体的策略参数值（如权重、阈值、超时时间等）
4. 这些策略修改后，预期会有什么风险？
5. 如何验证策略改进的效果？

返回JSON格式：
{{
    "strategy_analysis": {{
        "most_critical_strategy": "最需要优化的策略类型",
        "improvement_goal": "改进目标描述",
        "risk_assessment": "风险评估"
    }},
    "new_strategies": [
        {{
            "name": "策略名称",
            "type": "matching|recommendation|routing|prompt|timeout",
            "config": {{
                "param1": value1,
                "param2": value2
            }},
            "trigger_condition": "何时启用此策略",
            "expected_improvement": "预期改进效果",
            "confidence": 0.0-1.0
        }}
    ],
    "validation_plan": {{
        "metrics_to_track": ["指标1", "指标2"],
        "success_criteria": "成功的判定标准",
        "rollback_trigger": "回滚的触发条件"
    }},
    "summary": "策略生成总结"
}}
"""

AVOIDANCE_STRATEGY_GENERATION_PROMPT = """你是一个问题预防专家。请根据以下坏case，生成具体的预防策略。

坏case详情：
{bad_case}

当前相关策略配置：
{current_config}

分析要求：
1. 这个坏case的根本原因是什么？
2. 如何设计一个策略来避免这种情况？
3. 给出具体的策略参数（如检查逻辑、阈值、备选方案等）
4. 这个策略会影响现有功能吗？
5. 如何验证策略的有效性？

返回JSON格式：
{{
    "root_cause": "根本原因描述",
    "strategy": {{
        "name": "策略名称",
        "type": "matching|recommendation|routing|prompt|timeout",
        "config": {{
            "check_logic": "检查逻辑描述",
            "threshold": 数值,
            "fallback_action": "备选动作"
        }},
        "trigger_condition": "何时触发此策略",
        "risk_mitigation": "如何降低风险"
    }},
    "validation": {{
        "test_cases": ["测试用例1", "测试用例2"],
        "success_metric": "成功指标"
    }}
}}
"""

CONTEXTUAL_STRATEGY_PROMPT = """你是一个实时决策优化专家。请根据当前上下文，生成针对性的策略建议。

当前上下文：
{context}

历史反思洞察：
{insights}

活跃策略：
{active_strategies}

分析要求：
1. 当前上下文下，哪些策略应该调整？
2. 给出具体的参数调整值
3. 这些调整是临时的还是持久的？
4. 如何快速验证调整效果？

返回JSON格式：
{{
    "strategy_adjustments": [
        {{
            "strategy_type": "策略类型",
            "adjustments": {{
                "param1": "新值",
                "param2": "新值"
            }},
            "temporary": true/false,
            "ttl_seconds": 有效期秒数,
            "reason": "调整原因"
        }}
    ],
    "confidence": 0.0-1.0,
    "quick_validation": {{
        "what_to_check": "验证要点",
        "expected_result": "预期结果"
    }}
}}
"""


class StrategyType(Enum):
    """策略类型"""
    MATCHING = "matching"           # 技师匹配策略
    RECOMMENDATION = "recommendation" # 推荐策略
    ROUTING = "routing"              # 路由策略
    PROMPT = "prompt"                # 提示词策略
    TIMEOUT = "timeout"              # 超时策略


class StrategyStatus(Enum):
    """策略状态"""
    ACTIVE = "active"                # 生效中
    PENDING = "pending"              # 待生效
    ARCHIVED = "archived"             # 已归档
    ROLLED_BACK = "rolled_back"      # 已回滚


@dataclass
class StrategyVersion:
    """策略版本"""
    version_id: str
    strategy_type: StrategyType
    name: str
    config: Dict[str, Any]
    priority: int = 0
    trigger_reason: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    created_by: str = "system"
    status: StrategyStatus = StrategyStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)


class StrategyUpdater:
    """
    策略更新器（Agent 驱动版）

    根据反思结果动态生成和更新 Agent 策略
    使用 LLM 生成具体的策略参数
    """

    def __init__(self, reflection_repo=None, llm=None):
        self.reflection_repo = reflection_repo
        self.llm = llm
        self.logger = logging.getLogger(__name__)

        # 内存中的策略存储
        self._strategies: Dict[str, List[StrategyVersion]] = {
            st.value: [] for st in StrategyType
        }

        # 活跃策略映射
        self._active_strategies: Dict[str, StrategyVersion] = {}

        # Agent 配置
        self._agent_config = {
            'use_llm_for_strategy': True,    # 使用 LLM 生成策略
            'min_insights_for_llm': 2,         # LLM 生成最小洞察数
            'fallback_to_rules': True,         # LLM 失败时 fallback
            'cache_strategies': True,          # 缓存策略
            'strategy_cache_ttl': 1800,        # 缓存 TTL（30分钟）
            'max_strategies_per_type': 10,     # 每种策略最大数量
        }

        # 策略缓存
        self._strategy_cache: Dict[str, Dict[str, Any]] = {}

        # 初始化默认策略
        self._init_default_strategies()

    def _init_default_strategies(self) -> None:
        """初始化默认策略"""
        default_configs = {
            StrategyType.MATCHING: {
                'similarity_weight': 0.4,
                'gender_preference_weight': 0.2,
                'availability_weight': 0.3,
                'experience_weight': 0.1,
                'fallback_enabled': True,
                'max_candidates': 5
            },
            StrategyType.RECOMMENDATION: {
                'personalization_level': 0.8,
                'diversity_weight': 0.2,
                'recency_weight': 0.3,
                'cold_start_mode': 'popularity'
            },
            StrategyType.ROUTING: {
                'appointment_threshold': 0.6,
                'consultation_threshold': 0.5,
                'escalation_threshold': 0.3,
                'fallback_to_consultation': True
            },
            StrategyType.PROMPT: {
                'appointment_style': 'detailed',
                'consultation_style': 'professional',
                'error_handling': 'apologize_and_alternatives',
                'confirmation_required': True
            },
            StrategyType.TIMEOUT: {
                'max_turns': 15,
                'confirmation_timeout': 120,
                'idle_timeout': 300,
                'escalation_after_turns': 10
            }
        }

        for strategy_type, config in default_configs.items():
            version = StrategyVersion(
                version_id=f"default_{strategy_type.value}_{datetime.now().strftime('%Y%m%d')}",
                strategy_type=strategy_type,
                name=f"默认{strategy_type.value}策略",
                config=config,
                priority=0,
                trigger_reason="系统初始化",
                status=StrategyStatus.ACTIVE,
                created_by="system"
            )
            self._strategies[strategy_type.value].append(version)
            self._active_strategies[strategy_type.value] = version

    def generate_strategies_from_insights(self, insights: Dict[str, Any]) -> List[StrategyVersion]:
        """
        从反思洞察生成策略更新（Agent 驱动）

        Args:
            insights: 反思洞察数据

        Returns:
            生成的策略列表
        """
        # 检查是否应该使用 LLM
        should_use_llm = (
            self._agent_config['use_llm_for_strategy']
            and self.llm is not None
            and self._has_sufficient_insights(insights)
        )

        if should_use_llm:
            return self._generate_strategies_with_agent(insights)
        else:
            return self._generate_strategies_with_rules(insights)

    def _has_sufficient_insights(self, insights: Dict[str, Any]) -> bool:
        """检查是否有足够的洞察用于 LLM 生成"""
        recommendations = insights.get('actionable_recommendations', [])
        bad_cases = insights.get('recent_bad_cases', [])

        return (
            len(recommendations) >= self._agent_config['min_insights_for_llm']
            or len(bad_cases) >= 1
        )

    def _generate_strategies_with_agent(self, insights: Dict[str, Any]) -> List[StrategyVersion]:
        """
        使用 Agent（LLM）生成策略

        Args:
            insights: 反思洞察

        Returns:
            Agent 生成的策略列表
        """
        self.logger.info("使用 Agent 生成策略")

        # 检查缓存
        cache_key = self._generate_cache_key(insights)
        if self._agent_config['cache_strategies'] and cache_key in self._strategy_cache:
            cached = self._strategy_cache[cache_key]
            cache_age = (datetime.now() - cached.get('_cached_at', datetime.min)).seconds
            if cache_age < self._agent_config['strategy_cache_ttl']:
                self.logger.info(f"使用缓存的策略，缓存年龄: {cache_age}秒")
                return self._cache_to_strategy_versions(cached)

        try:
            # 准备数据
            prepared_insights = self._prepare_insights_for_llm(insights)
            current_strategies = self._get_current_strategies_summary()

            # 构建 Prompt
            prompt = STRATEGY_GENERATION_PROMPT.format(
                insights=_safe_dumps(prepared_insights, ensure_ascii=False, indent=2),
                current_strategies=_safe_dumps(current_strategies, ensure_ascii=False, indent=2)
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                strategies = self._parse_llm_strategies(result)

                # 缓存结果
                if self._agent_config['cache_strategies']:
                    result['_cached_at'] = datetime.now()
                    self._strategy_cache[cache_key] = result

                self.logger.info(f"Agent 生成了 {len(strategies)} 个策略")
                return strategies
            else:
                self.logger.warning("LLM 调用失败，fallback 到规则引擎")
                return self._generate_strategies_with_rules(insights)

        except json.JSONDecodeError as e:
            self.logger.error(f"LLM 返回格式错误: {e}")
            if self._agent_config['fallback_to_rules']:
                return self._generate_strategies_with_rules(insights)
            return []
        except Exception as e:
            self.logger.error(f"Agent 策略生成失败: {e}")
            if self._agent_config['fallback_to_rules']:
                return self._generate_strategies_with_rules(insights)
            return []

    def _prepare_insights_for_llm(self, insights: Dict[str, Any]) -> Dict[str, Any]:
        """准备洞察数据用于 LLM"""
        return {
            'actionable_recommendations': insights.get('actionable_recommendations', [])[:10],
            'recent_bad_cases': insights.get('recent_bad_cases', [])[:10],
            'recent_insights': insights.get('recent_insights', [])[:5],
            'summary': insights.get('summary', '')
        }

    def _get_current_strategies_summary(self) -> Dict[str, Any]:
        """获取当前策略摘要"""
        summary = {}
        for st_type in StrategyType:
            active = self._active_strategies.get(st_type.value)
            if active:
                summary[st_type.value] = {
                    'version_id': active.version_id,
                    'config': active.config,
                    'priority': active.priority
                }
        return summary

    def _generate_cache_key(self, insights: Dict[str, Any]) -> str:
        """生成缓存键"""
        import hashlib
        content = json.dumps(insights, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()

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
                        {"role": "system", "content": "你是一个专业的AI系统策略优化专家。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"}
                )
                return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"LLM 调用异常: {e}")
            return None

    def _call_llm_sync(self, prompt: str, temperature: float = 0.3) -> Optional[str]:
        """同步调用 LLM"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self._call_llm_async(prompt))
                    return future.result(timeout=30)
            else:
                return asyncio.run(self._call_llm_async(prompt))
        except Exception as e:
            self.logger.error(f"同步 LLM 调用失败: {e}")
            return None

    def _parse_llm_strategies(self, llm_result: Dict[str, Any]) -> List[StrategyVersion]:
        """解析 LLM 生成的策略"""
        strategies = []
        new_strategies = llm_result.get('new_strategies', [])

        for idx, s_data in enumerate(new_strategies):
            try:
                strategy_type = StrategyType(s_data.get('type', 'matching'))
            except ValueError:
                strategy_type = StrategyType.MATCHING

            version_id = f"agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{idx}"

            strategy = StrategyVersion(
                version_id=version_id,
                strategy_type=strategy_type,
                name=s_data.get('name', f'Agent策略{idx + 1}'),
                config=s_data.get('config', {}),
                priority=int(s_data.get('confidence', 0.5) * 10),
                trigger_reason=f"Agent生成: {s_data.get('trigger_condition', '')}",
                status=StrategyStatus.PENDING,
                created_by="agent",
                metadata={
                    'expected_improvement': s_data.get('expected_improvement', ''),
                    'trigger_condition': s_data.get('trigger_condition', ''),
                    'confidence': s_data.get('confidence', 0.5)
                }
            )
            strategies.append(strategy)

        return strategies

    def _cache_to_strategy_versions(self, cached: Dict[str, Any]) -> List[StrategyVersion]:
        """将缓存转换为策略版本"""
        strategies = []
        new_strategies = cached.get('new_strategies', [])

        for idx, s_data in enumerate(new_strategies):
            try:
                strategy_type = StrategyType(s_data.get('type', 'matching'))
            except ValueError:
                strategy_type = StrategyType.MATCHING

            version_id = f"cached_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{idx}"

            strategy = StrategyVersion(
                version_id=version_id,
                strategy_type=strategy_type,
                name=s_data.get('name', f'缓存策略{idx + 1}'),
                config=s_data.get('config', {}),
                priority=int(s_data.get('confidence', 0.5) * 10),
                trigger_reason="从缓存恢复",
                status=StrategyStatus.PENDING,
                created_by="cached"
            )
            strategies.append(strategy)

        return strategies

    def _generate_strategies_with_rules(self, insights: Dict[str, Any]) -> List[StrategyVersion]:
        """
        使用规则引擎生成策略（fallback 模式）

        Args:
            insights: 反思洞察

        Returns:
            规则生成的策略列表
        """
        self.logger.info("使用规则引擎生成策略")
        new_strategies = []

        # 1. 从坏 case 生成避免策略
        bad_cases = insights.get('recent_bad_cases', [])
        for case in bad_cases:
            strategy = self._generate_avoidance_strategy(case)
            if strategy:
                new_strategies.append(strategy)

        # 2. 从推荐生成优化策略
        recommendations = insights.get('actionable_recommendations', [])
        for rec in recommendations:
            if rec.get('priority') == 'high':
                strategy = self._generate_optimization_strategy(rec)
                if strategy:
                    new_strategies.append(strategy)

        # 3. 从模式分析生成适应策略
        pattern_insights = insights.get('pattern_insights', {})
        for pattern_type, pattern_data in pattern_insights.items():
            strategy = self._generate_adaptation_strategy(pattern_type, pattern_data)
            if strategy:
                new_strategies.append(strategy)

        return new_strategies

    def generate_avoidance_strategy_for_case(
        self,
        bad_case: Dict[str, Any]
    ) -> Optional[StrategyVersion]:
        """
        为单个坏case生成避免策略（Agent 驱动）

        Args:
            bad_case: 坏case 详情

        Returns:
            生成的避免策略
        """
        if not self.llm or not self._agent_config['use_llm_for_strategy']:
            return self._generate_avoidance_strategy(bad_case)

        self.logger.info(f"使用 Agent 为坏case生成避免策略: {bad_case.get('description', '')[:50]}")

        try:
            # 获取当前配置
            task_type = bad_case.get('task_type', 'appointment')
            if task_type == 'appointment':
                strategy_type = StrategyType.MATCHING
            elif task_type == 'consultation':
                strategy_type = StrategyType.PROMPT
            else:
                strategy_type = StrategyType.ROUTING

            current = self.get_active_strategy(strategy_type)
            current_config = current.get('config', {}) if current else {}

            # 构建 Prompt
            prompt = AVOIDANCE_STRATEGY_GENERATION_PROMPT.format(
                bad_case=_safe_dumps(bad_case, ensure_ascii=False, indent=2),
                current_config=_safe_dumps(current_config, ensure_ascii=False, indent=2)
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                strategy_data = result.get('strategy', {})

                version_id = f"avoid_agent_{datetime.now().strftime('%H%M%S')}"

                return StrategyVersion(
                    version_id=version_id,
                    strategy_type=strategy_type,
                    name=f"避免: {bad_case.get('description', '未知问题')[:50]}",
                    config=strategy_data.get('config', {}),
                    priority=8,
                    trigger_reason=f"Agent生成避免策略: {result.get('root_cause', '')}",
                    status=StrategyStatus.PENDING,
                    created_by="agent",
                    metadata={
                        'root_cause': result.get('root_cause', ''),
                        'validation': result.get('validation', {})
                    }
                )

        except Exception as e:
            self.logger.error(f"Agent 避免策略生成失败: {e}")

        # Fallback 到规则
        return self._generate_avoidance_strategy(bad_case)

    def _init_default_strategies(self) -> None:
        """初始化默认策略"""
        default_configs = {
            StrategyType.MATCHING: {
                'similarity_weight': 0.4,
                'gender_preference_weight': 0.2,
                'availability_weight': 0.3,
                'experience_weight': 0.1,
                'fallback_enabled': True,
                'max_candidates': 5
            },
            StrategyType.RECOMMENDATION: {
                'personalization_level': 0.8,
                'diversity_weight': 0.2,
                'recency_weight': 0.3,
                'cold_start_mode': 'popularity'
            },
            StrategyType.ROUTING: {
                'appointment_threshold': 0.6,
                'consultation_threshold': 0.5,
                'escalation_threshold': 0.3,
                'fallback_to_consultation': True
            },
            StrategyType.PROMPT: {
                'appointment_style': 'detailed',
                'consultation_style': 'professional',
                'error_handling': 'apologize_and_alternatives',
                'confirmation_required': True
            },
            StrategyType.TIMEOUT: {
                'max_turns': 15,
                'confirmation_timeout': 120,
                'idle_timeout': 300,
                'escalation_after_turns': 10
            }
        }

        for strategy_type, config in default_configs.items():
            version = StrategyVersion(
                version_id=f"default_{strategy_type.value}_{datetime.now().strftime('%Y%m%d')}",
                strategy_type=strategy_type,
                name=f"默认{strategy_type.value}策略",
                config=config,
                priority=0,
                trigger_reason="系统初始化",
                status=StrategyStatus.ACTIVE
            )
            self._strategies[strategy_type.value].append(version)
            self._active_strategies[strategy_type.value] = version

    def generate_strategies_from_insights(self, insights: Dict[str, Any]) -> List[StrategyVersion]:
        """
        从反思洞察生成策略更新

        Args:
            insights: 反思洞察数据

        Returns:
            生成的策略列表
        """
        new_strategies = []

        # 1. 从坏 case 生成避免策略
        bad_cases = insights.get('recent_bad_cases', [])
        for case in bad_cases:
            strategy = self._generate_avoidance_strategy(case)
            if strategy:
                new_strategies.append(strategy)

        # 2. 从推荐生成优化策略
        recommendations = insights.get('actionable_recommendations', [])
        for rec in recommendations:
            if rec.get('priority') == 'high':
                strategy = self._generate_optimization_strategy(rec)
                if strategy:
                    new_strategies.append(strategy)

        # 3. 从模式分析生成适应策略
        pattern_insights = insights.get('pattern_insights', {})
        for pattern_type, pattern_data in pattern_insights.items():
            strategy = self._generate_adaptation_strategy(pattern_type, pattern_data)
            if strategy:
                new_strategies.append(strategy)

        return new_strategies

    def _generate_avoidance_strategy(self, bad_case: Dict[str, Any]) -> Optional[StrategyVersion]:
        """从坏 case 生成避免策略（规则模式）"""
        trigger = bad_case.get('trigger', {})
        suggested_fix = bad_case.get('suggested_fix', {})

        if not suggested_fix:
            return None

        # 确定策略类型
        case_type = bad_case.get('task_type', '')
        if case_type == 'appointment':
            strategy_type = StrategyType.MATCHING
        elif case_type == 'consultation':
            strategy_type = StrategyType.PROMPT
        else:
            strategy_type = StrategyType.ROUTING

        version_id = f"avoid_{bad_case.get('case_id', 'unknown')}_{datetime.now().strftime('%H%M%S')}"

        return StrategyVersion(
            version_id=version_id,
            strategy_type=strategy_type,
            name=f"避免: {bad_case.get('description', '未知问题')[:50]}",
            config=suggested_fix,
            priority=bad_case.get('severity', 5),
            trigger_reason=f"坏case: {bad_case.get('description', '')[:100]}",
            status=StrategyStatus.PENDING,
            created_by="rules"
        )

    def _generate_optimization_strategy(self, recommendation: Dict[str, Any]) -> Optional[StrategyVersion]:
        """从推荐生成优化策略（规则模式）"""
        action = recommendation.get('action', {})
        if not action:
            return None

        strategy_type_str = action.get('type', 'matching')
        try:
            strategy_type = StrategyType(strategy_type_str)
        except ValueError:
            strategy_type = StrategyType.MATCHING

        version_id = f"opt_{recommendation.get('id', 'unknown')}_{datetime.now().strftime('%H%M%S')}"

        return StrategyVersion(
            version_id=version_id,
            strategy_type=strategy_type,
            name=f"优化: {recommendation.get('title', '推荐策略')[:50]}",
            config=action.get('parameters', {}),
            priority=10 if recommendation.get('priority') == 'high' else 5,
            trigger_reason=f"推荐: {recommendation.get('title', '')}",
            status=StrategyStatus.PENDING,
            created_by="rules"
        )

    def _generate_adaptation_strategy(
        self,
        pattern_type: str,
        pattern_data: Dict[str, Any]
    ) -> Optional[StrategyVersion]:
        """从模式分析生成适应策略（规则模式）"""
        # 根据模式类型选择策略类型
        strategy_type_map = {
            'user_preference': StrategyType.RECOMMENDATION,
            'time_pattern': StrategyType.MATCHING,
            'error_pattern': StrategyType.PROMPT,
            'success_pattern': StrategyType.ROUTING
        }

        strategy_type = strategy_type_map.get(pattern_type, StrategyType.MATCHING)

        # 生成适应配置
        adaptation_config = {
            'enabled': True,
            'pattern_type': pattern_type,
            'confidence': pattern_data.get('confidence', 0.5),
            'parameters': pattern_data.get('parameters', {})
        }

        version_id = f"adapt_{pattern_type}_{datetime.now().strftime('%H%M%S')}"

        return StrategyVersion(
            version_id=version_id,
            strategy_type=strategy_type,
            name=f"适应: {pattern_type}模式",
            config=adaptation_config,
            priority=7,
            trigger_reason=f"模式分析: {pattern_type}",
            status=StrategyStatus.PENDING,
            created_by="rules"
        )

    def get_contextual_strategy(
        self,
        context: Dict[str, Any],
        insights: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        根据当前上下文生成针对性的策略建议（Agent 驱动）

        Args:
            context: 当前上下文
            insights: 反思洞察

        Returns:
            策略调整建议
        """
        if not self.llm or not self._agent_config['use_llm_for_strategy']:
            return {}

        self.logger.info("使用 Agent 生成上下文策略")

        try:
            # 准备数据
            prepared_context = {
                'session_id': context.get('session_id', ''),
                'task_type': context.get('task_type', ''),
                'user_profile': context.get('user_profile', {}),
                'current_turn': context.get('current_turn', 0)
            }
            active_strategies = self.get_all_active_strategies()

            # 构建 Prompt
            prompt = CONTEXTUAL_STRATEGY_PROMPT.format(
                context=_safe_dumps(prepared_context, ensure_ascii=False, indent=2),
                insights=_safe_dumps(insights, ensure_ascii=False, indent=2),
                active_strategies=_safe_dumps(active_strategies, ensure_ascii=False, indent=2)
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                return {
                    'adjustments': result.get('strategy_adjustments', []),
                    'confidence': result.get('confidence', 0.0),
                    'quick_validation': result.get('quick_validation', {}),
                    '_method': 'agent'
                }

        except Exception as e:
            self.logger.error(f"上下文策略生成失败: {e}")

        return {'_method': 'fallback'}

    def activate_strategy(self, version_id: str, strategy_type: StrategyType) -> bool:
        """
        激活策略

        Args:
            version_id: 策略版本 ID
            strategy_type: 策略类型

        Returns:
            是否激活成功
        """
        strategies = self._strategies.get(strategy_type.value, [])

        # 找到目标策略
        target_strategy = None
        for s in strategies:
            if s.version_id == version_id:
                target_strategy = s
                break

        if not target_strategy:
            self.logger.warning(f"未找到策略: {version_id}")
            return False

        # 归档当前活跃策略
        current_active = self._active_strategies.get(strategy_type.value)
        if current_active:
            current_active.status = StrategyStatus.ARCHIVED

        # 激活新策略
        target_strategy.status = StrategyStatus.ACTIVE
        self._active_strategies[strategy_type.value] = target_strategy

        self.logger.info(f"激活策略: {version_id} ({strategy_type.value})")
        return True

    def rollback_strategy(self, strategy_type: StrategyType) -> bool:
        """
        回滚策略到默认版本

        Args:
            strategy_type: 策略类型

        Returns:
            是否回滚成功
        """
        default_strategy = None
        for s in self._strategies.get(strategy_type.value, []):
            if 'default' in s.version_id and s.status == StrategyStatus.ACTIVE:
                default_strategy = s
                break

        if not default_strategy:
            self.logger.warning(f"未找到默认策略: {strategy_type.value}")
            return False

        # 归档当前活跃策略
        current_active = self._active_strategies.get(strategy_type.value)
        if current_active:
            current_active.status = StrategyStatus.ROLLED_BACK

        # 激活默认策略
        default_strategy.status = StrategyStatus.ACTIVE
        self._active_strategies[strategy_type.value] = default_strategy

        self.logger.info(f"回滚策略: {strategy_type.value}")
        return True

    def get_active_strategy(self, strategy_type: StrategyType) -> Optional[Dict[str, Any]]:
        """
        获取活跃策略配置

        Args:
            strategy_type: 策略类型

        Returns:
            策略配置
        """
        active = self._active_strategies.get(strategy_type.value)
        if not active:
            return None

        return {
            'version_id': active.version_id,
            'name': active.name,
            'config': active.config,
            'priority': active.priority,
            'trigger_reason': active.trigger_reason,
            'created_at': active.created_at.isoformat()
        }

    def get_all_active_strategies(self) -> Dict[str, Dict[str, Any]]:
        """获取所有活跃策略"""
        result = {}
        for st in StrategyType:
            strategy = self.get_active_strategy(st)
            if strategy is not None:
                result[st.value] = strategy
        return result

    def apply_strategy_to_context(
        self,
        base_context: Dict[str, Any],
        task_type: str
    ) -> Dict[str, Any]:
        """
        将策略应用到上下文中

        Args:
            base_context: 基础上下文
            task_type: 任务类型

        Returns:
            应用策略后的上下文
        """
        context = base_context.copy()

        # 根据任务类型选择相关策略
        if task_type == 'appointment':
            matching_strategy = self.get_active_strategy(StrategyType.MATCHING)
            if matching_strategy:
                context['matching_config'] = matching_strategy['config']

            recommendation_strategy = self.get_active_strategy(StrategyType.RECOMMENDATION)
            if recommendation_strategy:
                context['recommendation_config'] = recommendation_strategy['config']

        elif task_type == 'consultation':
            prompt_strategy = self.get_active_strategy(StrategyType.PROMPT)
            if prompt_strategy:
                context['prompt_config'] = prompt_strategy['config']

        # 应用通用策略
        timeout_strategy = self.get_active_strategy(StrategyType.TIMEOUT)
        if timeout_strategy:
            context['timeout_config'] = timeout_strategy['config']

        routing_strategy = self.get_active_strategy(StrategyType.ROUTING)
        if routing_strategy:
            context['routing_config'] = routing_strategy['config']

        return context

    def export_strategies(self) -> Dict[str, Any]:
        """导出所有策略配置"""
        return {
            'exported_at': datetime.now().isoformat(),
            'strategies': {
                st.value: [
                    asdict(s) for s in self._strategies.get(st.value, [])
                ]
                for st in StrategyType
            },
            'active_strategies': self.get_all_active_strategies()
        }

    def import_strategies(self, data: Dict[str, Any]) -> None:
        """导入策略配置"""
        strategies_data = data.get('strategies', {})

        for st in StrategyType:
            st_str = st.value
            if st_str in strategies_data:
                for s_data in strategies_data[st_str]:
                    # 转换 datetime 字符串
                    if 'created_at' in s_data and isinstance(s_data['created_at'], str):
                        s_data['created_at'] = datetime.fromisoformat(s_data['created_at'])

                    s_data['strategy_type'] = st
                    strategy = StrategyVersion(**s_data)
                    self._strategies[st_str].append(strategy)
