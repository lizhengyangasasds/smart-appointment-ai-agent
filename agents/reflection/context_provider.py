"""
反思上下文提供者 - 为 Agent 提供结构化的反思上下文

核心功能：
1. 将反思洞察转换为 Agent 可理解的上下文（Agent 驱动）
2. 构建包含历史经验的对话上下文
3. 提供实时的策略调整建议
4. 支持提示词动态注入

Agent 架构：
- 使用 LLM 将反思洞察转化为自然语言指导
- 混合模式：简单场景用模板，复杂场景用 LLM
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import asyncio


# ==================== Agent Prompt 模板 ====================

CONTEXT_GENERATION_PROMPT = """你是一个专业的 AI 对话指导专家。请将以下反思洞察转化为按摩房预约 Agent 的实时决策指导。

当前场景信息：
{current_context}

反思洞察：
{insights}

历史坏case：
{bad_cases}

当前活跃策略：
{active_strategies}

指导要求：
1. 结合历史经验和当前场景，给出具体的决策指导
2. 明确告诉 Agent 应该做什么和避免什么
3. 提供具体的数值建议（如推荐几个技师候选、追问次数等）
4. 语气自然，像是经验丰富的同事在给建议
5. 如果当前场景有风险，要明确警告

返回JSON格式：
{{
    "guidance": "自然语言指导（100字以内）",
    "do_list": ["应该做的事1", "应该做的事2"],
    "avoid_list": ["应该避免的事1", "应该避免的事2"],
    "specific_suggestions": {{
        "recommendation_count": 数量,
        "max_turns": 最大轮数,
        "timeout_seconds": 超时秒数,
        "style": "回复风格建议"
    }},
    "risk_warning": "如果有风险，给出警告",
    "confidence": 0.0-1.0
}}
"""

STRATEGY_INJECTION_PROMPT = """你是一个提示词工程专家。请将以下策略配置和洞察转化为一段提示词注入文本，用于指导 AI 对话系统。

策略配置：
{strategy_config}

反思洞察：
{insights}

用户当前请求：
{user_request}

生成要求：
1. 将策略和洞察转化为自然语言指导
2. 长度控制在 200 字以内
3. 避免使用技术术语，用业务语言表达
4. 如果有优先级，要突出重点

返回格式：
{{"injection": "生成的注入文本..."}}
"""


class ContextFormat(Enum):
    """上下文格式"""
    COMPACT = "compact"       # 紧凑格式（用于提示词）
    DETAILED = "detailed"    # 详细格式（用于调试）
    ACTIONABLE = "actionable" # 可执行格式（用于决策）


@dataclass
class ReflectionContext:
    """反思上下文"""
    # 基本信息
    session_id: str
    task_type: str
    timestamp: datetime = field(default_factory=datetime.now)

    # 洞察数据
    recent_insights: List[str] = field(default_factory=list)
    recommendations: List[Dict[str, Any]] = field(default_factory=list)
    bad_cases: List[Dict[str, Any]] = field(default_factory=list)
    patterns: List[str] = field(default_factory=list)

    # 策略配置
    active_strategy: Optional[Dict[str, Any]] = None
    strategy_adjustments: Dict[str, Any] = field(default_factory=dict)

    # 上下文文本
    context_text: str = ""
    prompt_injection: str = ""

    # Agent 生成的内容
    agent_guidance: str = ""
    do_list: List[str] = field(default_factory=list)
    avoid_list: List[str] = field(default_factory=list)
    specific_suggestions: Dict[str, Any] = field(default_factory=dict)

    # 元数据
    confidence: float = 0.0
    data_sources: List[str] = field(default_factory=list)
    generation_method: str = "template"  # template / agent


class ReflectionContextProvider:
    """
    反思上下文提供者（Agent 驱动版）

    将反思洞察转换为 Agent 可直接使用的上下文
    """

    def __init__(
        self,
        reflection_engine=None,
        strategy_updater=None,
        closed_loop_evaluator=None,
        llm=None
    ):
        self.reflection_engine = reflection_engine
        self.strategy_updater = strategy_updater
        self.closed_loop_evaluator = closed_loop_evaluator
        self.llm = llm
        self.logger = logging.getLogger(__name__)

        # 上下文缓存
        self._context_cache: Dict[str, ReflectionContext] = {}
        self._cache_ttl = 60  # 1分钟缓存

        # Agent 配置
        self._agent_config = {
            'use_llm_for_guidance': True,   # 使用 LLM 生成指导
            'min_complexity_for_llm': 3,     # 复杂度阈值
            'cache_llm_results': True,      # 缓存 LLM 结果
            'llm_cache_ttl': 300,           # 缓存 TTL（5分钟）
        }

        # LLM 结果缓存
        self._llm_cache: Dict[str, Dict[str, Any]] = {}

    def get_context_for_agent(
        self,
        session_id: str,
        task_type: str,
        format: ContextFormat = ContextFormat.COMPACT,
        use_agent: bool = None
    ) -> ReflectionContext:
        """
        为 Agent 获取反思上下文

        Args:
            session_id: 会话 ID
            task_type: 任务类型
            format: 上下文格式
            use_agent: 是否强制使用 Agent（None=自动判断）

        Returns:
            反思上下文
        """
        # 检查缓存
        cache_key = f"{session_id}_{task_type}_{format.value}"
        cached = self._context_cache.get(cache_key)

        if cached:
            age = (datetime.now() - cached.timestamp).seconds
            if age < self._cache_ttl:
                return cached

        # 构建新上下文
        context = self._build_context(session_id, task_type, format, use_agent)

        # 更新缓存
        self._context_cache[cache_key] = context

        return context

    def _build_context(
        self,
        session_id: str,
        task_type: str,
        format: ContextFormat,
        use_agent: bool = None
    ) -> ReflectionContext:
        """构建反思上下文"""
        context = ReflectionContext(
            session_id=session_id,
            task_type=task_type
        )

        # 1. 获取反思洞察
        if self.reflection_engine:
            insights = self.reflection_engine.get_reflection_insights(days=7)
            context.recent_insights = insights.get('recent_insights', [])
            context.recommendations = insights.get('actionable_recommendations', [])
            context.bad_cases = insights.get('recent_bad_cases', [])
            context.data_sources.append('reflection_engine')
        else:
            insights = {}
            context.recent_insights = []
            context.recommendations = []
            context.bad_cases = []

        # 2. 获取任务类型特定的洞察
        if self.reflection_engine:
            task_insights = self.reflection_engine.get_task_type_insights(task_type)
            context.patterns = task_insights.get('patterns', [])
            context.data_sources.append('task_specific')

        # 3. 获取活跃策略
        if self.strategy_updater:
            from .strategy_updater import StrategyType

            if task_type == 'appointment':
                strategy_type = StrategyType.MATCHING
            elif task_type == 'consultation':
                strategy_type = StrategyType.PROMPT
            else:
                strategy_type = StrategyType.ROUTING

            context.active_strategy = self.strategy_updater.get_active_strategy(strategy_type)
            context.strategy_adjustments = context.active_strategy.get('config', {}) if context.active_strategy else {}
            context.data_sources.append('strategy_updater')

        # 4. 自动判断是否使用 Agent
        complexity = self._calculate_complexity(context)
        if use_agent is None:
            use_agent = (
                self._agent_config['use_llm_for_guidance']
                and complexity >= self._agent_config['min_complexity_for_llm']
                and self.llm is not None
            )

        # 5. 生成上下文文本
        if use_agent and self.llm:
            self._generate_context_with_agent(context, insights)
        else:
            self._generate_context_with_template(context, format)

        # 6. 计算置信度
        context.confidence = self._calculate_confidence(context)

        return context

    def _calculate_complexity(self, context: ReflectionContext) -> int:
        """计算上下文复杂度"""
        complexity = 0

        # 基于洞察数量
        complexity += min(3, len(context.recommendations))
        complexity += min(3, len(context.bad_cases))

        # 基于策略数量
        if context.active_strategy:
            config = context.active_strategy.get('config', {})
            complexity += min(2, len(config))

        # 基于模式数量
        complexity += min(2, len(context.patterns))

        return complexity

    def _generate_context_with_agent(self, context: ReflectionContext, insights: Dict[str, Any]) -> None:
        """
        使用 Agent（LLM）生成上下文

        Args:
            context: 反思上下文
            insights: 反思洞察
        """
        self.logger.info("使用 Agent 生成反思上下文")

        # 检查缓存
        cache_key = f"context_{context.task_type}_{len(context.recommendations)}_{len(context.bad_cases)}"
        if self._agent_config['cache_llm_results'] and cache_key in self._llm_cache:
            cached = self._llm_cache[cache_key]
            context.agent_guidance = cached.get('guidance', '')
            context.do_list = cached.get('do_list', [])
            context.avoid_list = cached.get('avoid_list', [])
            context.specific_suggestions = cached.get('specific_suggestions', {})
            context.generation_method = 'agent_cached'
            return

        try:
            # 准备数据
            current_context = {
                'session_id': context.session_id,
                'task_type': context.task_type,
                'has_recommendations': len(context.recommendations) > 0,
                'has_bad_cases': len(context.bad_cases) > 0,
                'has_patterns': len(context.patterns) > 0
            }

            active_strategies = {}
            if context.active_strategy:
                active_strategies = {
                    'version_id': context.active_strategy.get('version_id', ''),
                    'config': context.active_strategy.get('config', {})
                }

            # 构建 Prompt
            prompt = CONTEXT_GENERATION_PROMPT.format(
                current_context=json.dumps(current_context, ensure_ascii=False, indent=2),
                insights=json.dumps(context.recommendations[:5], ensure_ascii=False, indent=2),
                bad_cases=json.dumps(context.bad_cases[:3], ensure_ascii=False, indent=2),
                active_strategies=json.dumps(active_strategies, ensure_ascii=False, indent=2)
            )

            # 调用 LLM
            response = self._call_llm_sync(prompt)

            if response:
                result = json.loads(response)
                context.agent_guidance = result.get('guidance', '')
                context.do_list = result.get('do_list', [])
                context.avoid_list = result.get('avoid_list', [])
                context.specific_suggestions = result.get('specific_suggestions', {})
                context.generation_method = 'agent'

                # 缓存
                if self._agent_config['cache_llm_results']:
                    self._llm_cache[cache_key] = result

                self.logger.info(f"Agent 生成了指导: {context.agent_guidance[:50]}...")
            else:
                self._generate_context_with_template(context, ContextFormat.COMPACT)

        except Exception as e:
            self.logger.error(f"Agent 上下文生成失败: {e}")
            self._generate_context_with_template(context, ContextFormat.COMPACT)

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
                        {"role": "system", "content": "你是一个专业的AI对话指导专家。"},
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

    def _generate_compact_text(self, context: ReflectionContext) -> str:
        """生成紧凑格式文本"""
        parts = []

        # 添加洞察摘要
        if context.recent_insights:
            parts.append(f"【历史洞察】{'; '.join(context.recent_insights[:3])}")

        # 添加高优先级建议
        high_priority = [r for r in context.recommendations if r.get('priority') == 'high']
        if high_priority:
            rec_texts = [r.get('title', r.get('description', '')) for r in high_priority[:2]]
            parts.append(f"【当前建议】{'; '.join(rec_texts)}")

        # 添加需要避免的模式
        if context.bad_cases:
            avoid_texts = [bc.get('description', '')[:30] for bc in context.bad_cases[:2]]
            parts.append(f"【避免问题】{'; '.join(avoid_texts)}")

        return '\n'.join(parts) if parts else "暂无反思数据"

    def _generate_detailed_text(self, context: ReflectionContext) -> str:
        """生成详细格式文本"""
        lines = ["=== 反思上下文详情 ==="]

        # 洞察列表
        if context.recent_insights:
            lines.append("\n【历史洞察】")
            for insight in context.recent_insights:
                lines.append(f"  - {insight}")

        # 推荐列表
        if context.recommendations:
            lines.append("\n【可执行建议】")
            for i, rec in enumerate(context.recommendations[:5], 1):
                lines.append(f"  {i}. {rec.get('title', rec.get('description', ''))}")
                if rec.get('action'):
                    lines.append(f"     操作: {rec['action']}")

        # 坏 case 列表
        if context.bad_cases:
            lines.append("\n【需要避免的 case】")
            for bc in context.bad_cases[:3]:
                lines.append(f"  - {bc.get('description', '')}")
                if bc.get('suggested_fix'):
                    lines.append(f"    建议: {bc['suggested_fix']}")

        # 活跃策略
        if context.active_strategy:
            lines.append("\n【当前策略】")
            lines.append(f"  版本: {context.active_strategy.get('version_id', 'unknown')}")
            lines.append(f"  配置: {context.active_strategy.get('config', {})}")

        return '\n'.join(lines)

    def _generate_actionable_text(self, context: ReflectionContext) -> str:
        """生成可执行格式文本"""
        actions = []

        # 从建议生成可执行操作
        for rec in context.recommendations[:3]:
            if rec.get('action'):
                actions.append({
                    'type': 'recommendation',
                    'description': rec.get('title', ''),
                    'action': rec['action'],
                    'priority': rec.get('priority', 'normal')
                })

        # 从坏 case 生成避免操作
        for bc in context.bad_cases[:2]:
            if bc.get('suggested_fix'):
                actions.append({
                    'type': 'avoidance',
                    'description': bc.get('description', ''),
                    'action': bc['suggested_fix'],
                    'priority': 'high'
                })

        # 转换为文本
        lines = ["=== 可执行操作 ==="]
        for action in actions:
            lines.append(f"[{action['priority'].upper()}] {action['description']}")
            lines.append(f"  操作: {action['action']}")

        return '\n'.join(lines)

    def _generate_prompt_injection(self, context: ReflectionContext) -> str:
        """生成提示词注入片段"""
        injections = []

        # 基于洞察添加指导
        if context.recent_insights:
            injections.append(
                f"参考以下历史经验: {'; '.join(context.recent_insights[:2])}"
            )

        # 基于坏 case 添加警告
        if context.bad_cases:
            avoid_patterns = [bc.get('description', '')[:40] for bc in context.bad_cases[:2]]
            injections.append(
                f"注意避免: {'; '.join(avoid_patterns)}"
            )

        # 基于建议添加指导
        high_priority = [r for r in context.recommendations if r.get('priority') == 'high']
        if high_priority:
            tips = [r.get('title', '') for r in high_priority[:2]]
            injections.append(f"建议: {'; '.join(tips)}")

        return '\n'.join(injections)

    def _generate_actionable_prompt(self, context: ReflectionContext) -> str:
        """生成可执行的提示词"""
        lines = [
            "【决策指导】",
            "在做决策时，请考虑以下因素:"
        ]

        # 添加建议
        for i, rec in enumerate(context.recommendations[:3], 1):
            lines.append(f"  {i}. {rec.get('title', '')}: {rec.get('action', '')}")

        # 添加警告
        if context.bad_cases:
            lines.append("\n【警告】")
            for bc in context.bad_cases[:2]:
                lines.append(f"  - 避免: {bc.get('description', '')}")

        return '\n'.join(lines)

    def _calculate_confidence(self, context: ReflectionContext) -> float:
        """计算上下文置信度"""
        confidence = 0.0

        # 数据源数量
        if 'reflection_engine' in context.data_sources:
            confidence += 0.3

        if 'task_specific' in context.data_sources:
            confidence += 0.2

        if 'strategy_updater' in context.data_sources:
            confidence += 0.2

        # 数据完整性
        if context.recent_insights:
            confidence += 0.1

        if context.recommendations:
            confidence += 0.1

        if context.bad_cases:
            confidence += 0.1

        return min(1.0, confidence)

    def inject_context_into_prompt(
        self,
        base_prompt: str,
        session_id: str,
        task_type: str,
        format: ContextFormat = ContextFormat.COMPACT
    ) -> str:
        """
        将反思上下文注入到提示词中

        Args:
            base_prompt: 基础提示词
            session_id: 会话 ID
            task_type: 任务类型
            format: 上下文格式

        Returns:
            注入后的提示词
        """
        context = self.get_context_for_agent(session_id, task_type, format)

        if not context.prompt_injection:
            return base_prompt

        # 构建注入文本
        injection = f"\n\n## 历史反思经验\n{context.prompt_injection}\n"

        # 在适当位置注入（通常在角色定义之后，任务描述之前）
        if "## 任务" in base_prompt:
            return base_prompt.replace("## 任务", f"{injection}\n## 任务")
        elif "# 任务" in base_prompt:
            return base_prompt.replace("# 任务", f"{injection}\n# 任务")
        else:
            return base_prompt + injection

    def get_strategy_for_context(
        self,
        task_type: str,
        additional_context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        获取适用于当前上下文的策略

        Args:
            task_type: 任务类型
            additional_context: 额外上下文

        Returns:
            策略配置
        """
        if not self.strategy_updater:
            return {}

        from .strategy_updater import StrategyType

        if task_type == 'appointment':
            strategy_type = StrategyType.MATCHING
        elif task_type == 'consultation':
            strategy_type = StrategyType.PROMPT
        else:
            strategy_type = StrategyType.ROUTING

        strategy = self.strategy_updater.get_active_strategy(strategy_type)

        if not strategy:
            return {}

        config = strategy.get('config', {}).copy()

        # 根据额外上下文调整配置
        if additional_context:
            # 可以根据上下文动态调整参数
            pass

        return {
            'version_id': strategy.get('version_id'),
            'config': config,
            'adjustments': self._generate_strategy_adjustments(task_type, config)
        }

    def _generate_strategy_adjustments(
        self,
        task_type: str,
        config: Dict[str, Any]
    ) -> List[str]:
        """生成策略调整说明"""
        adjustments = []

        for key, value in config.items():
            if isinstance(value, (int, float)):
                adjustments.append(f"{key}={value}")

        return adjustments

    def clear_cache(self) -> None:
        """清除缓存"""
        self._context_cache.clear()
        self.logger.info("反思上下文缓存已清除")
