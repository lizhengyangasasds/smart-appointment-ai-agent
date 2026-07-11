"""Classifier Runner。

评测入口：直接调 TaskClassifier.classify_task（拿到原始 category），
而非 TaskClassificationAgent.classify_task_stream（后者会路由到下游 Agent）。

成功条件：got_category == expected.task_type
"""

from __future__ import annotations

import asyncio
from typing import List

from .base import EvalCase, EvalResult, make_eval_session_id, run_async
from agents.task_classification.task_classifier import TaskClassifier


async def _run_one(case: EvalCase) -> EvalResult:
    sid = make_eval_session_id("classifier", case.id)
    llm = None
    try:
        from config.model_provider import create_chat_model

        llm = create_chat_model(temperature=0)
    except Exception as e:
        return EvalResult(
            case_id=case.id,
            scenario=case.scenario,
            input=case.input,
            success=0,
            latency_s=0.0,
            turns=case.turns,
            expected=case.expected,
            got={},
            error=f"LLM 初始化失败: {e}",
        )

    classifier = TaskClassifier(llm)

    async def _call():
        return await classifier.classify_task(case.input)

    got_value, latency, err = await run_async(_call())

    if err:
        return EvalResult(
            case_id=case.id,
            scenario=case.scenario,
            input=case.input,
            success=0,
            latency_s=latency,
            turns=case.turns,
            expected=case.expected,
            got={},
            error=err,
        )

    got_category = str(got_value or "").strip().lower()
    expected_category = str(case.expected.get("task_type", "")).strip().lower()
    is_match = got_category == expected_category

    return EvalResult(
        case_id=case.id,
        scenario=case.scenario,
        input=case.input,
        success=1 if is_match else 0,
        latency_s=latency,
        turns=case.turns,
        expected={"task_type": expected_category},
        got={"task_type": got_category},
    )


def run(cases: List[EvalCase]) -> List[EvalResult]:
    """同步入口：顺序执行所有 case（避免并发把 LLM provider 打满）。"""
    return [asyncio.run(_run_one(c)) for c in cases]