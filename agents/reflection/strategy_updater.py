"""
策略更新器 - 基于反思结果动态调整 Agent 策略

核心功能：
1. 从反思洞察中提取可执行的策略调整
2. 维护策略版本历史
3. 提供策略查询接口
4. 支持策略回滚
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
import json
import logging


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
    策略更新器

    根据反思结果动态生成和更新 Agent 策略
    """

    def __init__(self, reflection_repo=None, llm=None):
        self.reflection_repo = reflection_repo
        self.llm = llm
        self.logger = logging.getLogger(__name__)

        # 内存中的策略存储（生产环境应该用数据库）
        self._strategies: Dict[str, List[StrategyVersion]] = {
            st.value: [] for st in StrategyType
        }

        # 活跃策略映射
        self._active_strategies: Dict[str, StrategyVersion] = {}

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
        """从坏 case 生成避免策略"""
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
            status=StrategyStatus.PENDING
        )

    def _generate_optimization_strategy(self, recommendation: Dict[str, Any]) -> Optional[StrategyVersion]:
        """从推荐生成优化策略"""
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
            status=StrategyStatus.PENDING
        )

    def _generate_adaptation_strategy(
        self,
        pattern_type: str,
        pattern_data: Dict[str, Any]
    ) -> Optional[StrategyVersion]:
        """从模式分析生成适应策略"""
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
            status=StrategyStatus.PENDING
        )

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
        return {
            st.value: self.get_active_strategy(st)
            for st in StrategyType
        }

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
