"""
错误分类单元测试

验证所有错误类型都能被正确分类（参数化测试）
"""

import pytest
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reflection.evaluator import TaskEvaluator
from db.base.exceptions import SlotTakenException


class TestErrorClassification:
    """错误分类单元测试 - 参数化测试覆盖所有错误类型"""

    @pytest.fixture
    def evaluator(self):
        """创建评估器实例"""
        return TaskEvaluator()

    # ===== Timeout 错误测试 =====

    @pytest.mark.parametrize("error_msg", [
        "Request timeout after 30s",
        "Connection timed out",
        "Operation timed out waiting for response",
        "Read timed out",
        "Timeout: LLM request exceeded 60s",
    ])
    def test_error_type_timeout(self, evaluator, error_msg):
        """测试：timeout 错误分类"""
        error = Exception(error_msg)
        result = evaluator._classify_error(error)
        assert result == 'timeout', f"Expected 'timeout' for: {error_msg}"

    # ===== Slot Unavailable 错误测试 =====

    @pytest.mark.parametrize("error_msg", [
        "Time slot already taken for technician 1",
        "Slot 15:00-16:00 is taken",
        "The requested slot has been taken",
        "Appointment slot taken by another user",
        "Time slot taken - please choose another",
    ])
    def test_error_type_slot_unavailable(self, evaluator, error_msg):
        """测试：slot_unavailable 错误分类"""
        error = Exception(error_msg)
        result = evaluator._classify_error(error)
        assert result == 'slot_unavailable', f"Expected 'slot_unavailable' for: {error_msg}"

    def test_error_type_slot_taken_exception(self, evaluator):
        """测试：SlotTakenException 直接分类"""
        error = SlotTakenException(1, "15:00", "16:00")
        result = evaluator._classify_error(error)
        assert result == 'slot_unavailable'

    # ===== Parse Error 错误测试 =====

    @pytest.mark.parametrize("error_msg", [
        "JSON parse error at line 1",
        "Invalid JSON: unexpected token",
        "Failed to parse JSON response",
        "JSONDecodeError: Expecting value",
        "Parse error: invalid syntax",
        "Failed to parse: malformed JSON",
    ])
    def test_error_type_parse_error(self, evaluator, error_msg):
        """测试：parse_error 错误分类"""
        error = Exception(error_msg)
        result = evaluator._classify_error(error)
        assert result == 'parse_error', f"Expected 'parse_error' for: {error_msg}"

    # ===== Database Error 错误测试 =====

    @pytest.mark.parametrize("error_msg", [
        "Database connection failed",
        "DB deadlock detected",
        "Database error: table not found",
        "DB connection timeout",
        "Database locked",
        "Failed to connect to database",
    ])
    def test_error_type_database_error(self, evaluator, error_msg):
        """测试：database_error 错误分类"""
        error = Exception(error_msg)
        result = evaluator._classify_error(error)
        assert result == 'database_error', f"Expected 'database_error' for: {error_msg}"

    # ===== LLM Error 错误测试 =====

    @pytest.mark.parametrize("error_msg", [
        "LLM API error: rate limit exceeded",
        "Model not found: gpt-xyz",
        "OpenAI API error: invalid API key",
        "LLM request failed",
        "Model inference error",
        "API error: 503 Service Unavailable",
        "LLM timeout after 30 seconds",
    ])
    def test_error_type_llm_error(self, evaluator, error_msg):
        """测试：llm_error 错误分类"""
        error = Exception(error_msg)
        result = evaluator._classify_error(error)
        assert result == 'llm_error', f"Expected 'llm_error' for: {error_msg}"

    # ===== Auth Error 错误测试 =====

    @pytest.mark.parametrize("error_msg", [
        "Permission denied for user",
        "Auth token expired",
        "Authentication failed",
        "Unauthorized access attempt",
        "Permission error: insufficient privileges",
        "Auth error: invalid credentials",
    ])
    def test_error_type_auth_error(self, evaluator, error_msg):
        """测试：auth_error 错误分类"""
        error = Exception(error_msg)
        result = evaluator._classify_error(error)
        assert result == 'auth_error', f"Expected 'auth_error' for: {error_msg}"

    # ===== Unknown Error 错误测试 =====

    @pytest.mark.parametrize("error_msg", [
        "Something unexpected happened",
        "An unknown error occurred",
        "Internal server error",
        "Oops! Something went wrong",
        "Unknown error code: 99999",
    ])
    def test_error_type_unknown_error(self, evaluator, error_msg):
        """测试：unknown_error 错误分类（无匹配关键词）"""
        error = Exception(error_msg)
        result = evaluator._classify_error(error)
        assert result == 'unknown_error', f"Expected 'unknown_error' for: {error_msg}"

    # ===== 边界情况测试 =====

    def test_classify_error_none(self, evaluator):
        """测试：传入 None 返回 None"""
        result = evaluator._classify_error(None)
        assert result is None

    def test_classify_error_empty_string(self, evaluator):
        """测试：空字符串错误消息"""
        error = Exception("")
        result = evaluator._classify_error(error)
        assert result == 'unknown_error'

    def test_classify_error_case_insensitive(self, evaluator):
        """测试：大小写不敏感"""
        error_upper = Exception("TIMEOUT ERROR")
        error_lower = Exception("timeout error")
        error_mixed = Exception("TimeOut ErRoR")

        assert evaluator._classify_error(error_upper) == 'timeout'
        assert evaluator._classify_error(error_lower) == 'timeout'
        assert evaluator._classify_error(error_mixed) == 'timeout'

    def test_classify_error_multiple_keywords(self, evaluator):
        """测试：多个关键词时按顺序匹配"""
        # parse 优先于 database
        error = Exception("json parse error in database")
        result = evaluator._classify_error(error)
        assert result == 'parse_error'

        # slot 优先于 llm
        error = Exception("slot unavailable, LLM error")
        result = evaluator._classify_error(error)
        assert result == 'slot_unavailable'

    def test_classify_error_timeout_substrings(self, evaluator):
        """测试：timeout 相关子字符串"""
        error = Exception("Request timeout after 30s")
        assert evaluator._classify_error(error) == 'timeout'

        error = Exception("Connection timed out")
        assert evaluator._classify_error(error) == 'timeout'

    def test_classify_error_slot_substrings(self, evaluator):
        """测试：slot 相关子字符串"""
        error = Exception("slot_id=123 is busy")
        assert evaluator._classify_error(error) == 'slot_unavailable'

        error = Exception("This slot is taken")
        assert evaluator._classify_error(error) == 'slot_unavailable'


class TestErrorClassificationCoverage:
    """错误分类覆盖率测试 - 确保所有错误类型都被覆盖"""

    @pytest.fixture
    def evaluator(self):
        return TaskEvaluator()

    def test_all_error_types_defined(self, evaluator):
        """测试：验证所有预期的错误类型都能被分类"""
        test_cases = [
            # (错误消息, 期望的错误类型)
            ("timeout error", "timeout"),
            ("slot taken", "slot_unavailable"),
            ("json parse error", "parse_error"),
            ("database error", "database_error"),
            ("llm error", "llm_error"),
            ("permission denied", "auth_error"),
            ("random error", "unknown_error"),
        ]

        for error_msg, expected_type in test_cases:
            error = Exception(error_msg)
            result = evaluator._classify_error(error)
            assert result == expected_type, f"Failed for: {error_msg}"

    def test_error_types_exhaustive(self, evaluator):
        """测试：穷举测试 - 确保没有遗漏的错误类型"""
        # 所有定义的错误类型
        expected_types = {
            'timeout',
            'slot_unavailable',
            'parse_error',
            'database_error',
            'llm_error',
            'auth_error',
            'unknown_error'
        }

        # 测试每种类型至少有一个匹配
        test_messages = [
            ("timeout test", "timeout"),
            ("slot test", "slot_unavailable"),
            ("parse test", "parse_error"),
            ("database test", "database_error"),
            ("llm test", "llm_error"),
            ("permission test", "auth_error"),
            ("xyz123 test", "unknown_error"),
        ]

        found_types = set()
        for msg, expected in test_messages:
            error = Exception(msg)
            result = evaluator._classify_error(error)
            found_types.add(result)

        assert found_types == expected_types, f"Missing types: {expected_types - found_types}"


class TestErrorClassificationIntegration:
    """错误分类集成测试 - 与评估器集成"""

    @pytest.fixture
    def evaluator(self):
        return TaskEvaluator()

    def test_appointment_evaluation_with_slot_error(self, evaluator):
        """测试：预约评估 - slot_unavailable"""
        error = SlotTakenException(1, "15:00", "16:00")

        result = evaluator.evaluate_appointment_task(
            session_id="test_slot_error",
            appointment_history={},
            turns_count=3,
            error=error
        )

        assert result['error_type'] == 'slot_unavailable'
        assert result['success'] == 0

    def test_appointment_evaluation_with_timeout_error(self, evaluator):
        """测试：预约评估 - timeout"""
        error = TimeoutError("LLM request timed out")

        result = evaluator.evaluate_appointment_task(
            session_id="test_timeout_error",
            appointment_history={},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'timeout'
        assert result['success'] == 0

    def test_consultation_evaluation_with_llm_error(self, evaluator):
        """测试：咨询评估 - llm_error"""
        error = Exception("LLM API rate limit exceeded")

        result = evaluator.evaluate_consultation_task(
            session_id="test_llm_error",
            consultation_data={'has_answer': False, 'answer_quality': 0.0, 'knowledge_hit': False},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'llm_error'
        assert result['success'] == 0

    def test_classification_evaluation_with_parse_error(self, evaluator):
        """测试：分类评估 - parse_error"""
        error = ValueError("JSON parse error in classification result")

        result = evaluator.evaluate_classification_task(
            session_id="test_parse_error",
            classification_data={'correctly_classified': False},
            turns_count=1,
            error=error
        )

        assert result['error_type'] == 'parse_error'
        assert result['success'] == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
