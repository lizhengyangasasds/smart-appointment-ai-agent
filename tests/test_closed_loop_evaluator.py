"""
ClosedLoopEvaluator 单元测试

验证闭环效果验证器的对比计算、统计显著性等逻辑
"""

import pytest
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reflection.closed_loop_evaluator import (
    ClosedLoopEvaluator,
    EvaluationResult,
    ComparisonMetrics,
    ImprovementResult
)


class TestClosedLoopEvaluator:
    """ClosedLoopEvaluator 单元测试"""

    @pytest.fixture
    def evaluator(self):
        """创建闭环评估器实例"""
        return ClosedLoopEvaluator()

    @pytest.fixture
    def mock_evaluation_repo(self):
        """创建模拟的评估仓库"""
        repo = MagicMock()
        repo.get_evaluations_by_task_type = MagicMock(return_value=[])
        return repo

    # ===== 改进率计算测试 =====

    def test_improvement_calculation_positive(self, evaluator):
        """测试：改进率计算 - 正向改进"""
        rate = evaluator._calculate_improvement_rate(0.5, 0.7)
        assert rate == pytest.approx(0.4)  # 40% 改进

    def test_improvement_calculation_significant(self, evaluator):
        """测试：改进率计算 - 显著改进"""
        rate = evaluator._calculate_improvement_rate(0.6, 0.9)
        assert rate == pytest.approx(0.5)  # 50% 改进

    def test_improvement_calculation_negative(self, evaluator):
        """测试：改进率计算 - 效果下降"""
        rate = evaluator._calculate_improvement_rate(0.8, 0.6)
        assert rate == pytest.approx(-0.25)  # 25% 下降

    def test_improvement_calculation_no_change(self, evaluator):
        """测试：改进率计算 - 无变化"""
        rate = evaluator._calculate_improvement_rate(0.5, 0.5)
        assert rate == pytest.approx(0.0)

    def test_improvement_calculation_from_zero(self, evaluator):
        """测试：改进率计算 - 从零开始改进"""
        rate = evaluator._calculate_improvement_rate(0.0, 0.5)
        assert rate == pytest.approx(1.0)  # 从 0 到非 0 算 100% 改进

    def test_improvement_calculation_stay_zero(self, evaluator):
        """测试：改进率计算 - 始终为零"""
        rate = evaluator._calculate_improvement_rate(0.0, 0.0)
        assert rate == pytest.approx(0.0)

    # ===== 置信度计算测试 =====

    def test_confidence_high_sample_large_effect(self, evaluator):
        """测试：置信度计算 - 大样本大效果"""
        confidence = evaluator._calculate_confidence(100, 100, 0.2)
        assert confidence >= 0.8

    def test_confidence_low_sample_small_effect(self, evaluator):
        """测试：置信度计算 - 小样本小效果"""
        confidence = evaluator._calculate_confidence(5, 5, 0.05)
        assert confidence < 0.5

    def test_confidence_medium(self, evaluator):
        """测试：置信度计算 - 中等样本中等效果"""
        confidence = evaluator._calculate_confidence(30, 30, 0.1)
        assert 0.3 <= confidence <= 0.8

    def test_confidence_at_boundary(self, evaluator):
        """测试：置信度边界值"""
        # 最大置信度
        conf_max = evaluator._calculate_confidence(1000, 1000, 1.0)
        assert conf_max <= 0.99

        # 最小置信度
        conf_min = evaluator._calculate_confidence(1, 1, 0.0)
        assert conf_min >= 0.0

    # ===== 统计显著性计算测试 =====

    def test_statistical_significance_clear_improvement(self, evaluator):
        """测试：统计显著性 - 明显改进"""
        metrics = ComparisonMetrics(
            before_success_rate=0.5,
            after_success_rate=0.7,
            before_avg_turns=8.0,
            after_avg_turns=6.0,
            before_avg_time=120.0,
            after_avg_time=100.0,
            before_failure_count=50,
            after_failure_count=30,
            sample_size_before=100,
            sample_size_after=100
        )

        sig = evaluator._calculate_statistical_significance(
            metrics, [], []
        )

        assert 0 <= sig <= 1
        assert sig >= 0.3  # 明显改进应该有较高显著性

    def test_statistical_significance_insufficient_data(self, evaluator):
        """测试：统计显著性 - 数据不足"""
        metrics = ComparisonMetrics(
            before_success_rate=0.5,
            after_success_rate=0.6,
            before_avg_turns=8.0,
            after_avg_turns=7.0,
            before_avg_time=120.0,
            after_avg_time=110.0,
            before_failure_count=5,
            after_failure_count=4,
            sample_size_before=10,  # 样本太小
            sample_size_after=10
        )

        sig = evaluator._calculate_statistical_significance(
            metrics, [], []
        )

        assert sig == 0.5  # 数据不足返回 0.5

    def test_statistical_significance_no_change(self, evaluator):
        """测试：统计显著性 - 无变化"""
        metrics = ComparisonMetrics(
            before_success_rate=0.6,
            after_success_rate=0.6,
            before_avg_turns=7.0,
            after_avg_turns=7.0,
            before_avg_time=100.0,
            after_avg_time=100.0,
            before_failure_count=40,
            after_failure_count=40,
            sample_size_before=100,
            sample_size_after=100
        )

        sig = evaluator._calculate_statistical_significance(
            metrics, [], []
        )

        assert sig >= 0  # 应该有较低的显著性

    # ===== 评估结果判定测试 =====

    def test_determine_evaluation_improved(self, evaluator):
        """测试：评估结果判定 - 改进"""
        result = evaluator._determine_evaluation(
            improvement_rate=0.1,  # >= 5%
            confidence=0.8,
            significance=0.7
        )

        assert result == EvaluationResult.IMPROVED

    def test_determine_evaluation_degraded(self, evaluator):
        """测试：评估结果判定 - 下降"""
        result = evaluator._determine_evaluation(
            improvement_rate=-0.15,  # <= -10%
            confidence=0.8,
            significance=0.7
        )

        assert result == EvaluationResult.DEGRADED

    def test_determine_evaluation_no_change_low_confidence(self, evaluator):
        """测试：评估结果判定 - 置信度低时判定为无变化"""
        result = evaluator._determine_evaluation(
            improvement_rate=0.1,
            confidence=0.5,  # < 0.7
            significance=0.7
        )

        assert result == EvaluationResult.NO_CHANGE

    def test_determine_evaluation_no_change_low_significance(self, evaluator):
        """测试：评估结果判定 - 统计显著性低时判定为无变化"""
        result = evaluator._determine_evaluation(
            improvement_rate=0.1,
            confidence=0.8,
            significance=0.4  # < 0.6
        )

        assert result == EvaluationResult.NO_CHANGE

    def test_determine_evaluation_no_change_small_improvement(self, evaluator):
        """测试：评估结果判定 - 改进幅度小"""
        result = evaluator._determine_evaluation(
            improvement_rate=0.03,  # < 5%
            confidence=0.9,
            significance=0.8
        )

        assert result == EvaluationResult.NO_CHANGE

    # ===== 建议生成测试 =====

    def test_generate_recommendation_improved(self, evaluator):
        """测试：生成建议 - 改进"""
        recommendation = evaluator._generate_recommendation(
            evaluation=EvaluationResult.IMPROVED,
            improvement_rate=0.15,
            confidence=0.85
        )

        assert "改进有效" in recommendation or "improved" in recommendation.lower()
        assert "15%" in recommendation or "15.0%" in recommendation

    def test_generate_recommendation_degraded(self, evaluator):
        """测试：生成建议 - 下降"""
        recommendation = evaluator._generate_recommendation(
            evaluation=EvaluationResult.DEGRADED,
            improvement_rate=-0.2,
            confidence=0.8
        )

        assert "下降" in recommendation or "degraded" in recommendation.lower()
        assert "回滚" in recommendation or "rollback" in recommendation.lower()

    def test_generate_recommendation_no_change(self, evaluator):
        """测试：生成建议 - 无变化"""
        recommendation = evaluator._generate_recommendation(
            evaluation=EvaluationResult.NO_CHANGE,
            improvement_rate=0.02,
            confidence=0.5
        )

        assert "不明显" in recommendation or "no change" in recommendation.lower()
        assert "更多数据" in recommendation or "more data" in recommendation.lower()

    # ===== 数据获取测试 =====

    def test_get_evaluation_data_empty(self, evaluator, mock_evaluation_repo):
        """测试：获取评估数据 - 空数据"""
        evaluator.evaluation_repo = mock_evaluation_repo
        mock_evaluation_repo.get_evaluations_by_task_type.return_value = []

        data = evaluator._get_evaluation_data(
            task_type='appointment',
            lookback_days=7
        )

        assert data['count'] == 0
        assert data['success_rate'] == 0.0
        assert data['avg_turns'] == 0.0

    def test_get_evaluation_data_with_records(self, evaluator, mock_evaluation_repo):
        """测试：获取评估数据 - 有记录"""
        evaluator.evaluation_repo = mock_evaluation_repo
        mock_evaluation_repo.get_evaluations_by_task_type.return_value = [
            {'success': 1, 'turns_count': 5, 'completion_time': 60.0},
            {'success': 1, 'turns_count': 7, 'completion_time': 80.0},
            {'success': 0, 'turns_count': 3, 'completion_time': 40.0},
        ]

        data = evaluator._get_evaluation_data(
            task_type='appointment',
            lookback_days=7
        )

        assert data['count'] == 3
        assert data['success_rate'] == pytest.approx(2/3)
        assert data['avg_turns'] == pytest.approx(5.0)
        assert data['failure_count'] == 1

    # ===== 策略评估完整流程测试 =====

    def test_evaluate_strategy_improvement_insufficient_data(self, evaluator, mock_evaluation_repo):
        """测试：策略评估 - 数据不足"""
        evaluator.evaluation_repo = mock_evaluation_repo
        mock_evaluation_repo.get_evaluations_by_task_type.return_value = [
            {'success': 1, 'turns_count': 5}  # 只有 1 条记录
        ]

        result = evaluator.evaluate_strategy_improvement(
            strategy_version_id="test_v1",
            task_type="appointment"
        )

        assert result.evaluation == EvaluationResult.INSUFFICIENT_DATA
        assert result.confidence == 0.0

    def test_evaluate_strategy_improvement_success(self, evaluator, mock_evaluation_repo):
        """测试：策略评估 - 成功评估"""
        evaluator.evaluation_repo = mock_evaluation_repo

        # 模拟足够的数据
        def mock_get_data(task_type, start_time=None, end_time=None):
            # 根据时间段返回不同的数据
            if start_time and datetime.now() - start_time < timedelta(days=3):
                # 策略启用后 - 更好的数据
                return [
                    {'success': 1, 'turns_count': 5, 'completion_time': 60.0}
                    for _ in range(12)
                ]
            else:
                # 策略启用前 - 较差的数据
                return [
                    {'success': 1, 'turns_count': 8, 'completion_time': 100.0}
                    for _ in range(12)
                ]

        mock_evaluation_repo.get_evaluations_by_task_type.side_effect = mock_get_data

        result = evaluator.evaluate_strategy_improvement(
            strategy_version_id="test_v2",
            task_type="appointment",
            lookback_days=7
        )

        assert result.evaluation in [
            EvaluationResult.IMPROVED,
            EvaluationResult.DEGRADED,
            EvaluationResult.NO_CHANGE
        ]
        assert 'improvement_rate' in str(result.improvement_rate)

    # ===== 自动评估和调整测试 =====

    def test_auto_evaluate_and_adjust_improved(self, evaluator, mock_evaluation_repo):
        """测试：自动评估调整 - 改进时保持策略"""
        evaluator.evaluation_repo = mock_evaluation_repo

        # 模拟改进的结果
        def mock_evaluate(strategy_version_id, task_type):
            return ImprovementResult(
                evaluation=EvaluationResult.IMPROVED,
                metrics=None,
                improvement_rate=0.15,
                confidence=0.85,
                statistical_significance=0.7,
                recommendation="策略有效，保持激活"
            )

        evaluator.evaluate_strategy_improvement = mock_evaluate

        result = evaluator.auto_evaluate_and_adjust(
            strategy_version_id="test_v3",
            task_type="appointment"
        )

        assert result['action_taken'] == "保持"
        assert result['evaluation'] == "improved"

    def test_auto_evaluate_and_adjust_degraded(self, evaluator, mock_evaluation_repo):
        """测试：自动评估调整 - 下降时回滚策略"""
        evaluator.evaluation_repo = mock_evaluation_repo
        evaluator.strategy_updater = MagicMock()

        # 模拟下降的结果
        def mock_evaluate(strategy_version_id, task_type):
            return ImprovementResult(
                evaluation=EvaluationResult.DEGRADED,
                metrics=None,
                improvement_rate=-0.2,
                confidence=0.8,
                statistical_significance=0.7,
                recommendation="策略效果下降，建议回滚"
            )

        evaluator.evaluate_strategy_improvement = mock_evaluate

        result = evaluator.auto_evaluate_and_adjust(
            strategy_version_id="test_v4",
            task_type="appointment"
        )

        assert "回滚" in result['action_taken']
        assert result['evaluation'] == "degraded"

    # ===== 对比报告生成测试 =====

    def test_generate_comparison_report(self, evaluator, mock_evaluation_repo):
        """测试：生成对比报告"""
        evaluator.evaluation_repo = mock_evaluation_repo
        mock_evaluation_repo.get_evaluations_by_task_type.return_value = [
            {'success': 1, 'turns_count': 6}
            for _ in range(10)
        ]

        report = evaluator.generate_comparison_report(
            task_type='appointment',
            days=14
        )

        assert report['report_type'] == 'comparison'
        assert report['task_type'] == 'appointment'
        assert 'recent_period' in report
        assert 'previous_period' in report
        assert 'comparison' in report

    # ===== 策略版本记录测试 =====

    def test_record_strategy_version(self, evaluator):
        """测试：记录策略版本时间窗口"""
        version_id = "test_version_001"
        start = datetime.now()
        end = start + timedelta(days=7)

        evaluator.record_strategy_version(version_id, start, end)

        assert version_id in evaluator._strategy_versions
        assert evaluator._strategy_versions[version_id] == (start, end)

    def test_record_strategy_version_default_times(self, evaluator):
        """测试：记录策略版本 - 默认时间"""
        version_id = "test_version_002"

        evaluator.record_strategy_version(version_id)

        assert version_id in evaluator._strategy_versions
        start, end = evaluator._strategy_versions[version_id]
        assert start is not None
        assert end == datetime.max


class TestComparisonMetrics:
    """ComparisonMetrics 数据类测试"""

    def test_metrics_creation(self):
        """测试：创建对比指标"""
        metrics = ComparisonMetrics(
            before_success_rate=0.5,
            after_success_rate=0.7,
            before_avg_turns=8.0,
            after_avg_turns=6.0,
            before_avg_time=120.0,
            after_avg_time=100.0,
            before_failure_count=50,
            after_failure_count=30,
            sample_size_before=100,
            sample_size_after=100
        )

        assert metrics.before_success_rate == 0.5
        assert metrics.after_success_rate == 0.7
        assert metrics.sample_size_before == 100


class TestImprovementResult:
    """ImprovementResult 数据类测试"""

    def test_result_creation(self):
        """测试：创建改进结果"""
        result = ImprovementResult(
            evaluation=EvaluationResult.IMPROVED,
            metrics=None,
            improvement_rate=0.15,
            confidence=0.85,
            statistical_significance=0.7,
            recommendation="策略有效"
        )

        assert result.evaluation == EvaluationResult.IMPROVED
        assert result.improvement_rate == 0.15
        assert result.confidence == 0.85


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
