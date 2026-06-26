"""
闭环效果验证器 - 验证反思改进效果

核心功能：
1. 对比策略调整前后的效果
2. 计算改进指标
3. 自动触发策略回滚或确认
4. 生成效果报告
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import logging


class EvaluationResult(Enum):
    """评估结果"""
    IMPROVED = "improved"           # 有改进
    DEGRADED = "degraded"           # 效果下降
    NO_CHANGE = "no_change"         # 无变化
    INSUFFICIENT_DATA = "insufficient_data"  # 数据不足


@dataclass
class ComparisonMetrics:
    """对比指标"""
    before_success_rate: float
    after_success_rate: float
    before_avg_turns: float
    after_avg_turns: float
    before_avg_time: float
    after_avg_time: float
    before_failure_count: int
    after_failure_count: int
    sample_size_before: int
    sample_size_after: int


@dataclass
class ImprovementResult:
    """改进结果"""
    evaluation: EvaluationResult
    metrics: ComparisonMetrics
    improvement_rate: float          # 改进率
    confidence: float               # 置信度
    statistical_significance: float # 统计显著性
    recommendation: str             # 建议
    details: Dict[str, Any] = field(default_factory=dict)


class ClosedLoopEvaluator:
    """
    闭环效果验证器

    通过 A/B 测试风格的方式验证策略改进效果
    """

    def __init__(
        self,
        evaluation_repo=None,
        reflection_repo=None,
        strategy_updater=None
    ):
        self.evaluation_repo = evaluation_repo
        self.reflection_repo = reflection_repo
        self.strategy_updater = strategy_updater
        self.logger = logging.getLogger(__name__)

        # 验证配置
        self.config = {
            'min_sample_size': 10,           # 最小样本量
            'improvement_threshold': 0.05,   # 改进阈值 (5%)
            'degradation_threshold': 0.10,   # 下降阈值 (10%)
            'confidence_level': 0.95,        # 置信度
            'evaluation_window_days': 7       # 评估窗口天数
        }

        # 策略版本映射 (strategy_version_id -> (start_time, end_time))
        self._strategy_versions: Dict[str, Tuple[datetime, datetime]] = {}

    def evaluate_strategy_improvement(
        self,
        strategy_version_id: str,
        task_type: str,
        lookback_days: int = 7
    ) -> ImprovementResult:
        """
        评估策略改进效果

        Args:
            strategy_version_id: 策略版本 ID
            task_type: 任务类型
            lookback_days: 回溯天数

        Returns:
            改进结果
        """
        if not self.evaluation_repo:
            return self._insufficient_data_result("评估仓库不可用")

        # 1. 确定策略时间窗口
        if strategy_version_id in self._strategy_versions:
            start_time, end_time = self._strategy_versions[strategy_version_id]
        else:
            # 使用当前时间作为策略启用时间
            end_time = datetime.now()
            start_time = end_time - timedelta(days=lookback_days)

        # 2. 获取前后数据
        before_data = self._get_evaluation_data(
            task_type=task_type,
            end_time=start_time,
            lookback_days=lookback_days
        )

        after_data = self._get_evaluation_data(
            task_type=task_type,
            start_time=start_time,
            end_time=end_time
        )

        # 3. 检查数据量
        if before_data['count'] < self.config['min_sample_size']:
            return self._insufficient_data_result(
                f"策略启用前数据不足: {before_data['count']} < {self.config['min_sample_size']}"
            )

        if after_data['count'] < self.config['min_sample_size']:
            return self._insufficient_data_result(
                f"策略启用后数据不足: {after_data['count']} < {self.config['min_sample_size']}"
            )

        # 4. 计算对比指标
        metrics = ComparisonMetrics(
            before_success_rate=before_data['success_rate'],
            after_success_rate=after_data['success_rate'],
            before_avg_turns=before_data['avg_turns'],
            after_avg_turns=after_data['avg_turns'],
            before_avg_time=before_data['avg_time'],
            after_avg_time=after_data['avg_time'],
            before_failure_count=before_data['failure_count'],
            after_failure_count=after_data['failure_count'],
            sample_size_before=before_data['count'],
            sample_size_after=after_data['count']
        )

        # 5. 计算改进率
        improvement_rate = self._calculate_improvement_rate(
            metrics.before_success_rate,
            metrics.after_success_rate
        )

        # 6. 计算置信度
        confidence = self._calculate_confidence(
            metrics.sample_size_before,
            metrics.sample_size_after,
            abs(improvement_rate)
        )

        # 7. 计算统计显著性
        significance = self._calculate_statistical_significance(
            metrics,
            before_data['success_details'],
            after_data['success_details']
        )

        # 8. 确定评估结果
        evaluation = self._determine_evaluation(
            improvement_rate,
            confidence,
            significance
        )

        # 9. 生成建议
        recommendation = self._generate_recommendation(
            evaluation,
            improvement_rate,
            confidence
        )

        return ImprovementResult(
            evaluation=evaluation,
            metrics=metrics,
            improvement_rate=improvement_rate,
            confidence=confidence,
            statistical_significance=significance,
            recommendation=recommendation,
            details={
                'strategy_version_id': strategy_version_id,
                'task_type': task_type,
                'evaluation_window': {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat()
                },
                'thresholds': self.config
            }
        )

    def _get_evaluation_data(
        self,
        task_type: str,
        start_time: datetime = None,
        end_time: datetime = None,
        lookback_days: int = 7
    ) -> Dict[str, Any]:
        """获取评估数据"""
        if end_time is None:
            end_time = datetime.now()

        if start_time is None:
            start_time = end_time - timedelta(days=lookback_days)

        # 从仓库获取数据
        evaluations = self.evaluation_repo.get_evaluations_by_task_type(
            task_type=task_type,
            start_time=start_time,
            end_time=end_time
        )

        if not evaluations:
            return {
                'count': 0,
                'success_rate': 0.0,
                'avg_turns': 0.0,
                'avg_time': 0.0,
                'failure_count': 0,
                'success_details': []
            }

        # 计算统计数据
        total = len(evaluations)
        successes = sum(1 for e in evaluations if e.get('success', 0) == 1)
        total_turns = sum(e.get('turns_count', 0) for e in evaluations)
        total_time = sum(e.get('completion_time', 0) for e in evaluations if e.get('completion_time'))
        failures = sum(1 for e in evaluations if e.get('success', 0) == 0)

        return {
            'count': total,
            'success_rate': successes / total if total > 0 else 0.0,
            'avg_turns': total_turns / total if total > 0 else 0.0,
            'avg_time': total_time / successes if successes > 0 else 0.0,
            'failure_count': failures,
            'success_details': [e for e in evaluations if e.get('success', 0) == 1]
        }

    def _calculate_improvement_rate(
        self,
        before_rate: float,
        after_rate: float
    ) -> float:
        """计算改进率"""
        if before_rate == 0:
            return 1.0 if after_rate > 0 else 0.0

        return (after_rate - before_rate) / before_rate

    def _calculate_confidence(
        self,
        n1: int,
        n2: int,
        effect_size: float
    ) -> float:
        """
        简化置信度计算

        考虑样本量和效果大小的置信度估算
        """
        # 样本量因子 (最大1.0)
        sample_factor = min(1.0, (n1 + n2) / 100)

        # 效果大小因子
        effect_factor = min(1.0, abs(effect_size) * 2)

        # 综合置信度
        confidence = sample_factor * effect_factor

        return min(0.99, max(0.0, confidence))

    def _calculate_statistical_significance(
        self,
        metrics: ComparisonMetrics,
        before_successes: List[Dict],
        after_successes: List[Dict]
    ) -> float:
        """
        简化的统计显著性计算

        使用比例差异的标准化
        """
        # 简化版: 使用改进率和样本量的组合
        if metrics.sample_size_before + metrics.sample_size_after < 20:
            return 0.5

        # Z-score 近似
        p1 = metrics.before_success_rate
        p2 = metrics.after_success_rate
        n1 = metrics.sample_size_before
        n2 = metrics.sample_size_after

        # 合并比例
        p_pooled = (
            (metrics.before_success_rate * n1 + metrics.after_success_rate * n2)
            / (n1 + n2)
        )

        # 标准误差
        se = (p_pooled * (1 - p_pooled) * (1/n1 + 1/n2)) ** 0.5

        if se == 0:
            return 0.5

        z = abs(p2 - p1) / se

        # 转换为伪显著性值 (简化处理)
        significance = min(0.99, z / 4)

        return significance

    def _determine_evaluation(
        self,
        improvement_rate: float,
        confidence: float,
        significance: float
    ) -> EvaluationResult:
        """确定评估结果"""
        # 需要足够的置信度和统计显著性
        if confidence < 0.7 or significance < 0.6:
            return EvaluationResult.NO_CHANGE

        if improvement_rate >= self.config['improvement_threshold']:
            return EvaluationResult.IMPROVED
        elif improvement_rate <= -self.config['degradation_threshold']:
            return EvaluationResult.DEGRADED
        else:
            return EvaluationResult.NO_CHANGE

    def _generate_recommendation(
        self,
        evaluation: EvaluationResult,
        improvement_rate: float,
        confidence: float
    ) -> str:
        """生成建议"""
        if evaluation == EvaluationResult.IMPROVED:
            return (
                f"策略改进有效！成功率提升 {improvement_rate:.1%}，"
                f"置信度 {confidence:.1%}。建议保持当前策略。"
            )
        elif evaluation == EvaluationResult.DEGRADED:
            return (
                f"策略效果下降 {improvement_rate:.1%}，"
                f"建议回滚到之前的策略版本。"
            )
        elif evaluation == EvaluationResult.NO_CHANGE:
            return (
                f"策略调整效果不明显 ({improvement_rate:+.1%})，"
                f"建议收集更多数据后再评估。"
            )
        else:
            return "数据不足，无法评估策略效果。"

    def _insufficient_data_result(self, reason: str) -> ImprovementResult:
        """数据不足时的结果"""
        return ImprovementResult(
            evaluation=EvaluationResult.INSUFFICIENT_DATA,
            metrics=None,
            improvement_rate=0.0,
            confidence=0.0,
            statistical_significance=0.0,
            recommendation=f"无法评估: {reason}"
        )

    def auto_evaluate_and_adjust(
        self,
        strategy_version_id: str,
        task_type: str
    ) -> Dict[str, Any]:
        """
        自动评估并调整策略

        根据评估结果自动激活或回滚策略

        Args:
            strategy_version_id: 策略版本 ID
            task_type: 任务类型

        Returns:
            调整结果
        """
        result = self.evaluate_strategy_improvement(strategy_version_id, task_type)

        if result.evaluation == EvaluationResult.IMPROVED:
            # 策略有效，保持激活
            action = "保持"
            self.logger.info(f"策略 {strategy_version_id} 改进有效，保持激活")

        elif result.evaluation == EvaluationResult.DEGRADED:
            # 策略效果下降，回滚
            if self.strategy_updater:
                from .strategy_updater import StrategyType
                # 根据 task_type 确定策略类型
                if task_type == 'appointment':
                    st = StrategyType.MATCHING
                elif task_type == 'consultation':
                    st = StrategyType.PROMPT
                else:
                    st = StrategyType.ROUTING

                self.strategy_updater.rollback_strategy(st)
                action = "已回滚"
                self.logger.warning(f"策略 {strategy_version_id} 效果下降，已回滚")
            else:
                action = "建议回滚（未配置策略更新器）"

        else:
            action = "继续观察"

        return {
            'evaluation': result.evaluation.value,
            'improvement_rate': result.improvement_rate,
            'confidence': result.confidence,
            'recommendation': result.recommendation,
            'action_taken': action
        }

    def generate_comparison_report(
        self,
        task_type: str,
        days: int = 14
    ) -> Dict[str, Any]:
        """
        生成对比报告

        Args:
            task_type: 任务类型
            days: 报告周期

        Returns:
            对比报告
        """
        # 获取不同时间段的对比数据
        recent_data = self._get_evaluation_data(
            task_type=task_type,
            lookback_days=days // 2
        )

        previous_data = self._get_evaluation_data(
            task_type=task_type,
            start_time=datetime.now() - timedelta(days=days),
            end_time=datetime.now() - timedelta(days=days // 2)
        )

        return {
            'report_type': 'comparison',
            'task_type': task_type,
            'period_days': days,
            'recent_period': {
                'days': days // 2,
                'data': recent_data
            },
            'previous_period': {
                'days': days // 2,
                'data': previous_data
            },
            'comparison': {
                'success_rate_change': (
                    recent_data['success_rate'] - previous_data['success_rate']
                ),
                'avg_turns_change': (
                    recent_data['avg_turns'] - previous_data['avg_turns']
                ),
                'failure_count_change': (
                    recent_data['failure_count'] - previous_data['failure_count']
                )
            },
            'generated_at': datetime.now().isoformat()
        }

    def record_strategy_version(
        self,
        strategy_version_id: str,
        start_time: datetime = None,
        end_time: datetime = None
    ) -> None:
        """记录策略版本时间窗口"""
        if start_time is None:
            start_time = datetime.now()

        if end_time is None:
            end_time = datetime.max

        self._strategy_versions[strategy_version_id] = (start_time, end_time)
