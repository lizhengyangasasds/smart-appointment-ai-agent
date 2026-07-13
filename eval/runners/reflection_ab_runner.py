"""L3 反思闭环 A/B 评测 Runner。

设计目的：
  验证 ReflectionAwareMixin 真的把反思洞察注入到主 Agent 的行为里 —— 不是说架构里有，
  而是证明"启用反思"比"不启用反思"在可量化指标上有差异。

A/B 变量：
  variant=A (treatment)：AppointmentAgent(reflection_engine=engine)
    - get_insights() 拉真实 reflection_logs / strategy_versions
    - apply_insights() 把 recommendations / bad_cases 注入 prompt
  variant=B (control)：AppointmentAgent(reflection_engine=None)
    - get_insights() 返回 _get_default_insights()
    - apply_insights() 是 no-op（基类抽象方法被 AppointmentAgent 实现为 no-op 或空）

评测指标：
  success_rate_a, success_rate_b           —— 主要结论
  avg_turns_a, avg_turns_b                 —— 反思是否减少轮次
  avg_latency_a, avg_latency_b             —— 反思是否带来额外延迟
  composite_score_a, composite_score_b     —— 综合分（同 test_effectiveness 加权）
  delta_success_rate (a - b)               —— 启用反思的提升
  delta_turns_reduction (b - a)            —— 启用反思的轮次减少

反思引擎可观测性（决定 bad_cases 提取率 0% 的根因）：
  reflection_logs_count_a                  —— variant A 跑完多了几条反思
  reflection_logs_count_b                  —— variant B 跑完多了几条（应该 0）
  bad_cases_non_empty_rate_a               —— A 里 bad_cases 非空行的占比

跑法：
  python -m eval.runners.reflection_ab_runner
  python -m eval.runners.reflection_ab_runner --limit 1 --cases ab_smoke
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func

from db.local_db import get_db_session
from db.models import ReflectionLog, TaskEvaluation

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("agents.appointment_agent").setLevel(logging.WARNING)
logging.getLogger("agents.appointment").setLevel(logging.WARNING)


# =========================================================================
# Cases
# =========================================================================
# 默认 7 个核心 case —— 覆盖 happy / 边界时段 / 偏好 / 4 个失败信号源。
# 反思引擎的 win 在"主链路失败"上 —— 必须保留足够失败 case 让反思有信号可用。
# 若只用 happy case，反思不会"帮倒忙"也没机会"帮得上忙"，A/B Δ 会恒为 0。
# 失败 case 类型分布：
#   - slot_unavailable : 时段冲突 / 边界时段
#   - parse_error      : 语义模糊 / 主体错位
#   - low_completion   : 油压不在知识库 / 输入残缺
#   - user_cancelled   : 单轮发起+撤回
#   - llm_error        : 输入基本不合规
DEFAULT_CASES: List[Dict[str, Any]] = [
    {
        "id": "ab_happy",
        "scenario": "标准预约：女/肩颈/60min/明天14:00",
        "input": "我是女生，想约明天下午2点做60分钟肩颈按摩",
        "expected": {"success": 2},
    },
    {
        "id": "ab_conflict",
        "scenario": "冲突时段：男/足疗/45min/今晚21:30（边界时间）",
        "input": "我是男的，给我约今晚9点半的足疗，45分钟",
        "expected": {"success": 1},  # 部分成功即可（边界时段不强求完成）
    },
    {
        "id": "ab_preference",
        "scenario": "用户偏好：油压/90min（user_behavior 已有偏好）",
        "input": "帮我约油压90分钟",
        "expected": {"success": 2},
    },
    # ========== 失败 case：让反思引擎有真实信号可用 ==========
    # 这 4 个 case 故意走"会触发业务失败信号"的路径：
    # 反思引擎把这些失败写进 reflection_logs 的 bad_cases / recommendations，
    # 之后 A 组 apply_insights() 注入 prompt，让 A 组在这些 case 上避免重蹈覆辙。
    # 若只用 happy case，反思不会"帮倒忙"也没机会"帮得上忙"，A/B Δ 会恒为 0。
    {
        "id": "ab_slot_conflict",
        "scenario": "时段冲突：同时段重复预约（上一个技师已占）",
        "input": "今晚9点30分给我约足疗45分钟，要男技师；刚才我已经在另一通对话里约过同一个时段了",
        # expected.success=0 表示允许 A/B 都失败 —— 但我们要看的是反思是否能通过 prompt
        # 注入"先查档期再 save"的策略，让 A 组在多次重复预约上减少 0% 成功率。
        "expected": {"success": 0, "error_type_any": ["slot_unavailable", "database_error", "parse_error"]},
    },
    {
        "id": "ab_parse_failure",
        "scenario": "解析失败：语义模糊（多种合理解读 + 时间格式错位）",
        "input": "我想约呃...那个就是明天吧不对后天，对，下午大概三四点那种，肩颈",
        "expected": {"success": 0, "error_type_any": ["parse_error", "low_completion"]},
    },
    {
        "id": "ab_user_cancel",
        "scenario": "用户取消：单轮里同时发起并撤回（应触发 user_cancelled）",
        "input": "帮我约明天上午10点的肩颈按摩，60分钟。算了算了不约了",
        "expected": {"success": 0, "error_type_any": ["user_cancelled", "low_completion"]},
    },
    {
        "id": "ab_llm_parse_error",
        "scenario": "LLM 解析错误：输入基本不合规（无主语、无时间、无项目）",
        "input": "嗯",
        "expected": {"success": 0, "error_type_any": ["llm_error", "parse_error", "low_completion"]},
    },
]


# =========================================================================
# Extended Cases（用 --extended 启用）
# =========================================================================
# 18 个参数化变体 —— 覆盖：
#   - 时段扫描（9:00 / 10:00 / 12:00 / 16:00 / 19:00 / 20:30）
#   - 项目扫描（肩颈 / 足疗 / 油压 / 经络）
#   - 时长扫描（45 / 60 / 90）
#   - 性别（男 / 女）
#   - 边界失败（解析失败 / 重复预约 / 输入残缺）
# 跑全套：约 $0.5-1.0 / 次（GPT-4o），3 次重复 ≈ $2.0
EXTENDED_CASES: List[Dict[str, Any]] = [
    # ===== 参数化变体 A：时段扫描（happy 路径） =====
    {"id": "ab_p_happy_9am_f",   "scenario": "happy 女/肩颈/60min/明天9:00",   "input": "帮我约明天上午9点的肩颈按摩，我是女生，60分钟",         "expected": {"success": 2}},
    {"id": "ab_p_happy_10am_f",  "scenario": "happy 女/肩颈/45min/明天10:00",  "input": "我是女生，明天10点肩颈45分钟",                           "expected": {"success": 2}},
    {"id": "ab_p_happy_12pm_m",  "scenario": "happy 男/足疗/60min/明天12:00",  "input": "我是男的，约明天中午12点足疗60分钟",                    "expected": {"success": 2}},
    {"id": "ab_p_happy_4pm_f",   "scenario": "happy 女/肩颈/90min/明天16:00",  "input": "女生，明天16点肩颈90分钟",                              "expected": {"success": 2}},
    {"id": "ab_p_happy_7pm_m",   "scenario": "happy 男/足疗/45min/明天19:00",  "input": "我是男的，明晚7点足疗45分钟",                            "expected": {"success": 2}},

    # ===== 参数化变体 B：项目 × 时长扫描 =====
    {"id": "ab_p_jingluo_60",    "scenario": "happy 女/经络/60min/明天15:00",   "input": "我想约经络60分钟，明天下午3点，我是女生",               "expected": {"success": 2}},
    {"id": "ab_p_zuliao_90",     "scenario": "happy 男/足疗/90min/明天14:00",   "input": "足疗90分钟，明天14点，男",                              "expected": {"success": 2}},
    {"id": "ab_p_jianzhong_45",  "scenario": "happy 女/肩颈/45min/后天11:00",   "input": "后天11点肩颈45分钟",                                    "expected": {"success": 2}},

    # ===== 参数化变体 C：边界时段（容易触发 slot_unavailable） =====
    {"id": "ab_p_edge_21",       "scenario": "边界 男/足疗/45min/今晚21:00",    "input": "今晚9点足疗45分钟，男",                                 "expected": {"success": 1}},
    {"id": "ab_p_edge_22",       "scenario": "边界 男/足疗/45min/今晚22:00",    "input": "今晚22点足疗45分钟，男",                                "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "ab_p_edge_late",     "scenario": "边界 男/足疗/45min/今晚23:30",    "input": "今晚23点30足疗45分钟，男",                              "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},

    # ===== 参数化变体 D：语义模糊（解析失败族） =====
    {"id": "ab_p_fuzzy_ampm",    "scenario": "模糊 上午/下午 错位",              "input": "我想约明天下午吧不对上午，肩颈60分钟",                  "expected": {"success": 0, "error_type_any": ["parse_error", "low_completion"]}},
    {"id": "ab_p_fuzzy_no_time", "scenario": "模糊 无明确时间",                  "input": "过两天有空，想做个肩颈",                                "expected": {"success": 0, "error_type_any": ["low_completion", "parse_error"]}},
    {"id": "ab_p_fuzzy_short",   "scenario": "模糊 输入残缺",                    "input": "肩颈",                                                  "expected": {"success": 0, "error_type_any": ["low_completion", "parse_error"]}},

    # ===== 参数化变体 E：知识库覆盖（油压不在库） =====
    {"id": "ab_p_youya_60",      "scenario": "知识库外 油压/60min",              "input": "帮我约油压60分钟",                                       "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "ab_p_youya_45",      "scenario": "知识库外 油压/45min",              "input": "明天晚上油压45分钟",                                     "expected": {"success": 0, "error_type_any": ["low_completion"]}},

    # ===== 参数化变体 F：用户取消 =====
    {"id": "ab_p_cancel_1",      "scenario": "取消 60min",                       "input": "约明天上午10点的肩颈60分钟。算了不约",                   "expected": {"success": 0, "error_type_any": ["user_cancelled", "low_completion"]}},
]


@dataclass
class ABResult:
    """一个 case 跑 A 和 B 两个变体后的对比结果。"""
    case_id: str
    scenario: str
    input: str

    # A（启用反思）
    success_a: int = 0
    turns_a: int = 0
    latency_a: float = 0.0
    error_type_a: Optional[str] = None
    error_a: str = ""
    raw_response_a: str = ""

    # B（不启用反思）
    success_b: int = 0
    turns_b: int = 0
    latency_b: float = 0.0
    error_type_b: Optional[str] = None
    error_b: str = ""
    raw_response_b: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "scenario": self.scenario,
            "input": self.input,
            "success_a": self.success_a,
            "success_b": self.success_b,
            "delta_success": self.success_a - self.success_b,
            "turns_a": self.turns_a,
            "turns_b": self.turns_b,
            "delta_turns": self.turns_b - self.turns_a,  # 正数 = 反思让对话更短
            "latency_a": round(self.latency_a, 3),
            "latency_b": round(self.latency_b, 3),
            "error_type_a": self.error_type_a or "",
            "error_type_b": self.error_type_b or "",
            "error_a": (self.error_a or "")[:200],
            "error_b": (self.error_b or "")[:200],
            "raw_response_a": (self.raw_response_a or "")[:300],
            "raw_response_b": (self.raw_response_b or "")[:300],
        }


# =========================================================================
# 反射引擎接入（反射服务会做全部 init，runner 只取 engine）
# =========================================================================

def _get_reflection_engine():
    """复用 services/reflection_service.py 的工厂逻辑（与 chat_handler.py 一致）。"""
    try:
        from services.reflection_service import get_reflection_service
        svc = get_reflection_service()
        return svc.agent.engine if svc.is_available else None
    except Exception as e:  # noqa: BLE001
        logging.warning(f"反思引擎不可用，A/B 退化为 A=engine=None 全对照: {e}")
        return None


def _fetch_latest_evaluation(session_id: str) -> Optional[Dict[str, Any]]:
    with get_db_session() as s:
        row = (
            s.query(TaskEvaluation)
            .filter(TaskEvaluation.session_id == session_id)
            .order_by(desc(TaskEvaluation.created_at))
            .first()
        )
        if not row:
            return None
        return {
            "success": row.success,
            "turns_count": row.turns_count,
            "error_type": row.error_type,
            "error_message": row.error_message,
            "success_rate": row.success_rate,
        }


# =========================================================================
# A/B 主流程
# =========================================================================

async def _run_variant(case: Dict[str, Any], variant: str, reflection_engine) -> Dict[str, Any]:
    """跑一个 variant（A 或 B），返回评测结果 dict。"""
    from agents.appointment_agent import AppointmentAgent

    suffix = "_A" if variant == "A" else "_B"
    sid = f"eval-ab-{case['id']}{suffix}-{int(time.time() * 1000)}"

    agent = AppointmentAgent(
        session_id=sid,
        unrelated_callback=None,
        reflection_engine=reflection_engine if variant == "A" else None,
    )

    out_tokens: List[str] = []

    async def _call():
        async for tok in agent.run_stream(user_input=case["input"], memory_context=""):
            out_tokens.append(str(tok))
        return "".join(out_tokens)

    t0 = time.monotonic()
    try:
        out_text = await _call()
        err = ""
    except Exception as e:  # noqa: BLE001
        import traceback
        err = traceback.format_exc()
        out_text = ""
    latency = time.monotonic() - t0

    ev = _fetch_latest_evaluation(sid)
    if ev is None:
        # Agent 异常退出没落库 —— runner 直接写 fallback row，确保 reflection_engine
        # 能看到这条失败信号（触发反思洞察提取），让 A/B 对比有意义。
        from db.repositories.reflection_repository import EvaluationRepository
        repo = EvaluationRepository()
        fallback_reason = "llm_error" if err else "low_completion"
        repo.save_evaluation(
            session_id=sid,
            task_type="appointment",
            success=0,
            success_rate=0.0,
            completion_time=latency,
            turns_count=0,
            error_type=fallback_reason,
            error_message=(err or "")[:500],
        )
        ev = {
            "success": 0,
            "turns_count": 0,
            "latency_s": latency,
            "error_type": fallback_reason,
            "error_message": (err or "")[:500],
        }
    return {
        "success": ev["success"],
        "turns_count": ev["turns_count"] or 0,
        "latency_s": ev["latency_s"] if "latency_s" in ev else latency,
        "error_type": ev["error_type"],
        "error_message": ev["error_message"] or "",
        "session_id": sid,
        "raw_response": out_text,
    }


async def _run_variant_sync(case: Dict[str, Any], variant: str,
                            reflection_engine) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """同步版本，返回 (res, ev_row, err) 三元组，session_id 暴露给 caller。"""
    res = await _run_variant(case, variant, reflection_engine)
    return res, {}, res.get("error_message", "")


def _get_latest_b_session_id(case_id: str) -> Optional[str]:
    """拿最近一条 eval-ab-<case_id>_B-<ts> session_id。"""
    from db.models import TaskEvaluation
    with get_db_session() as s:
        row = (
            s.query(TaskEvaluation)
            .filter(TaskEvaluation.session_id.like(f"eval-ab-{case_id}_B-%"))
            .order_by(desc(TaskEvaluation.created_at))
            .first()
        )
        return row.session_id if row else None


async def run_ab(case: Dict[str, Any], reflection_engine) -> ABResult:
    """对单个 case 跑 A 和 B 两个变体。"""
    res_a = await _run_variant(case, "A", reflection_engine)
    res_b = await _run_variant(case, "B", reflection_engine)

    return ABResult(
        case_id=case["id"],
        scenario=case["scenario"],
        input=case["input"],
        success_a=res_a["success"],
        turns_a=res_a["turns_count"],
        latency_a=res_a["latency_s"],
        error_type_a=res_a["error_type"],
        error_a=res_a["error_message"] or "",
        raw_response_a=res_a.get("raw_response", ""),
        success_b=res_b["success"],
        turns_b=res_b["turns_count"],
        latency_b=res_b["latency_s"],
        error_type_b=res_b["error_type"],
        error_b=res_b["error_message"] or "",
        raw_response_b=res_b.get("raw_response", ""),
    )


# =========================================================================
# 汇总 + 反思链路可观测性
# =========================================================================

def _summarize(results: List[ABResult]) -> Dict[str, Any]:
    """计算 summary 指标：success_rate / avg_turns / composite_score + delta。

    区分 full_success / partial_success / fail 三档，外加按 error_type 分组的
    失败原因分布 —— 让 Δ 对失败 case 的修复敏感（happy-only 跑不出差异）。
    """
    if not results:
        return {}

    def _safe_avg(xs):
        return round(sum(xs) / max(len(xs), 1), 3)

    success_rate_a = _safe_avg([r.success_a >= 1 for r in results])  # 1/2 都算非完全失败
    success_rate_b = _safe_avg([r.success_b >= 1 for r in results])
    full_success_rate_a = _safe_avg([r.success_a == 2 for r in results])
    full_success_rate_b = _safe_avg([r.success_b == 2 for r in results])

    # 按 error_type 分桶：哪种失败在 A 组里少了，说明反思真的把 prompt 改对了
    def _error_hist(results_list, key):
        from collections import Counter
        return dict(Counter(
            (getattr(r, key) or "none") for r in results_list
        ))
    error_hist_a = _error_hist(results, "error_type_a")
    error_hist_b = _error_hist(results, "error_type_b")

    # fail 比例（success == 0 的占比）—— 反思应让 A 失败更少
    fail_rate_a = _safe_avg([r.success_a == 0 for r in results])
    fail_rate_b = _safe_avg([r.success_b == 0 for r in results])

    avg_turns_a = _safe_avg([r.turns_a for r in results])
    avg_turns_b = _safe_avg([r.turns_b for r in results])
    avg_latency_a = _safe_avg([r.latency_a for r in results])
    avg_latency_b = _safe_avg([r.latency_b for r in results])

    # composite：成功率 0.7 + 轮次越少越好 0.2 + 延迟越快越好 0.1
    def _composite(success_rate, turns, latency):
        # 归一：轮次参考 5，延迟参考 5s
        turns_norm = max(0.0, 1.0 - max(turns - 1, 0) / 5.0)
        latency_norm = max(0.0, 1.0 - latency / 10.0)
        return round(success_rate * 0.7 + turns_norm * 0.2 + latency_norm * 0.1, 3)

    composite_a = _composite(success_rate_a, avg_turns_a, avg_latency_a)
    composite_b = _composite(success_rate_b, avg_turns_b, avg_latency_b)

    return {
        "n_cases": len(results),
        "success_rate_a": success_rate_a,
        "success_rate_b": success_rate_b,
        "delta_success_rate": round(success_rate_a - success_rate_b, 3),
        "full_success_rate_a": full_success_rate_a,
        "full_success_rate_b": full_success_rate_b,
        "delta_full_success_rate": round(full_success_rate_a - full_success_rate_b, 3),
        "fail_rate_a": fail_rate_a,
        "fail_rate_b": fail_rate_b,
        "delta_fail_rate": round(fail_rate_a - fail_rate_b, 3),  # 负数 = A 失败更少（反思有用）
        "avg_turns_a": avg_turns_a,
        "avg_turns_b": avg_turns_b,
        "delta_turns_reduction": round(avg_turns_b - avg_turns_a, 3),
        "avg_latency_a": avg_latency_a,
        "avg_latency_b": avg_latency_b,
        # 延迟分位数
        "p50_latency_a": round(_percentile([r.latency_a for r in results], 50), 3),
        "p95_latency_a": round(_percentile([r.latency_a for r in results], 95), 3),
        "p99_latency_a": round(_percentile([r.latency_a for r in results], 99), 3),
        "p50_latency_b": round(_percentile([r.latency_b for r in results], 50), 3),
        "p95_latency_b": round(_percentile([r.latency_b for r in results], 95), 3),
        "p99_latency_b": round(_percentile([r.latency_b for r in results], 99), 3),
        # Token 消耗（用 raw_response 字符数估算）
        "total_tokens_a_est": sum(
            _est_tokens(r.raw_response_a) for r in results
        ),
        "total_tokens_b_est": sum(
            _est_tokens(r.raw_response_b) for r in results
        ),
        "avg_tokens_a_est": round(
            sum(_est_tokens(r.raw_response_a) for r in results) / max(len(results), 1), 1
        ),
        "avg_tokens_b_est": round(
            sum(_est_tokens(r.raw_response_b) for r in results) / max(len(results), 1), 1
        ),
        # GPT-4o 近似价格（$2.50/M prompt + $10.00/M completion）
        "estimated_cost_a_usd": round(
            sum(_est_tokens(r.raw_response_a) for r in results) * 6.25 / 1_000_000, 6
        ),
        "estimated_cost_b_usd": round(
            sum(_est_tokens(r.raw_response_b) for r in results) * 6.25 / 1_000_000, 6
        ),
        "composite_score_a": composite_a,
        "composite_score_b": composite_b,
        "delta_composite": round(composite_a - composite_b, 3),
        # 离散度：每条 case 的成功/延迟/成本的 ±std
        # A/B 各自的离散度用来判断"指标稳不稳定"
        # 如果 A 的 std 远小于 B 的 std，说明反思让行为更稳定（面试卖点）
        "success_a_std": round(_safe_std([r.success_a for r in results]), 3),
        "success_b_std": round(_safe_std([r.success_b for r in results]), 3),
        "latency_a_std": round(_safe_std([r.latency_a for r in results]), 3),
        "latency_b_std": round(_safe_std([r.latency_b for r in results]), 3),
        "error_type_hist_a": error_hist_a,
        "error_type_hist_b": error_hist_b,
    }


def _percentile(data: List[float], pct: float) -> float:
    """最近秩法（nearest-rank）分位数。"""
    if not data:
        return 0.0
    sorted_d = sorted(data)
    n = len(sorted_d)
    if n == 1:
        return sorted_d[0]
    idx = (pct / 100.0) * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_d[lo]
    frac = idx - lo
    return sorted_d[lo] * (1 - frac) + sorted_d[hi] * frac


def _safe_std(xs: List[float]) -> float:
    """总体标准差（除以 n）。样本量小时比样本标准差更稳。"""
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return math.sqrt(var)


def _est_tokens(text: str) -> int:
    """粗略估算 token 数（CJK 按 1.5，ASCII 按 0.25）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    return int(cjk * 1.5 + (len(text) - cjk) * 0.25)


def _reflection_logs_stats(before_a_ts: int, before_b_ts: int) -> Dict[str, Any]:
    """A/B 跑完后，看 reflection_logs 实际增加了多少；以及 bad_cases 非空率。"""
    with get_db_session() as s:
        rows_a = (
            s.query(ReflectionLog)
            .filter(ReflectionLog.session_id.like("eval-ab-%_A-%"))
            .order_by(desc(ReflectionLog.created_at))
            .limit(10)
            .all()
        )
        rows_b = (
            s.query(ReflectionLog)
            .filter(ReflectionLog.session_id.like("eval-ab-%_B-%"))
            .order_by(desc(ReflectionLog.created_at))
            .limit(10)
            .all()
        )

        # 全表统计最近 N 条反思日志里的 bad_cases / recommendations / patterns 提取率
        # 不限于 eval session —— 看真实数据
        recent = (
            s.query(ReflectionLog)
            .order_by(desc(ReflectionLog.created_at))
            .limit(50)
            .all()
        )
        n_recent = max(len(recent), 1)
        n_bad_cases_nonempty = sum(
            1 for r in recent
            if r.bad_cases and r.bad_cases != [] and r.bad_cases != '[]'
        )
        n_recommendations_nonempty = sum(
            1 for r in recent
            if r.recommendations and r.recommendations != [] and r.recommendations != '[]'
        )
        n_patterns_nonempty = sum(
            1 for r in recent
            if r.patterns_discovered and r.patterns_discovered != [] and r.patterns_discovered != '[]'
        )

        # 全表 strategy_versions
        n_strategy_active = (
            s.query(ReflectionLog)
            .filter(ReflectionLog.bad_cases.is_(None))
            .count()
        )

        return {
            "reflection_logs_a_count": len(rows_a),
            "reflection_logs_b_count": len(rows_b),
            "recent_bad_cases_extraction_rate": round(n_bad_cases_nonempty / n_recent, 3),
            "recent_recommendations_extraction_rate": round(n_recommendations_nonempty / n_recent, 3),
            "recent_patterns_extraction_rate": round(n_patterns_nonempty / n_recent, 3),
            "recent_window": n_recent,
        }


# =========================================================================
# 报告落盘
# =========================================================================

def _write_report(out_dir: Path, summary: Dict[str, Any], results: List[ABResult],
                  reflection_stats: Dict[str, Any], engine_info: Dict[str, Any]) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    # per-case CSV
    csv_path = out_dir / "ab_results.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        if results:
            cols = list(results[0].to_dict().keys())
            f.write(",".join(cols) + "\n")
            for r in results:
                d = r.to_dict()
                # 简单 CSV 转义：去掉换行 + 双引号包裹含逗号/引号的字段
                line = []
                for c in cols:
                    v = str(d[c])
                    if "," in v or '"' in v or "\n" in v:
                        v = '"' + v.replace('"', '""') + '"'
                    line.append(v)
                f.write(",".join(line) + "\n")

    # summary json
    summary_path = out_dir / "ab_summary.json"
    payload = {
        "summary": summary,
        "reflection_stats": reflection_stats,
        "engine_info": engine_info,
        "results": [r.to_dict() for r in results],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"csv": str(csv_path), "summary": str(summary_path)}


# =========================================================================
# CLI 入口
# =========================================================================

async def main(cases: List[Dict[str, Any]], out_dir: Path,
               reset_db: bool = False, repeat: int = 1) -> Dict[str, Any]:
    print("=" * 72)
    print("L3 反思闭环 A/B 评测")
    print("=" * 72)

    # ========== 可选：跑前清库，避免 DB 状态污染 ==========
    # 必须清：(1) task_evaluations（A/B 之前的评测结果会污染 reflection engine 信号源）
    #        (2) user_recommendations（预约产物，slot 占用）
    # 不能清：reflection_logs（这是反思引擎的产物，A/B 要读它作为 insights 来源）
    if reset_db:
        print("[init] --reset-db 已启用：清 task_evaluations + user_recommendations ...")
        try:
            from db.models import TaskEvaluation, UserRecommendation
            with get_db_session() as s:
                n_eval = s.query(TaskEvaluation).delete(synchronize_session=False)
                n_rec = s.query(UserRecommendation).delete(synchronize_session=False)
                s.commit()
            print(f"  清掉 task_evaluations={n_eval} 条, user_recommendations={n_rec} 条")
        except Exception as ex:
            print(f"  [WARN] 清库失败: {ex}")

    engine = _get_reflection_engine()
    engine_info = {
        "engine_available": engine is not None,
        "engine_class": type(engine).__name__ if engine else None,
    }
    print(f"[init] reflection_engine available: {engine_info['engine_available']}")
    if engine is None:
        print("[warn] 反思引擎不可用，A 组会退化为对照组 —— 整个 A/B 失去意义。")
        print("[warn] 检查 services/reflection_service.py / 数据库连接 / .env 配置。")

    # repeat=N 时，每条 case 实际跑 N×2 次（每个变体各 N 次）
    total_runs = len(cases) * repeat * 2
    print(f"[init] {len(cases)} cases × {repeat} repeats × 2 variants = {total_runs} runs\n")

    # ========== Phase 1: 离线填充 reflection_logs ==========
    # A/B 测试的本质是比较"有反思洞察注入" vs "无洞察注入"。
    # 如果没有预先跑过 cases，reflection_logs 为空，get_insights() 返回空，
    # A/B 在第一轮跑不出差异（等于都是 B）。
    # Phase 1: 先跑一遍 B variant（无洞察），让 evaluator 把结果落库；
    # 然后触发 engine.analyze_recent_failures() 把洞察写入 reflection_logs。
    # Phase 2: 再跑真正的 A/B，此时 A 组能注入真实 insights。
    # 注意：Phase 1 和 Phase 2 的 cases 相同，但 B variant 在 Phase 2 里
    # 仍然用 engine=None —— 两轮 B 的区别在于 Phase 2 的 B 不会再触发
    # reflection_logs 写入（因为 engine=None 的 AppointmentAgent 不调用 engine）。
    print("Phase 1/2: 填充反思洞察（B-variant 离线评测）...")
    b_session_ids = []
    for i, case in enumerate(cases, 1):
        print(f"[P1 {i}/{len(cases)}] {case['id']} B-variant...")
        res = await _run_variant(case, "B", None)
        sid = res.get("session_id", "unknown")
        b_session_ids.append(sid)
        print(f"  success={res.get('success')} sid=...{sid[-24:]}")

    # Phase 1 后清预约产物（user_recommendations），避免与 Phase 2 冲突。
    # 注意：只清预约结果，不清 technicians / reflection_logs / task_evaluations。
    try:
        from db.models import UserRecommendation
        with get_db_session() as s:
            n_del = s.query(UserRecommendation).delete(synchronize_session=False)
            s.commit()
        print(f"\nPhase 1 后: 清理 {n_del} 条 user_recommendations（避免 Phase 2 slot 冲突）")
    except Exception as ex:
        print(f"\nPhase 1 后: user_recommendations 清理跳过（{ex}）")

    # 触发 engine 分析 Phase 1 的评测结果，写入 reflection_logs
    if engine is not None:
        print("\n触发 engine.analyze_and_record() 写入 reflection_logs...")
        try:
            result = await engine.analyze_and_record(days=3)
            print(f"  分析完成: patterns={len(result.get('patterns', []))} "
                  f"bad_cases={len(result.get('bad_cases', []))} "
                  f"recommendations={len(result.get('recommendations', []))}")
            if result.get('failed_analysis'):
                fa = result['failed_analysis']
                print(f"  failed_analysis: total_failed={fa.get('total_failed')} "
                      f"error_dist={fa.get('error_type_distribution')}")
            if result.get('pattern_analysis'):
                pa = result['pattern_analysis']
                print(f"  pattern_analysis: total_sessions={pa.get('total_sessions')} "
                      f"task_dist={pa.get('task_type_distribution')}")
        except Exception as ex:
            print(f"  [WARN] engine.analyze_and_record 失败: {ex}")
            import traceback
            traceback.print_exc()

    # Phase 1 后查 reflection_logs 状态
    with get_db_session() as s:
        from db.models import ReflectionLog
        n_ref = s.query(ReflectionLog).count()
        n_bad = sum(
            1 for r in s.query(ReflectionLog).limit(20).all()
            if r.bad_cases and r.bad_cases not in ("[]", "None")
        )
        print(f"\nPhase 1 后: reflection_logs={n_ref} 条, bad_cases 非空={n_bad} 条")

    # ========== Phase 2: 真正的 A/B 对比 ==========
    # repeat=N 时每条 case 跑 N 次（每个 variant 各 N 次），用均值作为该 case 的最终值
    print("\n" + "=" * 72)
    print(f"Phase 2/2: A/B 对比评测 (repeat={repeat})")
    print("=" * 72)
    results: List[ABResult] = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']}: {case['scenario']}")

        # repeat 1 次：直接用原 run_ab
        # repeat >1 次：每条 case 跑 N 次取均值
        if repeat <= 1:
            ab = await run_ab(case, engine)
            results.append(ab)
            print(
                f"    A: success={ab.success_a} turns={ab.turns_a} latency={ab.latency_a:.2f}s"
                f"  |  B: success={ab.success_b} turns={ab.turns_b} latency={ab.latency_b:.2f}s"
            )
        else:
            a_runs: List[Dict[str, Any]] = []
            b_runs: List[Dict[str, Any]] = []
            for r in range(repeat):
                a_run = await _run_variant(case, "A", engine)
                b_run = await _run_variant(case, "B", None)
                a_runs.append(a_run)
                b_runs.append(b_run)
                print(f"    [r={r+1}/{repeat}] A: success={a_run['success']} latency={a_run['latency_s']:.2f}s"
                      f"  |  B: success={b_run['success']} latency={b_run['latency_s']:.2f}s")

            # 聚合：success 取众数；latency / turns 取均值
            def _agg(runs: List[Dict[str, Any]]):
                succs = [r["success"] for r in runs]
                # 出现次数最多的 success 值（众数），平手取大
                from collections import Counter
                most_common_succ = Counter(succs).most_common(1)[0][0]
                return {
                    "success": most_common_succ,
                    "turns": sum(r["turns_count"] for r in runs) / len(runs),
                    "latency": sum(r["latency_s"] for r in runs) / len(runs),
                    "error_type": next((r["error_type"] for r in runs if r.get("error_type")), ""),
                    "error_message": next((r["error_message"] for r in runs if r.get("error_message")), ""),
                    "raw_response": runs[-1].get("raw_response", ""),
                }

            a_agg = _agg(a_runs)
            b_agg = _agg(b_runs)
            ab = ABResult(
                case_id=case["id"],
                scenario=case["scenario"],
                input=case["input"],
                success_a=a_agg["success"],
                turns_a=int(round(a_agg["turns"])),
                latency_a=a_agg["latency"],
                error_type_a=a_agg["error_type"],
                error_a=a_agg["error_message"],
                raw_response_a=a_agg["raw_response"],
                success_b=b_agg["success"],
                turns_b=int(round(b_agg["turns"])),
                latency_b=b_agg["latency"],
                error_type_b=b_agg["error_type"],
                error_b=b_agg["error_message"],
                raw_response_b=b_agg["raw_response"],
            )
            results.append(ab)
            print(
                f"    AGG: A: success={ab.success_a} turns={ab.turns_a} latency={ab.latency_a:.2f}s"
                f"  |  B: success={ab.success_b} turns={ab.turns_b} latency={ab.latency_b:.2f}s"
            )

    summary = _summarize(results)
    reflection_stats = _reflection_logs_stats(0, 0)

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"  n_cases                  : {summary.get('n_cases')}")
    print(f"  success_rate   (A / B)   : {summary.get('success_rate_a')} / {summary.get('success_rate_b')}")
    print(f"  full_success   (A / B)   : {summary.get('full_success_rate_a')} / {summary.get('full_success_rate_b')}")
    print(f"  avg_turns      (A / B)   : {summary.get('avg_turns_a')} / {summary.get('avg_turns_b')}")
    print(f"  avg_latency    (A / B)   : {summary.get('avg_latency_a')}s / {summary.get('avg_latency_b')}s")
    print(f"  P50 latency    (A / B)   : {summary.get('p50_latency_a')}s / {summary.get('p50_latency_b')}s")
    print(f"  P95 latency    (A / B)   : {summary.get('p95_latency_a')}s / {summary.get('p95_latency_b')}s")
    print(f"  P99 latency    (A / B)   : {summary.get('p99_latency_a')}s / {summary.get('p99_latency_b')}s")
    print(f"  composite      (A / B)   : {summary.get('composite_score_a')} / {summary.get('composite_score_b')}")
    print(f"  success std    (A / B)   : {summary.get('success_a_std')} / {summary.get('success_b_std')}  (离散度，越小越稳)")
    print(f"  latency std    (A / B)   : {summary.get('latency_a_std')}s / {summary.get('latency_b_std')}s")
    print(f"  avg_tokens     (A / B)   : {summary.get('avg_tokens_a_est')} / {summary.get('avg_tokens_b_est')}  (估算)")
    print(f"  total_cost     (A / B)   : ${summary.get('estimated_cost_a_usd')} / ${summary.get('estimated_cost_b_usd')} (GPT-4o 近似)")
    print(f"  Δ success_rate           : {summary.get('delta_success_rate'):+.3f}")
    print(f"  Δ full_success_rate      : {summary.get('delta_full_success_rate'):+.3f}")
    print(f"  Δ fail_rate              : {summary.get('delta_fail_rate'):+.3f} (负数=A 失败更少)")
    print(f"  Δ turns_reduction        : {summary.get('delta_turns_reduction'):+.3f} (正=反思让对话更短)")
    print(f"  Δ composite              : {summary.get('delta_composite'):+.3f}")

    print("\n错误类型分布：")
    print(f"  A: {summary.get('error_type_hist_a')}")
    print(f"  B: {summary.get('error_type_hist_b')}")

    print("\n反思链路可观测性（最近 50 条 reflection_logs 全表统计）：")
    print(f"  bad_cases 提取率        : {reflection_stats['recent_bad_cases_extraction_rate'] * 100:.1f}%")
    print(f"  recommendations 提取率  : {reflection_stats['recent_recommendations_extraction_rate'] * 100:.1f}%")
    print(f"  patterns 提取率         : {reflection_stats['recent_patterns_extraction_rate'] * 100:.1f}%")
    print(f"  本次跑 A 写入反思条数    : {reflection_stats['reflection_logs_a_count']}")
    print(f"  本次跑 B 写入反思条数    : {reflection_stats['reflection_logs_b_count']}")

    paths = _write_report(out_dir, summary, results, reflection_stats, engine_info)
    print(f"\n产物已写：")
    print(f"  - {paths['csv']}")
    print(f"  - {paths['summary']}")

    return {
        "summary": summary,
        "reflection_stats": reflection_stats,
        "engine_info": engine_info,
        "paths": paths,
    }


def cli():
    parser = argparse.ArgumentParser(description="L3 反思闭环 A/B 评测")
    parser.add_argument("--out", default=f"reports/l3_ab_{time.strftime('%Y%m%d-%H%M%S')}",
                        help="报告输出目录")
    parser.add_argument("--cases", nargs="*", default=None,
                        help="只跑指定 case id（如 ab_happy ab_conflict）")
    parser.add_argument("--extended", action="store_true",
                        help="启用 EXTENDED_CASES（默认 7 → 25 个 case）")
    parser.add_argument("--reset-db", dest="reset_db", action="store_true",
                        help="跑前清 task_evaluations + user_recommendations（避免 DB 状态污染）")
    parser.add_argument("--repeat", type=int, default=1,
                        help="每条 case 重复 N 次取众数（用于统计置信区间，N=1 关闭）")
    args = parser.parse_args()

    cases = EXTENDED_CASES if args.extended else DEFAULT_CASES
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            print(f"[error] 没有匹配的 case id: {args.cases}")
            print(f"[hint] 当前 pool: {[c['id'] for c in (EXTENDED_CASES if args.extended else DEFAULT_CASES)]}")
            return

    asyncio.run(main(cases, Path(args.out), reset_db=args.reset_db, repeat=args.repeat))


if __name__ == "__main__":
    cli()