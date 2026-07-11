"""Appointment Runner。

评测入口：AppointmentAgent.run_stream(input)，由其内部自动落 task_evaluations。
评测成功后从 DB 拿 task_evaluations 的 success / success_level / error_type。

成功条件（按 expected 的字段逐项比对）：
  - expected.success = 2 → 要求 task_evaluations.success == 2（完全成功）
  - expected.success = 1 → 要求 task_evaluations.success >= 1（部分或完全成功）
  - expected.min_gender  → 要求解析出的 gender 字段 == 该值（兜底）
  - expected.technician_name → 要求解析出的 technician_name 字段 == 该值（兜底）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .base import EvalCase, EvalResult, make_eval_session_id, run_async
from db.repositories.reflection_repository import EvaluationRepository
from db.local_db import get_db_session
from db.models import TaskEvaluation
from sqlalchemy import desc

logging.getLogger("agents.appointment_agent").setLevel(logging.WARNING)
logging.getLogger("agents.appointment").setLevel(logging.WARNING)


def _fetch_latest_evaluation(session_id: str) -> Optional[Dict[str, Any]]:
    """拿某个 session 最近一次 task_evaluations 行。"""
    with get_db_session() as session:
        row = (
            session.query(TaskEvaluation)
            .filter(TaskEvaluation.session_id == session_id)
            .order_by(desc(TaskEvaluation.created_at))
            .first()
        )
        if not row:
            return None
        return {
            "id": row.id,
            "task_type": row.task_type,
            "success": row.success,
            "success_rate": row.success_rate,
            "completion_time": row.completion_time,
            "turns_count": row.turns_count,
            "error_type": row.error_type,
            "error_message": row.error_message,
        }


async def _run_one(case: EvalCase) -> EvalResult:
    sid = make_eval_session_id("appointment", case.id)

    # 每个 case 新建一个 AppointmentAgent，session_id 隔离
    from agents.appointment_agent import AppointmentAgent

    agent = AppointmentAgent(session_id=sid)

    async def _call():
        out_tokens: List[str] = []
        async for tok in agent.run_stream(user_input=case.input, memory_context=""):
            out_tokens.append(str(tok))
        return "".join(out_tokens)

    _out, latency, err = await run_async(_call())

    # 不管成功失败，都从 DB 拿一次评测行
    evaluation = _fetch_latest_evaluation(sid)

    if err:
        return EvalResult(
            case_id=case.id,
            scenario=case.scenario,
            input=case.input,
            success=0,
            latency_s=latency,
            turns=case.turns,
            expected=case.expected,
            got={"evaluation": evaluation or {}, "exception": True},
            error=err,
        )

    if not evaluation:
        # 跑完没落库（极少见，可能是 evaluation_repo 初始化失败）
        return EvalResult(
            case_id=case.id,
            scenario=case.scenario,
            input=case.input,
            success=0,
            latency_s=latency,
            turns=case.turns,
            expected=case.expected,
            got={"evaluation": None, "note": "no task_evaluations row written"},
            error="AppointmentAgent 跑完未生成 task_evaluations 行",
        )

    # 逐项匹配 expected
    expected = case.expected or {}
    matches: Dict[str, bool] = {}

    # 1. success 等级
    if "success" in expected:
        want_success = int(expected["success"])
        matches["success"] = int(evaluation.get("success") or 0) >= want_success
    else:
        # 默认：只要 success >= 1 都算成功（至少 PARTIAL）
        matches["success"] = int(evaluation.get("success") or 0) >= 1

    # 2. gender 兜底（从 action_data 里读）
    if "min_gender" in expected:
        action_data = evaluation.get("action_data") or {}
        gender = action_data.get("gender") if isinstance(action_data, dict) else None
        matches["min_gender"] = gender == expected["min_gender"]

    # 3. technician_name 兜底
    if "technician_name" in expected:
        action_data = evaluation.get("action_data") or {}
        tech_name = (
            action_data.get("technician_name")
            if isinstance(action_data, dict)
            else None
        )
        matches["technician_name"] = tech_name == expected["technician_name"]

    # 4. unrelated 兜底
    if expected.get("unrelated"):
        matches["unrelated"] = (evaluation.get("error_type") in ("unrelated", "low_completion", "parse_error")) or (
            int(evaluation.get("success") or 0) == 1
        )

    all_match = all(matches.values()) if matches else False

    return EvalResult(
        case_id=case.id,
        scenario=case.scenario,
        input=case.input,
        success=1 if all_match else 0,
        latency_s=latency,
        turns=evaluation.get("turns_count") or case.turns,
        expected=expected,
        got={"evaluation": evaluation, "matches": matches},
    )


def run(cases: List[EvalCase]) -> List[EvalResult]:
    """顺序跑（避免并发 LLM）。"""
    return [asyncio.run(_run_one(c)) for c in cases]