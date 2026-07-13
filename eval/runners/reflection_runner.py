"""Reflection Runner。

评测入口：纯函数式。手工构造 appointment_history / turns_count / completion_time，
调 TaskEvaluator.evaluate_appointment_task 拿到 evaluation；再调
ReflectionEngine.reflect_on_task 触发反思日志落库。

成功条件：
  - got_should_reflect == expected.should_reflect
  - 当 expected.error_type 给定时，evaluation.error_type == expected.error_type
  - 当 expected.success_level 给定时，evaluation.success_level == expected.success_level
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .base import EvalCase, EvalResult, make_eval_session_id, run_async
from db.repositories.reflection_repository import EvaluationRepository
from db.local_db import get_db_session
from db.models import ReflectionLog
from sqlalchemy import desc


def _build_error(error_type: Optional[str]):
    """根据 input_fixture 的 error_type 字符串构造对应的异常对象。"""
    if not error_type:
        return None
    from agents.reflection.evaluator import (
        AppointmentSaveFailedError,
        UserCancelledError,
        AppointmentTimeoutError,
    )

    if error_type == "slot_unavailable":
        return AppointmentSaveFailedError(
            message="slot taken", reason="slot_unavailable"
        )
    if error_type == "database_error":
        return AppointmentSaveFailedError(
            message="database error", reason="database_error"
        )
    if error_type == "user_cancelled":
        return UserCancelledError()
    if error_type == "timeout":
        return AppointmentTimeoutError()
    if error_type == "llm_error":
        return RuntimeError("llm api rate limit exceeded")
    if error_type == "parse_error":
        return ValueError("parse json failed")
    # 兜底
    return RuntimeError(error_type)


def _fetch_latest_reflection(evaluation_id: int) -> Optional[Dict[str, Any]]:
    """拿最新一条与 evaluation_id 关联的反思日志。"""
    with get_db_session() as session:
        row = (
            session.query(ReflectionLog)
            .filter(ReflectionLog.evaluation_id == evaluation_id)
            .order_by(desc(ReflectionLog.created_at))
            .first()
        )
        if not row:
            return None
        return {
            "id": row.id,
            "reflection_type": row.reflection_type,
            "findings": row.findings,
            "recommendations": row.recommendations,
            "patterns_discovered": row.patterns_discovered,
            "bad_cases": row.bad_cases,
        }


async def _run_one(case: EvalCase) -> EvalResult:
    sid = make_eval_session_id("reflection", case.id)
    # reflection_cases.json 里 input_fixture 在 EvalCase 加载时被合并进 expected["_fixture"]
    fixture: Dict[str, Any] = case.expected.get("_fixture") or {}
    expected: Dict[str, Any] = {
        k: v for k, v in case.expected.items() if not k.startswith("_")
    }

    appointment_history = fixture.get("appointment_history") or {}
    turns_count = int(fixture.get("turns_count") or 0)
    completion_time = fixture.get("completion_time")
    err_obj = _build_error(fixture.get("error_type"))

    from agents.reflection_agent import ReflectionAgent

    reflection = ReflectionAgent()

    async def _call():
        return await reflection.reflect_on_appointment(
            session_id=sid,
            appointment_history=appointment_history,
            turns_count=turns_count,
            completion_time=completion_time,
            error=err_obj,
        )

    result, latency, err = await run_async(_call())

    if err:
        return EvalResult(
            case_id=case.id,
            scenario=case.scenario,
            input=str(fixture),
            success=0,
            latency_s=latency,
            turns=case.turns,
            expected=expected,
            got={},
            error=err,
            expected_should_reflect=bool(expected.get("should_reflect")),
            got_should_reflect=False,
            reflection_log_written=False,
            bad_cases_non_empty=False,
            raw_response=result or "",
        )

    evaluation = (result or {}).get("evaluation") or {}
    reflection_result = (result or {}).get("reflection") or {}

    got_should_reflect = bool(evaluation.get("should_reflect"))

    # 反思日志是否落库（reflection_result.reflection_id 非空即可）
    reflection_log = None
    reflection_log_written = False
    bad_cases_non_empty = False
    rid = reflection_result.get("reflection_id")
    if rid:
        reflection_log_written = True
        reflection_log = _fetch_latest_reflection(rid)
        if reflection_log and reflection_log.get("bad_cases"):
            bad_cases_non_empty = True

    # 匹配判定
    matches: Dict[str, bool] = {}
    matches["should_reflect"] = got_should_reflect == bool(
        expected.get("should_reflect")
    )
    if "success_level" in expected:
        matches["success_level"] = (
            str(evaluation.get("success_level", ""))
            == str(expected["success_level"])
        )
    if "error_type" in expected:
        matches["error_type"] = (
            str(evaluation.get("error_type", "")) == str(expected["error_type"])
        )

    is_match = all(matches.values()) if matches else False

    return EvalResult(
        case_id=case.id,
        scenario=case.scenario,
        input=str(fixture),
        success=1 if is_match else 0,
        latency_s=latency,
        turns=case.turns,
        expected=expected,
        got={
            "evaluation": evaluation,
            "reflection_id": rid,
            "matches": matches,
        },
        expected_should_reflect=bool(expected.get("should_reflect")),
        got_should_reflect=got_should_reflect,
        reflection_log_written=reflection_log_written,
        bad_cases_non_empty=bad_cases_non_empty,
        raw_response=str(result),
    )


def run(cases: List[EvalCase]) -> List[EvalResult]:
    """顺序跑。反思链路里 LLM 调用较多，避免并发。"""
    return [asyncio.run(_run_one(c)) for c in cases]