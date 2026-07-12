"""
反思系统端到端集成测试

验证反思系统各组件的完整协作流程
"""

import pytest
import sys
import os
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reflection.evaluator import TaskEvaluator, SuccessLevel
from agents.reflection.analyzer import ReflectionAnalyzer
from agents.reflection.reporter import ReflectionReporter
from agents.reflection.strategy_updater import StrategyUpdater, StrategyType
from agents.reflection.closed_loop_evaluator import ClosedLoopEvaluator, EvaluationResult
from db.base.exceptions import SlotTakenException


class TestReflectionIntegration:
    """反思系统端到端集成测试"""

    @pytest.fixture
    def reflection_engine(self):
        """创建完整的反思引擎（简化版，依赖实际组件）"""
        from agents.reflection.engine import ReflectionEngine

        evaluator = TaskEvaluator()
        analyzer = ReflectionAnalyzer()
        reporter = ReflectionReporter()
        strategy_updater = StrategyUpdater()

        return ReflectionEngine(
            evaluator=evaluator,
            analyzer=analyzer,
            reporter=reporter,
            strategy_updater=strategy_updater
        )

    # ===== 评估器与策略生成集成测试 =====

    def test_failed_appointment_triggers_strategy_generation(self):
        """测试：失败的预约任务触发策略生成"""
        evaluator = TaskEvaluator()
        strategy_updater = StrategyUpdater()

        # 模拟失败的预约
        error = SlotTakenException(1, "15:00", "16:00")
        result = evaluator.evaluate_appointment_task(
            session_id="test_int_001",
            appointment_history={'gender': '女', 'start_time': '15:00'},
            turns_count=5,
            error=error
        )

        assert result['should_reflect'] == True
        assert result['error_type'] == 'slot_unavailable'

        # 模拟基于评估结果生成策略
        insights = {
            'actionable_recommendations': [{
                'id': 'rec_slot_001',
                'title': '优化时间段推荐',
                'priority': 'high',
                'action': {
                    'type': 'matching',
                    'parameters': {
                        'check_availability_first': True,
                        'conflict_detection': 'strict'
                    }
                }
            }]
        }

        strategies = strategy_updater.generate_strategies_from_insights(insights)
        assert len(strategies) >= 1
        assert strategies[0].strategy_type == StrategyType.MATCHING

    def test_partial_success_triggers_reflection(self):
        """测试：部分成功触发反思和策略更新"""
        evaluator = TaskEvaluator()
        strategy_updater = StrategyUpdater()

        # 模拟部分成功（轮数过多）
        result = evaluator.evaluate_appointment_task(
            session_id="test_int_002",
            appointment_history={
                'gender': '女',
                'start_time': '15:00',
                'duration': '60',
                'project': '全身按摩'
            },
            turns_count=15,  # 超过 10 轮阈值
            completion_time=180.0
        )

        assert result['should_reflect'] == True

        # 生成减少轮数的策略
        insights = {
            'actionable_recommendations': [{
                'id': 'rec_turns_001',
                'title': '减少对话轮数',
                'priority': 'high',
                'action': {
                    'type': 'prompt',
                    'parameters': {
                        'ask_briefly': True,
                        'max_questions_per_turn': 2
                    }
                }
            }]
        }

        strategies = strategy_updater.generate_strategies_from_insights(insights)
        prompt_strategies = [s for s in strategies
                           if s.strategy_type == StrategyType.PROMPT]
        assert len(prompt_strategies) >= 1

    # ===== 策略生成→激活→应用完整链路测试 =====

    def test_strategy_lifecycle_full_path(self):
        """测试：策略生命周期完整路径"""
        strategy_updater = StrategyUpdater()

        # 1. 从洞察生成策略
        insights = {
            'recent_bad_cases': [{
                'case_id': 'bc_full_001',
                'description': '技师匹配不准确',
                'task_type': 'appointment',
                'severity': 8,
                'trigger': {},
                'suggested_fix': {
                    'similarity_weight': 0.7,
                    'availability_weight': 0.3
                }
            }]
        }

        strategies = strategy_updater.generate_strategies_from_insights(insights)
        assert len(strategies) >= 1

        # 2. 激活策略
        new_strategy = strategies[0]
        activation_result = strategy_updater.activate_strategy(
            new_strategy.version_id,
            new_strategy.strategy_type
        )
        assert activation_result == True

        # 3. 获取活跃策略
        active = strategy_updater.get_active_strategy(new_strategy.strategy_type)
        assert active['version_id'] == new_strategy.version_id

        # 4. 应用策略到上下文
        context = {'user_id': 'test_user'}
        updated_context = strategy_updater.apply_strategy_to_context(
            context,
            task_type='appointment'
        )

        assert 'matching_config' in updated_context
        assert updated_context['matching_config']['similarity_weight'] == 0.7

    # ===== 闭环效果验证集成测试 =====

    def test_closed_loop_feedback_integration(self):
        """测试：闭环反馈集成"""
        strategy_updater = StrategyUpdater()
        evaluator = ClosedLoopEvaluator()

        # 模拟策略效果下降的检测
        improvement_rate = evaluator._calculate_improvement_rate(0.8, 0.6)
        assert improvement_rate < 0  # 下降

        # 验证应该回滚
        confidence = evaluator._calculate_confidence(100, 100, abs(improvement_rate))
        significance = 0.7

        result = evaluator._determine_evaluation(
            improvement_rate,
            confidence,
            significance
        )

        assert result == EvaluationResult.DEGRADED

        # 模拟回滚
        rollback_result = strategy_updater.rollback_strategy(StrategyType.MATCHING)
        assert rollback_result == True

        # 验证回到了默认策略
        active = strategy_updater.get_active_strategy(StrategyType.MATCHING)
        assert 'default' in active['version_id'].lower()

    def test_improvement_detection_and_strategy_generation(self):
        """测试：改进检测与策略生成联动"""
        strategy_updater = StrategyUpdater()
        evaluator = ClosedLoopEvaluator()

        # 计算改进率
        improvement_rate = evaluator._calculate_improvement_rate(0.5, 0.75)

        # 验证检测到改进
        assert improvement_rate > 0

        # 高置信度时，生成强化策略
        confidence = evaluator._calculate_confidence(100, 100, improvement_rate)

        if improvement_rate >= evaluator.config['improvement_threshold'] and confidence >= 0.7:
            # 生成强化现有策略的策略
            insights = {
                'actionable_recommendations': [{
                    'id': 'rec_reinforce_001',
                    'title': '强化成功策略',
                    'priority': 'high',
                    'action': {
                        'type': 'matching',
                        'parameters': {
                            'reinforce_successful_patterns': True
                        }
                    }
                }]
            }

            strategies = strategy_updater.generate_strategies_from_insights(insights)
            assert len(strategies) >= 1


class TestReflectionAnalyzerIntegration:
    """反思分析器集成测试"""

    @pytest.fixture
    def analyzer(self):
        return ReflectionAnalyzer()

    @pytest.mark.asyncio
    async def test_analyze_failed_tasks_with_mock_data(self, analyzer):
        """测试：使用模拟数据分析失败任务"""
        # 模拟失败评估数据
        mock_evaluations = [
            {
                'session_id': 'sess_001',
                'task_type': 'appointment',
                'success': 0,
                'error_type': 'slot_unavailable',
                'error_message': 'Time slot taken',
                'turns_count': 5,
                'completion_time': 60.0
            },
            {
                'session_id': 'sess_002',
                'task_type': 'appointment',
                'success': 0,
                'error_type': 'slot_unavailable',
                'error_message': 'Slot taken',
                'turns_count': 3,
                'completion_time': 45.0
            },
            {
                'session_id': 'sess_003',
                'task_type': 'appointment',
                'success': 0,
                'error_type': 'timeout',
                'error_message': 'Request timeout',
                'turns_count': 10,
                'completion_time': 150.0
            },
        ]

        # 模拟 evaluation_repo 返回失败数据
        analyzer.evaluation_repo = MagicMock()
        analyzer.evaluation_repo.get_failed_evaluations.return_value = mock_evaluations
        # 禁用 LLM 以使用规则引擎
        analyzer.llm = None

        result = await analyzer.analyze_failed_tasks(task_type='appointment', days=7)

        assert 'error_type_distribution' in result
        assert result['error_type_distribution'].get('slot_unavailable', 0) == 2
        assert result['error_type_distribution'].get('timeout', 0) == 1


class TestReflectionReporterIntegration:
    """反思报告器集成测试"""

    @pytest.fixture
    def reporter(self):
        return ReflectionReporter()

    def test_generate_post_task_report(self, reporter):
        """测试：生成任务后报告"""
        evaluation_result = {
            'success': 0,
            'success_level': 'FAILED',
            'success_rate': 0.0,
            'error_type': 'slot_unavailable',
            'error_message': 'Time slot taken',
            'turns_count': 5,
            'should_reflect': True
        }

        reflection_result = {
            'reflection_id': 1,
            'trigger_type': 'post_task',
            'findings': {
                'evaluation_summary': evaluation_result,
                'recommendations': [
                    {
                        'type': 'action',
                        'title': '优化时间段检测',
                        'priority': 'high'
                    }
                ]
            }
        }

        report = reporter.generate_post_task_report(
            session_id='sess_test_001',
            evaluation_result=evaluation_result,
            reflection_result=reflection_result
        )

        assert 'session_id' in report
        assert 'evaluation' in report
        assert 'reflection' in report
        assert 'timestamp' in report


class TestEndToEndScenarios:
    """端到端场景测试"""

    def test_scenario_slot_conflict_resolution(self):
        """场景测试：时间段冲突解决"""
        # 1. 用户预约
        evaluator = TaskEvaluator()
        strategy_updater = StrategyUpdater()

        # 2. 检测到时间段冲突
        error = SlotTakenException(1, "15:00", "16:00")
        result = evaluator.evaluate_appointment_task(
            session_id="scenario_001",
            appointment_history={
                'gender': '女',
                'start_time': '15:00'
            },
            turns_count=3,
            error=error
        )

        assert result['error_type'] == 'slot_unavailable'

        # 3. 生成避免策略
        insights = {
            'recent_bad_cases': [{
                'case_id': 'bc_slot_001',
                'description': '时间段被占用',
                'task_type': 'appointment',
                'severity': 7,
                'trigger': {},
                'suggested_fix': {
                    'check_availability_first': True,
                    'offer_alternatives': True
                }
            }]
        }

        strategies = strategy_updater.generate_strategies_from_insights(insights)

        # 4. 激活策略
        if strategies:
            strategy_updater.activate_strategy(
                strategies[0].version_id,
                StrategyType.MATCHING
            )

        # 5. 应用策略
        context = strategy_updater.apply_strategy_to_context(
            {'user_request': '我要预约15:00'},
            task_type='appointment'
        )

        # 验证策略已应用
        assert context['matching_config']['check_availability_first'] == True

    def test_scenario_timeout_optimization(self):
        """场景测试：超时优化"""
        evaluator = TaskEvaluator()
        strategy_updater = StrategyUpdater()

        # 1. 检测到超时问题
        error = TimeoutError("LLM request timed out")
        result = evaluator.evaluate_appointment_task(
            session_id="scenario_002",
            appointment_history={},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'timeout'

        # 2. 生成超时优化策略
        insights = {
            'actionable_recommendations': [{
                'id': 'rec_timeout_001',
                'title': '优化超时处理',
                'priority': 'high',
                'action': {
                    'type': 'timeout',
                    'parameters': {
                        'retry_attempts': 3,
                        'fallback_enabled': True
                    }
                }
            }]
        }

        strategies = strategy_updater.generate_strategies_from_insights(insights)

        # 3. 验证策略生成
        timeout_strategies = [s for s in strategies
                             if s.strategy_type == StrategyType.TIMEOUT]
        assert len(timeout_strategies) >= 1

    def test_scenario_low_success_rate_improvement(self):
        """场景测试：低成功率改进"""
        evaluator = TaskEvaluator()
        closed_loop = ClosedLoopEvaluator()

        # 模拟两个阶段的数据对比
        # 阶段1：改进前
        improvement_before = closed_loop._calculate_improvement_rate(0.4, 0.5)

        # 阶段2：应用策略后
        improvement_after = closed_loop._calculate_improvement_rate(0.5, 0.75)

        # 验证改进率提升
        assert improvement_after > improvement_before

        # 计算置信度
        confidence = closed_loop._calculate_confidence(50, 50, improvement_after)

        # 验证效果显著
        result = closed_loop._determine_evaluation(
            improvement_after,
            confidence,
            significance=0.7
        )

        assert result == EvaluationResult.IMPROVED

    def test_scenario_multi_task_type_optimization(self):
        """场景测试：多任务类型优化"""
        evaluator = TaskEvaluator()
        strategy_updater = StrategyUpdater()

        # 1. 预约任务失败
        appointment_result = evaluator.evaluate_appointment_task(
            session_id="multi_001",
            appointment_history={},
            turns_count=5,
            error=SlotTakenException(1, "15:00", "16:00")
        )

        # 2. 咨询任务失败
        consultation_result = evaluator.evaluate_consultation_task(
            session_id="multi_002",
            consultation_data={'has_answer': False},
            turns_count=3,
            error=Exception("RAG retrieval failed")
        )

        # 3. 分类任务失败
        classification_result = evaluator.evaluate_classification_task(
            session_id="multi_003",
            classification_data={'correctly_classified': False},
            turns_count=1,
            error=Exception("Classification model error")
        )

        # 验证不同任务类型触发不同策略
        appointment_insights = {
            'recent_bad_cases': [{
                'case_id': 'bc_appt',
                'task_type': 'appointment',
                'suggested_fix': {'similarity_weight': 0.6}
            }]
        }
        appointment_strategies = strategy_updater.generate_strategies_from_insights(appointment_insights)

        consultation_insights = {
            'recent_bad_cases': [{
                'case_id': 'bc_consult',
                'task_type': 'consultation',
                'suggested_fix': {'prompt_style': 'detailed'}
            }]
        }
        consultation_strategies = strategy_updater.generate_strategies_from_insights(consultation_insights)

        # 验证生成了不同类型的策略
        assert len(appointment_strategies) >= 1
        assert len(consultation_strategies) >= 1


class TestEdgeCases:
    """边界情况测试"""

    def test_empty_insights_no_strategies(self):
        """测试：空洞察不生成策略"""
        strategy_updater = StrategyUpdater()

        strategies = strategy_updater.generate_strategies_from_insights({})
        assert len(strategies) == 0

    def test_no_evaluation_repo_graceful_degradation(self):
        """测试：无评估仓库时优雅降级"""
        evaluator = TaskEvaluator(evaluation_repo=None)

        result = evaluator.evaluate_appointment_task(
            session_id="edge_001",
            appointment_history={},
            turns_count=5
        )

        # 应该正常返回，不抛出异常
        assert 'success' in result
        assert result['evaluation_id'] is None  # 因为没有仓库

    def test_invalid_task_type_handling(self):
        """测试：无效任务类型的处理"""
        evaluator = TaskEvaluator()

        # 使用未知的任务类型应该走通用评估逻辑
        # 这里主要测试不会崩溃
        result = evaluator._generic_evaluation(
            session_id="edge_002",
            task_type="unknown_task",
            task_result={},
            turns_count=1,
            completion_time=10.0,
            error=None
        )

        assert result['success'] == 1
        assert 'success_level' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
