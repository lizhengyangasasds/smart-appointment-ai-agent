"""
StrategyUpdater 单元测试

验证策略更新器的策略生成、激活、回滚等逻辑
"""

import pytest
import sys
import os
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reflection.strategy_updater import (
    StrategyUpdater,
    StrategyType,
    StrategyStatus,
    StrategyVersion
)
from db.repositories.reflection_repository import StrategyRepository


class TestStrategyUpdater:
    """StrategyUpdater 单元测试"""

    @pytest.fixture
    def updater(self):
        """创建策略更新器实例"""
        return StrategyUpdater()

    # ===== 默认策略初始化测试 =====

    def test_default_strategies_initialized(self, updater):
        """测试：默认策略已初始化"""
        for strategy_type in StrategyType:
            active = updater.get_active_strategy(strategy_type)
            assert active is not None, f"策略 {strategy_type.value} 未初始化"
            assert 'version_id' in active
            assert 'config' in active

    def test_all_strategy_types_have_default(self, updater):
        """测试：所有策略类型都有默认配置"""
        expected_types = [
            StrategyType.MATCHING,
            StrategyType.RECOMMENDATION,
            StrategyType.ROUTING,
            StrategyType.PROMPT,
            StrategyType.TIMEOUT
        ]

        for st in expected_types:
            active = updater.get_active_strategy(st)
            assert active is not None
            assert 'default' in active['version_id'].lower() or active['name']

    def test_default_matching_strategy_config(self, updater):
        """测试：默认匹配策略配置"""
        config = updater.get_active_strategy(StrategyType.MATCHING)['config']

        assert 'similarity_weight' in config
        assert 'gender_preference_weight' in config
        assert 'availability_weight' in config
        assert config['fallback_enabled'] == True
        assert config['max_candidates'] == 5

    def test_default_recommendation_strategy_config(self, updater):
        """测试：默认推荐策略配置"""
        config = updater.get_active_strategy(StrategyType.RECOMMENDATION)['config']

        assert 'personalization_level' in config
        assert 'diversity_weight' in config
        assert 'recency_weight' in config
        assert config['cold_start_mode'] == 'popularity'

    # ===== 从坏 case 生成策略测试 =====

    def test_generate_avoidance_strategy_from_bad_case(self, updater):
        """测试：从坏 case 生成避免策略"""
        bad_case = {
            'case_id': 'bc_weekend_001',
            'description': '周末下午预约失败率高',
            'task_type': 'appointment',
            'severity': 8,
            'trigger': {
                'time_slot': 'weekend_afternoon'
            },
            'suggested_fix': {
                'weekend_weight': -0.2,
                'prefer_workday': True,
                'similarity_weight': 0.5
            }
        }

        strategies = updater.generate_strategies_from_insights({
            'recent_bad_cases': [bad_case]
        })

        assert len(strategies) >= 1

        # 找到生成的避免策略
        avoidance_strategy = None
        for s in strategies:
            if 'avoid' in s.version_id.lower() or 'avoid' in s.name.lower():
                avoidance_strategy = s
                break

        assert avoidance_strategy is not None
        assert avoidance_strategy.strategy_type == StrategyType.MATCHING
        assert 'weekend_weight' in avoidance_strategy.config
        assert avoidance_strategy.config['weekend_weight'] == -0.2
        assert avoidance_strategy.priority == 8

    def test_generate_avoidance_strategy_consultation(self, updater):
        """测试：从咨询类坏 case 生成 prompt 策略"""
        bad_case = {
            'case_id': 'bc_rag_001',
            'description': 'RAG 检索失败率高',
            'task_type': 'consultation',
            'severity': 7,
            'trigger': {},
            'suggested_fix': {
                'prompt_style': 'detailed',
                'fallback_enabled': True
            }
        }

        strategies = updater.generate_strategies_from_insights({
            'recent_bad_cases': [bad_case]
        })

        # 咨询类应该生成 PROMPT 策略
        prompt_strategies = [s for s in strategies if s.strategy_type == StrategyType.PROMPT]
        assert len(prompt_strategies) >= 1

    def test_generate_avoidance_strategy_no_fix(self, updater):
        """测试：坏 case 无 suggested_fix 时不生成策略"""
        bad_case = {
            'case_id': 'bc_empty_001',
            'description': '无修复建议的坏 case',
            'task_type': 'appointment',
            'trigger': {},
            # 没有 suggested_fix
        }

        strategies = updater.generate_strategies_from_insights({
            'recent_bad_cases': [bad_case]
        })

        # 应该没有生成策略（因为没有 suggested_fix）
        avoid_strategies = [s for s in strategies
                           if 'avoid' in s.version_id.lower()]
        assert len(avoid_strategies) == 0

    # ===== 从推荐生成策略测试 =====

    def test_generate_optimization_strategy_from_recommendation(self, updater):
        """测试：从推荐生成优化策略"""
        recommendation = {
            'id': 'rec_001',
            'title': '增加相似度权重',
            'priority': 'high',
            'action': {
                'type': 'matching',
                'parameters': {
                    'similarity_weight': 0.6,
                    'gender_preference_weight': 0.3
                }
            }
        }

        strategies = updater.generate_strategies_from_insights({
            'actionable_recommendations': [recommendation]
        })

        assert len(strategies) >= 1

        # 找到生成的优化策略
        opt_strategy = None
        for s in strategies:
            if 'opt' in s.version_id.lower() or '优化' in s.name:
                opt_strategy = s
                break

        assert opt_strategy is not None
        assert opt_strategy.strategy_type == StrategyType.MATCHING
        assert opt_strategy.config['similarity_weight'] == 0.6
        assert opt_strategy.priority == 10  # high priority = 10

    def test_generate_optimization_strategy_low_priority(self, updater):
        """测试：低优先级推荐生成低优先级策略"""
        recommendation = {
            'id': 'rec_002',
            'title': '次要优化',
            'priority': 'medium',  # 不是 high
            'action': {
                'type': 'matching',
                'parameters': {'test_param': 0.5}
            }
        }

        strategies = updater.generate_strategies_from_insights({
            'actionable_recommendations': [recommendation]
        })

        opt_strategies = [s for s in strategies if 'opt' in s.version_id.lower()]
        if opt_strategies:
            assert opt_strategies[0].priority == 5  # non-high priority = 5

    def test_generate_optimization_strategy_invalid_type(self, updater):
        """测试：无效的策略类型时使用默认类型"""
        recommendation = {
            'id': 'rec_003',
            'title': '无效类型测试',
            'priority': 'high',
            'action': {
                'type': 'invalid_type_xyz',  # 无效类型
                'parameters': {}
            }
        }

        strategies = updater.generate_strategies_from_insights({
            'actionable_recommendations': [recommendation]
        })

        # 应该使用默认的 MATCHING 策略
        assert len(strategies) >= 1
        assert strategies[0].strategy_type == StrategyType.MATCHING

    # ===== 从模式分析生成策略测试 =====

    def test_generate_adaptation_strategy_user_preference(self, updater):
        """测试：从用户偏好模式生成推荐策略"""
        insights = {
            'pattern_insights': {
                'user_preference': {
                    'confidence': 0.8,
                    'parameters': {
                        'preferred_gender': 'female'
                    }
                }
            }
        }

        strategies = updater.generate_strategies_from_insights(insights)

        adapt_strategies = [s for s in strategies
                          if 'adapt' in s.version_id.lower()]
        assert len(adapt_strategies) >= 1

        # 用户偏好应该生成 RECOMMENDATION 策略
        rec_strategies = [s for s in adapt_strategies
                         if s.strategy_type == StrategyType.RECOMMENDATION]
        assert len(rec_strategies) >= 1

    def test_generate_adaptation_strategy_time_pattern(self, updater):
        """测试：从时间模式生成匹配策略"""
        insights = {
            'pattern_insights': {
                'time_pattern': {
                    'confidence': 0.7,
                    'parameters': {
                        'peak_hours': ['14:00', '15:00', '16:00']
                    }
                }
            }
        }

        strategies = updater.generate_strategies_from_insights(insights)

        adapt_strategies = [s for s in strategies
                          if s.strategy_type == StrategyType.MATCHING]
        assert len(adapt_strategies) >= 1

    # ===== 策略激活和回滚测试 =====

    def test_strategy_activation(self, updater):
        """测试：策略激活"""
        # 先生成一个新策略
        strategies = updater.generate_strategies_from_insights({
            'actionable_recommendations': [{
                'id': 'rec_activation_test',
                'title': '激活测试策略',
                'priority': 'high',
                'action': {
                    'type': 'matching',
                    'parameters': {'test_value': 0.99}
                }
            }]
        })

        if strategies:
            new_strategy = strategies[0]

            # 激活策略
            result = updater.activate_strategy(
                new_strategy.version_id,
                new_strategy.strategy_type
            )

            assert result == True

            # 验证策略已激活
            active = updater.get_active_strategy(new_strategy.strategy_type)
            assert active['version_id'] == new_strategy.version_id

    def test_strategy_activation_invalid_version(self, updater):
        """测试：激活无效版本返回 False"""
        result = updater.activate_strategy(
            'invalid_version_id_xyz',
            StrategyType.MATCHING
        )

        assert result == False

    def test_strategy_rollback(self, updater):
        """测试：策略回滚到默认版本"""
        # 先激活一个新策略
        strategies = updater.generate_strategies_from_insights({
            'actionable_recommendations': [{
                'id': 'rec_rollback_test',
                'title': '回滚测试',
                'priority': 'high',
                'action': {
                    'type': 'matching',
                    'parameters': {'rollback_test': True}
                }
            }]
        })

        if strategies:
            updater.activate_strategy(
                strategies[0].version_id,
                StrategyType.MATCHING
            )

            # 回滚
            result = updater.rollback_strategy(StrategyType.MATCHING)

            assert result == True

            # 验证回到了默认策略
            active = updater.get_active_strategy(StrategyType.MATCHING)
            assert 'default' in active['version_id'].lower()

    # ===== 策略应用测试 =====

    def test_apply_strategy_to_context_appointment(self, updater):
        """测试：应用策略到预约任务上下文"""
        context = {'user_id': 'test_user', 'session_id': 'test_session'}

        updated_context = updater.apply_strategy_to_context(
            context,
            task_type='appointment'
        )

        assert 'matching_config' in updated_context
        assert 'recommendation_config' in updated_context

    def test_apply_strategy_to_context_consultation(self, updater):
        """测试：应用策略到咨询任务上下文"""
        context = {'user_id': 'test_user', 'session_id': 'test_session'}

        updated_context = updater.apply_strategy_to_context(
            context,
            task_type='consultation'
        )

        assert 'prompt_config' in updated_context

    # ===== 获取活跃策略测试 =====

    def test_get_all_active_strategies(self, updater):
        """测试：获取所有活跃策略"""
        all_strategies = updater.get_all_active_strategies()

        assert StrategyType.MATCHING.value in all_strategies
        assert StrategyType.RECOMMENDATION.value in all_strategies
        assert StrategyType.ROUTING.value in all_strategies
        assert StrategyType.PROMPT.value in all_strategies
        assert StrategyType.TIMEOUT.value in all_strategies

        for st_value, strategy_info in all_strategies.items():
            assert strategy_info is not None
            assert 'version_id' in strategy_info
            assert 'config' in strategy_info

    # ===== 策略导出导入测试 =====

    def test_export_strategies(self, updater):
        """测试：导出策略"""
        export_data = updater.export_strategies()

        assert 'exported_at' in export_data
        assert 'strategies' in export_data
        assert 'active_strategies' in export_data

        for st in StrategyType:
            assert st.value in export_data['strategies']

    def test_import_strategies(self, updater):
        """测试：导入策略"""
        # 先导出
        export_data = updater.export_strategies()

        # 创建新的 updater
        new_updater = StrategyUpdater()

        # 导入
        new_updater.import_strategies(export_data)

        # 验证导入成功
        for st in StrategyType:
            active = new_updater.get_active_strategy(st)
            assert active is not None

    # ===== 策略优先级测试 =====

    def test_high_severity_bad_case_high_priority(self, updater):
        """测试：高严重性坏 case 生成高优先级策略"""
        bad_case = {
            'case_id': 'bc_high_severity',
            'description': '严重问题',
            'task_type': 'appointment',
            'severity': 10,  # 最高优先级
            'trigger': {},
            'suggested_fix': {'test': True}
        }

        strategies = updater.generate_strategies_from_insights({
            'recent_bad_cases': [bad_case]
        })

        if strategies:
            assert strategies[0].priority == 10


class TestStrategyVersion:
    """StrategyVersion 数据类测试"""

    def test_strategy_version_creation(self):
        """测试：策略版本创建"""
        strategy = StrategyVersion(
            version_id="test_v1",
            strategy_type=StrategyType.MATCHING,
            name="测试策略",
            config={'test_param': 1.0},
            priority=5,
            trigger_reason="单元测试创建"
        )

        assert strategy.version_id == "test_v1"
        assert strategy.strategy_type == StrategyType.MATCHING
        assert strategy.config['test_param'] == 1.0
        assert strategy.priority == 5

    def test_strategy_version_default_status(self):
        """测试：默认状态为 PENDING"""
        strategy = StrategyVersion(
            version_id="test_v2",
            strategy_type=StrategyType.RECOMMENDATION,
            name="测试策略2",
            config={}
        )

        assert strategy.status == StrategyStatus.PENDING


class TestStrategyUpdaterPersistence:
    """StrategyUpdater 端到端持久化测试

    覆盖闭环链路：
      反思洞察 → generate_strategies_from_insights → activate_strategy → DB 落盘
      → 进程重启（构造新实例） → 从 DB hydrate 恢复活跃策略

    每个测试用独立 version_id 前缀隔离，避免跨测试污染。
    """

    @pytest.fixture
    def repo(self):
        """提供 StrategyRepository 实例（不清理历史数据，靠 version_id 前缀隔离）"""
        return StrategyRepository()

    @pytest.fixture
    def fresh_updater(self, repo):
        """每个测试拿到独立的 StrategyUpdater 实例（基于 import-id 前缀生成）"""
        # 用 id(self) 让 version_id 各测试独立
        return StrategyUpdater(strategy_repo=repo)

    def test_default_strategies_persisted_on_first_init_for_fresh_type(self, repo):
        """首次构造某 strategy_type 的活跃策略时（DB 还没有），默认策略被落盘

        注意：本测试用 fresh type，避免和其他测试共用 matching。
        timeout 是相对独立的策略类型，且该 fixture 之前没人为激活过。
        """
        # 强制把"如果有 active"先归档掉，模拟"DB 里这个 type 还没人写过"
        for v in repo.get_versions_by_type('timeout'):
            if v['is_active'] == 1:
                repo.rollback('timeout')

        StrategyUpdater(strategy_repo=repo)

        active = repo.load_all_active()
        assert 'timeout' in active, 'timeout 默认策略应该被落盘'
        assert active['timeout']['version_id'].startswith('default_timeout_')
        assert active['timeout']['is_active'] == 1
        assert active['timeout']['status'] == 'active'

    def test_activate_strategy_persists_and_deactivates_others(self, repo, fresh_updater):
        """activate_strategy 后：目标版本落库并 active=1；同 type 默认版本变 archived"""
        su = fresh_updater
        # 用规则的 generate 路径（无 LLM）
        strategies = su.generate_strategies_from_insights({
            'recent_bad_cases': [{
                'case_id': 'PERSIST-1',
                'task_type': 'appointment',
                'description': '持久化测试1',
                'severity': 6,
                'suggested_fix': {'similarity_weight': 0.7, 'max_candidates': 7},
            }]
        })
        assert len(strategies) == 1
        target = strategies[0]

        # 关键：generate 之后必须能 activate（之前 bug：生成但没注册到 _strategies）
        ok = su.activate_strategy(target.version_id, target.strategy_type)
        assert ok, 'activate_strategy 应该能找到刚生成的版本'

        # 验证 DB 状态
        matching_versions = repo.get_versions_by_type('matching')
        active_rows = [v for v in matching_versions if v['is_active'] == 1]
        assert len(active_rows) == 1, f'matching 应该只有一个 active，实际={len(active_rows)}'
        assert active_rows[0]['version_id'] == target.version_id

        # 同 type 的 default 应该 archived
        default_row = [v for v in matching_versions if v['version_id'].startswith('default_')][0]
        assert default_row['is_active'] == 0
        assert default_row['status'] == 'archived'

    def test_rollback_restores_default_in_db(self, repo, fresh_updater):
        """rollback 后：default 版本恢复 active=1，之前 active 的版本变 rolled_back"""
        su = fresh_updater
        # 先激活一个非默认版本
        strategies = su.generate_strategies_from_insights({
            'recent_bad_cases': [{
                'case_id': 'PERSIST-2',
                'task_type': 'appointment',
                'description': 'rollback 测试',
                'severity': 5,
                'suggested_fix': {'similarity_weight': 0.65},
            }]
        })
        su.activate_strategy(strategies[0].version_id, strategies[0].strategy_type)

        # 回滚
        ok = su.rollback_strategy(StrategyType.MATCHING)
        assert ok, 'rollback 应该成功（即使 default 当前是 archived 也能找到）'

        matching_versions = repo.get_versions_by_type('matching')
        active_rows = [v for v in matching_versions if v['is_active'] == 1]
        assert len(active_rows) == 1
        assert active_rows[0]['version_id'].startswith('default_matching_')

        # 之前激活的避免策略应该 rolled_back
        avoid_row = [v for v in matching_versions if v['version_id'].startswith('avoid_PERSIST-2')][0]
        assert avoid_row['status'] == 'rolled_back'

    def test_restart_hydrates_active_strategies_from_db(self, repo):
        """进程重启（构造新 StrategyUpdater）应该从 DB 恢复活跃策略"""
        # 阶段 1：触发一个非默认策略并落库
        su1 = StrategyUpdater(strategy_repo=repo)
        strategies = su1.generate_strategies_from_insights({
            'recent_bad_cases': [{
                'case_id': 'RESTART-1',
                'task_type': 'appointment',
                'description': '重启恢复测试',
                'severity': 7,
                'suggested_fix': {'similarity_weight': 0.8, 'special_marker': 'restart_test'},
            }]
        })
        target = strategies[0]
        su1.activate_strategy(target.version_id, target.strategy_type)

        # 阶段 2：模拟重启
        su2 = StrategyUpdater(strategy_repo=repo)

        active = su2._active_strategies.get('matching')
        assert active is not None
        assert active.version_id == target.version_id, \
            f'重启后 matching 应该恢复成 {target.version_id}，实际={active.version_id}'
        assert active.config.get('special_marker') == 'restart_test', \
            'config 也应从 DB 恢复'

    def test_activate_unknown_version_returns_false_without_db_change(self, repo, fresh_updater):
        """activate 一个不存在的 version_id 应该返回 False，DB 状态不变"""
        su = fresh_updater
        before = {v['version_id']: v['is_active']
                  for v in repo.get_versions_by_type('matching')}

        ok = su.activate_strategy('nonexistent_version_xyz', StrategyType.MATCHING)
        assert ok is False

        after = {v['version_id']: v['is_active']
                 for v in repo.get_versions_by_type('matching')}
        assert before == after, '失败的 activate 不应改 DB'

    def test_repository_upsert_is_idempotent(self, repo):
        """save_version 同 version_id 重复调用应幂等（不抛异常、不重复插入）"""
        v_id = 'IDEMPOTENT-1'
        first = repo.save_version(
            version_id=v_id,
            strategy_type='matching',
            name='第一次',
            config={'a': 1},
        )
        second = repo.save_version(
            version_id=v_id,
            strategy_type='matching',
            name='第二次',
            config={'b': 2},  # 即使 config 不同，也不覆盖
        )
        assert first == second, '幂等返回的 row id 应相同'

        # 实际保存的应该是第一次的 config
        versions = [v for v in repo.get_versions_by_type('matching') if v['version_id'] == v_id]
        assert len(versions) == 1
        assert versions[0]['name'] == '第一次'
        assert versions[0]['config'] == {'a': 1}


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
