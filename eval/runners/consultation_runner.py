"""Consultation Runner。

评测入口：ConsultantAgent.consult_stream(input)，
其内部会自动通过 _record_consultation_behavior 在 user_behaviors 表里写记录。

评测成功的判定：从刚写的 user_behavior 里取 knowledge_docs 命中的 category，
看是否落入 expected.top_category 的集合。

失败兜底：如果没写 user_behavior，按"返回内容是否非空 + 是否包含按摩相关词"粗略判定。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .base import EvalCase, EvalResult, make_eval_session_id, run_async
from db.local_db import get_db_session
from db.models import UserBehavior
from sqlalchemy import desc


def _fetch_latest_behavior(session_id: str) -> Optional[Dict[str, Any]]:
    """拿最近一次 consultation 类型的 user_behavior 记录。"""
    with get_db_session() as session:
        row = (
            session.query(UserBehavior)
            .filter(
                UserBehavior.session_id == session_id,
                UserBehavior.action_type == "consultation",
            )
            .order_by(desc(UserBehavior.created_at))
            .first()
        )
        if not row:
            return None
        return {
            "id": row.id,
            "action_type": row.action_type,
            "action_data": row.action_data,
            "session_id": row.session_id,
        }


async def _run_one(case: EvalCase) -> EvalResult:
    sid = make_eval_session_id("consultation", case.id)

    from agents.consultant_agent import ConsultantAgent

    agent = ConsultantAgent(session_id=sid)
    # 主动初始化 KnowledgeRetriever（KnowledgeRetriever 本身无 async ctx 接口）
    await agent.knowledge_retriever.initialize()

    out_tokens: List[str] = []

    async def _call():
        async for tok in agent.consult_stream(case.input):
            out_tokens.append(str(tok))
        return "".join(out_tokens)

    _out, latency, err = await run_async(_call())

    behavior = _fetch_latest_behavior(sid)

    if err:
        return EvalResult(
            case_id=case.id,
            scenario=case.scenario,
            input=case.input,
            success=0,
            latency_s=latency,
            turns=case.turns,
            expected=case.expected,
            got={"behavior": behavior or {}, "exception": True},
            error=err,
            raw_response="".join(out_tokens),
        )

    if not behavior:
        # 没有行为记录，按输出非空粗判
        non_empty = "".join(out_tokens).strip() != ""
        return EvalResult(
            case_id=case.id,
            scenario=case.scenario,
            input=case.input,
            success=1 if non_empty else 0,
            latency_s=latency,
            turns=case.turns,
            expected=case.expected,
            got={"behavior": None, "fallback": True, "response_non_empty": non_empty},
            error="未找到 user_behavior 行，按 fallback 判定",
            raw_response="".join(out_tokens),
        )

    action_data = behavior.get("action_data") or {}
    categories = action_data.get("categories") if isinstance(action_data, dict) else None
    expected_top = case.expected.get("top_category")

    matched = bool(
        expected_top and categories and expected_top in categories
    )

    return EvalResult(
        case_id=case.id,
        scenario=case.scenario,
        input=case.input,
        success=1 if matched else 0,
        latency_s=latency,
        turns=case.turns,
        expected={"top_category": expected_top},
        got={
            "behavior_id": behavior.get("id"),
            "categories": categories,
            "matched": matched,
        },
        raw_response="".join(out_tokens),
    )


def run(cases: List[EvalCase]) -> List[EvalResult]:
    """顺序跑。"""
    return [asyncio.run(_run_one(c)) for c in cases]