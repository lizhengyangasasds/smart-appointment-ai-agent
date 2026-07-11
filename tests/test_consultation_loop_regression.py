"""
回归测试：'李娜今天有空吗' 类咨询问题不应触发无限循环

bug 描述：
当用户输入像"李娜今天有空吗"这种既是咨询问询又有预约意向的模糊输入时：
1. LLM 可能把它分类为 'appointment'（听到人名 + 时间词）
2. AppointmentAgent input_parser 又把它识别为 unrelated=true（实际是问询不是预约指令）
3. 历史上 appointment_processor.handle_unrelated_request 调用
   task_classification_agent.handle_unrelated 后者又调 process_task_stream
   重新分类，形成 分类 → appointment → unrelated → 再次分类 → ... 的死循环。
4. 同样地，consultation 路径里 handle_unrelated_async 也会触发同样的循环。

修复：
- task_classification_agent.handle_unrelated / handle_unrelated_async 改为直接给礼貌拒绝，
  不再 process_task_stream。
- appointment_processor.handle_unrelated_request 修复 sync/await bug
  (callback 是 async def，必须 await 而不是 yield coroutine 对象)。

本测试：
- stub 掉 TaskClassifier 让它返回 'appointment' / 'query'
- stub 掉 InputParser.parse_stream 让它返回 unrelated=true
- stub 掉 ConsultationClassifier.is_consultation_related 让它返回 False
- 计数 classify/parse/handle_unrelated 的调用次数，确保都不超过 1。
"""

import asyncio
import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("MODEL_PROVIDER", "openai-compatible")
os.environ.setdefault("LLM_API_KEY", "mock")
os.environ.setdefault("LLM_BASE_URL", "http://mock")
os.environ.setdefault("LLM_MODEL", "mock")


@pytest.fixture(autouse=True)
def mock_llm_provider(monkeypatch):
    """用 mock 替代真实 LLM provider。"""
    import config.model_provider as mp
    mock_llm = MagicMock()
    monkeypatch.setattr(mp, "create_chat_model", lambda temperature=0: mock_llm)
    return mock_llm


@pytest.mark.asyncio
async def test_appointment_unrelated_does_not_loop(mock_llm_provider, monkeypatch):
    """复现 路径1：classify → 'appointment' → parse → unrelated=true → handle_unrelated
    关键断言：classify_task 和 handle_unrelated 都只调用 1 次（不循环）。"""
    from agents.task_classification import task_classifier as tc_module
    from agents.appointment.input_parser import InputParser
    from agents.task_classification_agent import TaskClassificationAgent as TCA

    counters = {"classify": 0, "parse": 0, "handle_unrelated": 0}

    async def fake_classify(self, task, memory_context=""):
        counters["classify"] += 1
        return "appointment"
    monkeypatch.setattr(tc_module.TaskClassifier, "classify_task", fake_classify)

    def fake_parse_stream(self, user_input, chat_history, memory_context=""):
        counters["parse"] += 1
        def _gen():
            yield (
                '{"unrelated": true, "info_complete": false, '
                '"missing_info": ["所有信息"], "technician_name": "未知", '
                '"project": "未知", "duration": "未知", "start_time": "未知", '
                '"gender": "未知", "preference": "未知", "confirmation": "未知"}'
            )
        return _gen()
    monkeypatch.setattr(InputParser, "parse_stream", fake_parse_stream)

    original_handle_unrelated = TCA.handle_unrelated

    async def counted(self, user_input, memory_context=""):
        counters["handle_unrelated"] += 1
        return await original_handle_unrelated(self, user_input, memory_context)
    monkeypatch.setattr(TCA, "handle_unrelated", counted)

    from agents.task_classification_agent import TaskClassificationAgent
    from agents.appointment_agent import AppointmentAgent
    from agents.consultant_agent import ConsultantAgent

    appointment = AppointmentAgent()
    consultant = ConsultantAgent()
    classifier = TaskClassificationAgent(appointment, consultant)

    token_count = 0
    async for tok in classifier.classify_task_stream("李娜今天有空吗"):
        token_count += 1
        if token_count > 30:
            break

    assert counters["classify"] == 1, \
        f"classify_task 被调用 {counters['classify']} 次（应=1，可能循环）"
    assert counters["parse"] == 1, \
        f"parse_stream 被调用 {counters['parse']} 次（应=1，可能循环）"
    assert counters["handle_unrelated"] <= 1, \
        f"handle_unrelated 被调用 {counters['handle_unrelated']} 次（应≤1，可能循环）"
    assert 0 < token_count <= 10, \
        f"tokens={token_count} 异常（预期 ≤10 条礼貌拒绝语）"


@pytest.mark.asyncio
async def test_consultation_unrelated_does_not_loop(mock_llm_provider, monkeypatch):
    """复现 路径2：classify → 'query' → consult_stream → is_consultation_related=False
    → handle_unrelated_async。关键断言：classify、is_consultation_related、
    handle_unrelated_async 都只调用 1 次（不循环）。"""
    from agents.task_classification import task_classifier as tc_module
    from agents.consultant import consultation_classifier as cc_module
    from agents.task_classification_agent import TaskClassificationAgent as TCA

    counters = {
        "classify": 0,
        "consult_classify": 0,
        "handle_unrelated_async": 0,
    }

    async def fake_classify(self, task, memory_context=""):
        counters["classify"] += 1
        return "query"
    monkeypatch.setattr(tc_module.TaskClassifier, "classify_task", fake_classify)

    async def fake_is_consultation_related(self, user_input):
        counters["consult_classify"] += 1
        return False
    monkeypatch.setattr(
        cc_module.ConsultationClassifier, "is_consultation_related",
        fake_is_consultation_related,
    )

    original_handle_unrelated_async = TCA.handle_unrelated_async

    async def counted(self, user_input, memory_context=""):
        counters["handle_unrelated_async"] += 1
        async for tok in original_handle_unrelated_async(self, user_input, memory_context):
            yield tok
    monkeypatch.setattr(TCA, "handle_unrelated_async", counted)

    from agents.task_classification_agent import TaskClassificationAgent
    from agents.appointment_agent import AppointmentAgent
    from agents.consultant_agent import ConsultantAgent

    appointment = AppointmentAgent()
    consultant = ConsultantAgent()
    classifier = TaskClassificationAgent(appointment, consultant)

    token_count = 0
    async for tok in classifier.classify_task_stream("李娜今天有空吗"):
        token_count += 1
        if token_count > 80:
            break

    assert counters["classify"] == 1, \
        f"classify_task 被调用 {counters['classify']} 次（应=1，可能循环）"
    assert counters["consult_classify"] == 1, \
        f"is_consultation_related 被调用 {counters['consult_classify']} 次（应=1，可能循环）"
    assert counters["handle_unrelated_async"] <= 1, \
        f"handle_unrelated_async 被调用 {counters['handle_unrelated_async']} 次（应≤1，可能循环）"
    # 50+ tokens 是因为 consultation_processor.handle_unrelated_request 把拒绝语按字符 yield
    assert 0 < token_count <= 70, \
        f"tokens={token_count} 异常（预期 ≤70：拒绝语按字符 yield）"