"""
反思闭环组件单元测试

直接导入闭环组件，绕过 agents 包导入
"""
import sys
import os

# 确保路径正确
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 直接导入闭环组件
from agents.reflection.reflection_aware import ReflectionAwareMixin
from agents.reflection.strategy_updater import StrategyUpdater, StrategyType, StrategyStatus
from agents.reflection.closed_loop_evaluator import ClosedLoopEvaluator, EvaluationResult
from agents.reflection.context_provider import ReflectionContextProvider, ContextFormat
from agents.reflection.engine import ReflectionEngine


class TestReflectionAwareMixin:
    """测试反思感知混入类"""

    def test_get_default_insights_when_no_engine(self):
        """测试当没有反思引擎时返回默认洞察"""
        class TestAgent(ReflectionAwareMixin):
            def apply_insights(self, insights):
                pass

        agent = TestAgent()
        insights = agent.get_insights()

        assert insights['summary'] == '暂无反思数据'
        assert insights['actionable_recommendations'] == []

    def test_should_avoid_pattern(self):
        """测试模式避免检查"""
        class TestAgent(ReflectionAwareMixin):
            def apply_insights(self, insights):
                pass

        agent = TestAgent()

        # 默认没有 avoid_patterns，应该返回 False
        assert agent.should_avoid_pattern('any_pattern') == False

    def test_validate_action_no_bad_cases(self):
        """测试无坏case时的验证"""
        class TestAgent(ReflectionAwareMixin):
            def apply_insights(self, insights):
                pass

        agent = TestAgent()
        agent._cached_insights = {'recent_bad_cases': []}

        result = agent.validate_action_against_insights(
            action={'type': 'test'},
            context={}
        )

        assert result['valid'] == True


class TestStrategyUpdater:
    """测试策略更新器"""

    def test_init_default_strategies(self):
        """测试初始化默认策略"""
        updater = StrategyUpdater()

        for st in StrategyType:
            active = updater.get_active_strategy(st)
            assert active is not None
            assert 'version_id' in active
            assert 'config' in active

    def test_generate_strategies_from_insights(self):
        """测试从洞察生成策略"""
        updater = StrategyUpdater()

        insights = {
            'recent_insights': [],
            'actionable_recommendations': [
                {
                    'id': 'rec1',
                    'title': '提高相似度权重',
                    'priority': 'high',
                    'action': {
                        'type': 'matching',
                        'parameters': {'similarity_weight': 0.6}
                    }
                }
            ],
            'recent_bad_cases': [
                {
                    'case_id': 'bc1',
                    'description': '性别不匹配问题',
                    'task_type': 'appointment',
                    'suggested_fix': {'gender_preference_weight': 0.3}
                }
            ],
            'pattern_insights': {}
        }

        strategies = updater.generate_strategies_from_insights(insights)
        assert len(strategies) >= 1

    def test_rollback_strategy(self):
        """测试策略回滚"""
        updater = StrategyUpdater()
        after = updater.get_active_strategy(StrategyType.MATCHING)
        assert 'default' in after['version_id']


class TestClosedLoopEvaluator:
    """测试闭环效果验证器"""

    def test_insufficient_data_result(self):
        """测试数据不足时的结果"""
        evaluator = ClosedLoopEvaluator()

        result = evaluator.evaluate_strategy_improvement(
            strategy_version_id='test_v1',
            task_type='appointment'
        )

        assert result.evaluation == EvaluationResult.INSUFFICIENT_DATA

    def test_calculate_improvement_rate(self):
        """测试改进率计算"""
        evaluator = ClosedLoopEvaluator()

        rate = evaluator._calculate_improvement_rate(0.8, 0.9)
        assert abs(rate - 0.125) < 0.001  # 使用近似比较


class TestReflectionContextProvider:
    """测试反思上下文提供者"""

    def test_get_context_compact_format(self):
        """测试紧凑格式上下文"""
        provider = ReflectionContextProvider()

        context = provider.get_context_for_agent(
            session_id='test_session',
            task_type='appointment',
            format=ContextFormat.COMPACT
        )

        assert context.session_id == 'test_session'
        assert context.task_type == 'appointment'

    def test_clear_cache(self):
        """测试清除缓存"""
        provider = ReflectionContextProvider()
        provider._context_cache['test'] = None
        provider.clear_cache()
        assert len(provider._context_cache) == 0


class TestReflectionEngineClosedLoop:
    """测试反思引擎闭环功能"""

    def test_engine_has_closed_loop_components(self):
        """测试反思引擎包含闭环组件"""
        engine = ReflectionEngine()

        assert hasattr(engine, 'strategy_updater')
        assert hasattr(engine, 'closed_loop_evaluator')
        assert hasattr(engine, 'context_provider')

        assert isinstance(engine.strategy_updater, StrategyUpdater)
        assert isinstance(engine.closed_loop_evaluator, ClosedLoopEvaluator)
        assert isinstance(engine.context_provider, ReflectionContextProvider)

    def test_engine_run_closed_loop_cycle(self):
        """测试引擎运行闭环周期"""
        engine = ReflectionEngine()

        result = engine.run_closed_loop_cycle(task_type='appointment')

        assert 'insights_generated' in result
        assert 'strategies_updated' in result
        assert 'evaluation_results' in result
        assert 'timestamp' in result


if __name__ == '__main__':
    # 运行测试
    import pytest
    pytest.main([__file__, '-v'])
