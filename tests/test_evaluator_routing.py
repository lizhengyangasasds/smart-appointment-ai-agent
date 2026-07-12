"""
评估通道单元测试

验证 4 类失败信号能正确写入 task_evaluations：
  1. save_appointment 返回 False → FAILED slot_unavailable
  2. 用户拒绝推荐 → FAILED user_cancelled
  3. parse 异常 → FAILED parse_error
  4. 正常完成预约 → SUCCESS / PARTIAL

只做单元验证，不依赖真实 LLM。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reflection.evaluator import (
    TaskEvaluator,
    AppointmentSaveFailedError,
    UserCancelledError,
    AppointmentTimeoutError,
    SuccessLevel,
)


# ======================================================================
# 语义异常 → error_type 映射
# ======================================================================

class TestBusinessErrorClassification:
    def test_save_failed_slot_unavailable(self):
        """AppointmentSaveFailedError(reason='slot_unavailable') → slot_unavailable"""
        ev = TaskEvaluator()
        err = AppointmentSaveFailedError(reason='slot_unavailable')
        result = ev._classify_business_error(err)
        assert result == 'slot_unavailable'

    def test_save_failed_database_error(self):
        """AppointmentSaveFailedError() 默认 → database_error"""
        ev = TaskEvaluator()
        err = AppointmentSaveFailedError()
        result = ev._classify_business_error(err)
        assert result == 'database_error'

    def test_user_cancelled(self):
        """UserCancelledError → user_cancelled"""
        ev = TaskEvaluator()
        err = UserCancelledError()
        result = ev._classify_business_error(err)
        assert result == 'user_cancelled'

    def test_timeout(self):
        """AppointmentTimeoutError → timeout"""
        ev = TaskEvaluator()
        err = AppointmentTimeoutError()
        result = ev._classify_business_error(err)
        assert result == 'timeout'

    def test_unknown_error_falls_back_to_generic(self):
        """非业务异常返回 None，fallback 到 _classify_error"""
        ev = TaskEvaluator()
        result = ev._classify_business_error(ValueError('slot conflict'))
        assert result is None
        # 确认 fallback 到 _classify_error 能识别 slot
        generic = ev._classify_error(ValueError('slot conflict'))
        assert generic == 'slot_unavailable'

    def test_plain_exception_not_matched(self):
        """普通 Exception 没有 reason 属性 → 返回 None"""
        ev = TaskEvaluator()
        assert ev._classify_business_error(RuntimeError('boom')) is None


# ======================================================================
# evaluate_appointment_task 的 error 参数路由
# ======================================================================

class TestEvaluateAppointmentTaskRouting:
    """验证 evaluate_appointment_task 对 3 类错误的路由结果"""

    def _hist(self, gender='女', start='2026-07-12 10:00', duration='60', project='按摩'):
        return dict(gender=gender, start_time=start, duration=duration, project=project)

    def test_slot_unavailable_maps_to_failed(self):
        ev = TaskEvaluator()
        result = ev.evaluate_appointment_task(
            session_id='s1',
            appointment_history=self._hist(),
            turns_count=3,
            completion_time=5.0,
            error=AppointmentSaveFailedError(reason='slot_unavailable'),
        )
        assert result['success'] == SuccessLevel.FAILED
        assert result['success_level'] == 'FAILED'
        assert result['error_type'] == 'slot_unavailable'
        assert result['success_rate'] == 0.0

    def test_user_cancelled_maps_to_failed(self):
        ev = TaskEvaluator()
        result = ev.evaluate_appointment_task(
            session_id='s2',
            appointment_history=self._hist(),
            turns_count=2,
            completion_time=3.0,
            error=UserCancelledError(),
        )
        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'user_cancelled'

    def test_llm_error_maps_to_failed(self):
        ev = TaskEvaluator()
        result = ev.evaluate_appointment_task(
            session_id='s3',
            appointment_history=self._hist(),
            turns_count=1,
            completion_time=2.0,
            error=RuntimeError('llm api error'),
        )
        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'llm_error'

    def test_full_history_no_error_is_success(self):
        ev = TaskEvaluator()
        result = ev.evaluate_appointment_task(
            session_id='s4',
            appointment_history=self._hist(),
            turns_count=2,
            completion_time=4.0,
            error=None,
        )
        assert result['success'] == SuccessLevel.SUCCESS
        assert result['error_type'] is None
        assert result['success_rate'] == 1.0

    def test_partial_history_no_error_is_partial(self):
        ev = TaskEvaluator()
        # 注意：evaluator 用 appointment_history.get(f) 做 truthy 判断，
        # "未知"是非空字符串 → truthy，所以只放 None 才算"未完成"
        # 以下用 None 确保字段缺失：gender=女（完成） + start_time=None + duration=None + project=按摩
        # → completion_rate = 2/4 = 0.5 (边界 PARTIAL)
        result = ev.evaluate_appointment_task(
            session_id='s5',
            appointment_history=dict(gender='女', start_time=None, duration=None, project='按摩'),
            turns_count=4,
            completion_time=8.0,
            error=None,
        )
        assert result['success'] == SuccessLevel.PARTIAL
        assert result['error_type'] == 'incomplete_info'

    def test_completion_time_triggers_reflection(self):
        ev = TaskEvaluator()
        result = ev.evaluate_appointment_task(
            session_id='s6',
            appointment_history=self._hist(),
            turns_count=3,
            completion_time=200.0,  # 超过 DEFAULT_THRESHOLDS 120s
            error=None,
        )
        assert result['should_reflect'] is True

    def test_high_turns_triggers_reflection(self):
        ev = TaskEvaluator()
        result = ev.evaluate_appointment_task(
            session_id='s7',
            appointment_history=self._hist(),
            turns_count=15,  # 超过 10 轮
            completion_time=10.0,
            error=None,
        )
        assert result['should_reflect'] is True


# ======================================================================
# evaluate_consultation_task / evaluate_classification_task 路由
# ======================================================================

class TestOtherTaskTypes:
    def test_consultation_with_save_failed_error(self):
        ev = TaskEvaluator()
        result = ev.evaluate_consultation_task(
            session_id='s8',
            consultation_data={'has_answer': True, 'answer_quality': 0.9},
            turns_count=2,
            error=AppointmentSaveFailedError(),
        )
        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'database_error'

    def test_classification_error(self):
        ev = TaskEvaluator()
        result = ev.evaluate_classification_task(
            session_id='s9',
            classification_data={'correctly_classified': True},
            turns_count=1,
            error=RuntimeError('timeout during classification'),
        )
        assert result['success'] == SuccessLevel.FAILED
        assert result['error_type'] == 'timeout'