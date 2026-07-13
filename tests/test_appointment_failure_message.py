"""
回归测试：避免「描述性偏好被当作技师名」的语义矛盾回复

场景：用户说"我要预约按摩服务，3 个小时，下午三点，女技师"
LLM 可能把"按摩服务"误填到 technician_name 字段。下游 create_appointment_failure_message
如果不做二次校验，就会向用户输出"抱歉，没有找到名为'按摩服务'的技师"这种
自相矛盾的回复。

修复：
1. message_builder.create_appointment_failure_message 加 _looks_like_real_name 校验
2. appointment_processor 找不到技师档期时，调用前再校验一次技师名并清掉脏数据
"""
import pytest
from agents.appointment.message_builder import MessageBuilder


class TestAppointmentFailureMessageGuard:
    """create_appointment_failure_message 应该拒绝把描述性偏好当技师名"""

    @pytest.fixture
    def mb(self):
        return MessageBuilder()

    def test_massage_service_should_not_trigger_not_found(self, mb):
        """'按摩服务' 是描述性偏好，不应触发'未找到名为'分支"""
        reply = mb.create_appointment_failure_message("按摩服务")
        assert "按摩服务" not in reply
        assert "没有找到名为" not in reply
        # 应走通用"没有合适的技师空闲"
        assert "没有合适的技师" in reply

    def test_unknown_should_go_generic(self, mb):
        reply = mb.create_appointment_failure_message("未知")
        assert "没有找到名为" not in reply
        assert "没有合适的技师" in reply

    def test_none_should_go_generic(self, mb):
        reply = mb.create_appointment_failure_message(None)
        assert "没有找到名为" not in reply
        assert "没有合适的技师" in reply

    def test_empty_should_go_generic(self, mb):
        reply = mb.create_appointment_failure_message("")
        assert "没有找到名为" not in reply

    def test_real_known_name_should_pass_through_to_db_branch(self, mb):
        """真实姓名（哪怕 DB 没有）应继续走 DB 查询分支，不被守卫误判

        守卫只防描述性偏好，不防真实姓名。即使用户拼了一个不存在的真名，
        我们也应该说"没找到名为X的技师"（合理），而不是"没合适技师空闲"。
        """
        # 4 字姓名可能是合法复合姓
        reply = mb.create_appointment_failure_message("欧阳娜娜")
        assert "欧阳娜娜" in reply
        # 不应走通用分支
        assert "该时间段没有合适的技师空闲" not in reply

    def test_descriptive_phrases_should_go_generic(self, mb):
        """各类描述性短语都必须走通用分支"""
        for desc in ["手劲大的", "手法好的", "经验丰富的", "推拿", "足疗", "足疗服务"]:
            reply = mb.create_appointment_failure_message(desc)
            assert "没有找到名为" not in reply, f"'{desc}' 不应触发'未找到名为'：{reply}"
            assert "没有合适的技师" in reply, f"'{desc}' 应走通用分支：{reply}"

    def test_real_name_helper_keeps_consistency_with_input_parser(self, mb):
        """MessageBuilder 内部的 _looks_like_real_name 与 InputParser 行为一致

        两边规则保持同步，避免一个判真名一个判描述带来的不一致。
        """
        assert mb._looks_like_real_name("王芳") is True
        assert mb._looks_like_real_name("张三") is True
        assert mb._looks_like_real_name("欧阳娜娜") is True
        assert mb._looks_like_real_name("按摩服务") is False
        assert mb._looks_like_real_name("手劲大的") is False
        assert mb._looks_like_real_name("") is False
        assert mb._looks_like_real_name(None) is False
        # 4 字复合姓允许
        assert mb._looks_like_real_name("张三李四") is True
