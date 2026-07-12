"""
TaskEvaluator 单元测试

验证任务评估器的评估逻辑正确性
"""

import pytest
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reflection.evaluator import TaskEvaluator, SuccessLevel
from db.base.exceptions import SlotTakenException


class TestTaskEvaluator:
    """TaskEvaluator 单元测试"""

    @pytest.fixture
    def evaluator(self):
        """创建评估器实例"""
        return TaskEvaluator()

    # ===== 预约任务评估测试 =====

    def test_successful_appointment_evaluation(self, evaluator):
        """测试：预约任务完全成功"""
        result = evaluator.evaluate_appointment_task(
            session_id="test_001",
            appointment_history={
                'gender': '女',
                'start_time': '2026-06-26 15:00',
                'duration': '60',
                'project': '全身按摩'
            },
            turns_count=5,
            completion_time=60.0,
            error=None
        )

        assert result['success'] == SuccessLevel.SUCCESS
        assert result['success_rate'] == 1.0
        assert result['success_level'] == 'SUCCESS'
        assert result['error_type'] is None
        assert len(result['completed_fields']) == 4
        assert len(result['missing_fields']) == 0

    def test_failed_evaluation_with_slot_exception(self, evaluator):
        """测试：时间段冲突错误分类"""
        error = SlotTakenException(1, "15:00", "16:00")

        result = evaluator.evaluate_appointment_task(
            session_id="test_002",
            appointment_history={'gender': '女', 'start_time': '15:00'},
            turns_count=3,
            error=error
        )

        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'slot_unavailable'
        assert result['success_rate'] == 0.0

    def test_timeout_error_classification(self, evaluator):
        """测试：超时错误分类"""
        error = TimeoutError("LLM request timed out after 30s")

        result = evaluator.evaluate_appointment_task(
            session_id="test_003",
            appointment_history={},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'timeout'
        assert result['success'] == SuccessLevel.FAILED

    def test_parse_error_classification(self, evaluator):
        """测试：JSON解析错误分类"""
        error = ValueError("JSON parse error at line 1")

        result = evaluator.evaluate_appointment_task(
            session_id="test_004",
            appointment_history={},
            turns_count=2,
            error=error
        )

        assert result['error_type'] == 'parse_error'

    def test_database_error_classification(self, evaluator):
        """测试：数据库错误分类"""
        error = Exception("Database connection failed")

        result = evaluator.evaluate_appointment_task(
            session_id="test_005",
            appointment_history={},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'database_error'

    def test_llm_error_classification(self, evaluator):
        """测试：LLM错误分类"""
        error = Exception("LLM API error: rate limit exceeded")

        result = evaluator.evaluate_appointment_task(
            session_id="test_006",
            appointment_history={},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'llm_error'

    def test_auth_error_classification(self, evaluator):
        """测试：认证错误分类"""
        error = Exception("Permission denied for user")

        result = evaluator.evaluate_appointment_task(
            session_id="test_007",
            appointment_history={},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'auth_error'

    def test_partial_completion_incomplete_info(self, evaluator):
        """测试：部分成功 - 信息不完整（完成率 >= 0.5）"""
        result = evaluator.evaluate_appointment_task(
            session_id="test_008",
            appointment_history={
                'gender': '女',
                'start_time': '15:00',
                # 缺少 duration 和 project
            },
            turns_count=8,
            completion_time=120.0
        )

        # completion_rate = 2/4 = 0.5
        assert result['success'] == SuccessLevel.PARTIAL
        assert result['completion_rate'] == 0.5
        assert result['error_type'] == 'incomplete_info'

    def test_partial_completion_low_completion(self, evaluator):
        """测试：部分成功 - 完成度过低（完成率 < 0.5）"""
        result = evaluator.evaluate_appointment_task(
            session_id="test_009",
            appointment_history={
                'gender': '女',
                # 只完成了 1/4 = 0.25
            },
            turns_count=3
        )

        assert result['success'] == SuccessLevel.FAILED
        assert result['completion_rate'] == 0.25
        assert result['error_type'] == 'low_completion'

    def test_reflection_triggered_by_low_success_rate(self, evaluator):
        """测试：成功率低于阈值触发反思"""
        result = evaluator.evaluate_appointment_task(
            session_id="test_010",
            appointment_history={
                'gender': '女',
                'start_time': '15:00',
                'duration': '60',
                # 缺少 project
            },
            turns_count=5,
            completion_time=60.0
        )

        # 完成率 3/4 = 0.75，低于 0.7 阈值
        assert result['should_reflect'] == True

    def test_reflection_triggered_by_high_turns(self, evaluator):
        """测试：对话轮数过多触发反思"""
        result = evaluator.evaluate_appointment_task(
            session_id="test_011",
            appointment_history={
                'gender': '女',
                'start_time': '15:00',
                'duration': '60',
                'project': '全身按摩'
            },
            turns_count=15,  # 超过 10 轮阈值
            completion_time=60.0
        )

        assert result['should_reflect'] == True
        assert result['success'] == SuccessLevel.SUCCESS

    def test_reflection_triggered_by_slow_completion(self, evaluator):
        """测试：完成时间过长触发反思"""
        result = evaluator.evaluate_appointment_task(
            session_id="test_012",
            appointment_history={
                'gender': '女',
                'start_time': '15:00',
                'duration': '60',
                'project': '全身按摩'
            },
            turns_count=5,
            completion_time=180.0  # 超过 120 秒阈值
        )

        assert result['should_reflect'] == True

    # ===== 咨询任务评估测试 =====

    def test_successful_consultation_with_knowledge_hit(self, evaluator):
        """测试：咨询成功 - 知识库命中且质量高"""
        result = evaluator.evaluate_consultation_task(
            session_id="test_consult_001",
            consultation_data={
                'has_answer': True,
                'answer_quality': 0.9,
                'knowledge_hit': True
            },
            turns_count=2,
            completion_time=30.0
        )

        assert result['success'] == SuccessLevel.SUCCESS
        assert result['success_rate'] >= 0.8
        assert result['error_type'] is None

    def test_partial_consultation_has_answer(self, evaluator):
        """测试：咨询部分成功 - 有答案但质量一般"""
        result = evaluator.evaluate_consultation_task(
            session_id="test_consult_002",
            consultation_data={
                'has_answer': True,
                'answer_quality': 0.5,
                'knowledge_hit': False
            },
            turns_count=3
        )

        assert result['success'] == SuccessLevel.PARTIAL
        assert result['error_type'] is None

    def test_failed_consultation_no_answer(self, evaluator):
        """测试：咨询失败 - 无答案"""
        result = evaluator.evaluate_consultation_task(
            session_id="test_consult_003",
            consultation_data={
                'has_answer': False,
                'answer_quality': 0.0,
                'knowledge_hit': False
            },
            turns_count=1
        )

        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'no_answer'

    def test_consultation_with_error(self, evaluator):
        """测试：咨询失败 - 系统错误"""
        error = TimeoutError("RAG retrieval timeout")

        result = evaluator.evaluate_consultation_task(
            session_id="test_consult_004",
            consultation_data={
                'has_answer': False,
                'answer_quality': 0.0,
                'knowledge_hit': False
            },
            turns_count=1,
            error=error
        )

        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'timeout'

    # ===== 分类任务评估测试 =====

    def test_successful_classification(self, evaluator):
        """测试：分类成功"""
        result = evaluator.evaluate_classification_task(
            session_id="test_class_001",
            classification_data={
                'correctly_classified': True
            },
            turns_count=1
        )

        assert result['success'] == SuccessLevel.SUCCESS
        assert result['success_rate'] == 1.0
        assert result['correctly_classified'] == True

    def test_failed_classification_misclassification(self, evaluator):
        """测试：分类失败 - 错误分类"""
        result = evaluator.evaluate_classification_task(
            session_id="test_class_002",
            classification_data={
                'correctly_classified': False
            },
            turns_count=1
        )

        assert result['success'] == SuccessLevel.PARTIAL
        assert result['success_rate'] == 0.5
        assert result['error_type'] == 'misclassification'

    def test_classification_with_error(self, evaluator):
        """测试：分类失败 - 系统错误"""
        error = Exception("Classification model unavailable")

        result = evaluator.evaluate_classification_task(
            session_id="test_class_003",
            classification_data={
                'correctly_classified': False
            },
            turns_count=1,
            error=error
        )

        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'unknown_error'

    # ===== 阈值配置测试 =====

    def test_custom_thresholds(self, evaluator):
        """测试：自定义阈值"""
        evaluator.update_thresholds(
            success_rate=0.8,
            turns_high=5,
            completion_time=60
        )

        assert evaluator.thresholds['success_rate'] == 0.8
        assert evaluator.thresholds['turns_high'] == 5
        assert evaluator.thresholds['completion_time'] == 60

    def test_should_not_reflect_on_successful_task(self, evaluator):
        """测试：成功的任务不应触发反思（除非配置允许）"""
        result = evaluator.evaluate_appointment_task(
            session_id="test_013",
            appointment_history={
                'gender': '女',
                'start_time': '15:00',
                'duration': '60',
                'project': '全身按摩'
            },
            turns_count=3,
            completion_time=30.0
        )

        # 成功率 100%，轮数 3（<10），时间 30s（<120s）
        assert result['should_reflect'] == False

    # ===== 错误类型分类方法测试 =====

    def test_classify_error_timeout(self, evaluator):
        """测试：_classify_error 方法 - timeout"""
        error = TimeoutError("timed out waiting for response")
        assert evaluator._classify_error(error) == 'timeout'

        error = Exception("operation timeout")
        assert evaluator._classify_error(error) == 'timeout'

    def test_classify_error_slot_unavailable(self, evaluator):
        """测试：_classify_error 方法 - slot_unavailable"""
        error = SlotTakenException(1, "15:00", "16:00")
        assert evaluator._classify_error(error) == 'slot_unavailable'

        error = Exception("time slot taken")
        assert evaluator._classify_error(error) == 'slot_unavailable'

    def test_classify_error_parse_error(self, evaluator):
        """测试：_classify_error 方法 - parse_error"""
        error = ValueError("json parse error")
        assert evaluator._classify_error(error) == 'parse_error'

        error = Exception("invalid json format")
        assert evaluator._classify_error(error) == 'parse_error'

    def test_classify_error_database_error(self, evaluator):
        """测试：_classify_error 方法 - database_error"""
        error = Exception("database connection lost")
        assert evaluator._classify_error(error) == 'database_error'

        error = Exception("db deadlock detected")
        assert evaluator._classify_error(error) == 'database_error'

    def test_classify_error_llm_error(self, evaluator):
        """测试：_classify_error 方法 - llm_error"""
        error = Exception("llm api error")
        assert evaluator._classify_error(error) == 'llm_error'

        error = Exception("model not found")
        assert evaluator._classify_error(error) == 'llm_error'

        error = Exception("openai api rate limit")
        assert evaluator._classify_error(error) == 'llm_error'

    def test_classify_error_auth_error(self, evaluator):
        """测试：_classify_error 方法 - auth_error"""
        error = Exception("permission denied")
        assert evaluator._classify_error(error) == 'auth_error'

        error = Exception("auth token expired")
        assert evaluator._classify_error(error) == 'auth_error'

    def test_classify_error_unknown(self, evaluator):
        """测试：_classify_error 方法 - unknown_error"""
        error = Exception("some weird error that doesn't match any pattern")
        assert evaluator._classify_error(error) == 'unknown_error'

    def test_classify_error_none(self, evaluator):
        """测试：_classify_error 方法 - 无错误"""
        assert evaluator._classify_error(None) is None


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
