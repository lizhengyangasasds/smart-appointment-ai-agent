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
# 选 3 个 appointment cases：A/B 跑两次太贵，且 L3 主要看"反思是否有用"，不在覆盖广度
# - happy：标准预约（基线 100% 通过率高，反思不会"帮倒忙"）
# - conflict：时段冲突（反思应该让预约更稳）
# - preference：用户偏好型（反思能把 user_behavior 沉淀用上）
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

    # B（不启用反思）
    success_b: int = 0
    turns_b: int = 0
    latency_b: float = 0.0
    error_type_b: Optional[str] = None
    error_b: str = ""

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
        await _call()
        err = ""
    except Exception as e:  # noqa: BLE001
        import traceback
        err = traceback.format_exc()
    latency = time.monotonic() - t0

    ev = _fetch_latest_evaluation(sid)
    if ev is None:
        # 没落 task_evaluations → 视为失败
        return {
            "success": 0,
            "turns_count": 0,
            "latency_s": latency,
            "error_type": "no_evaluation_written",
            "error_message": err[-200:] if err else "AppointmentAgent 未落 task_evaluations 行",
        }
    return {
        "success": ev["success"],
        "turns_count": ev["turns_count"] or 0,
        "latency_s": latency,
        "error_type": ev["error_type"],
        "error_message": ev["error_message"] or "",
    }


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
        success_b=res_b["success"],
        turns_b=res_b["turns_count"],
        latency_b=res_b["latency_s"],
        error_type_b=res_b["error_type"],
        error_b=res_b["error_message"] or "",
    )


# =========================================================================
# 汇总 + 反思链路可观测性
# =========================================================================

def _summarize(results: List[ABResult]) -> Dict[str, Any]:
    """计算 summary 指标：success_rate / avg_turns / composite_score + delta。"""
    if not results:
        return {}

    def _safe_avg(xs):
        return round(sum(xs) / max(len(xs), 1), 3)

    success_rate_a = _safe_avg([r.success_a >= 1 for r in results])  # 1/2 都算非完全失败
    success_rate_b = _safe_avg([r.success_b >= 1 for r in results])
    full_success_rate_a = _safe_avg([r.success_a == 2 for r in results])
    full_success_rate_b = _safe_avg([r.success_b == 2 for r in results])

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
        "avg_turns_a": avg_turns_a,
        "avg_turns_b": avg_turns_b,
        "delta_turns_reduction": round(avg_turns_b - avg_turns_a, 3),
        "avg_latency_a": avg_latency_a,
        "avg_latency_b": avg_latency_b,
        "composite_score_a": composite_a,
        "composite_score_b": composite_b,
        "delta_composite": round(composite_a - composite_b, 3),
    }


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

async def main(cases: List[Dict[str, Any]], out_dir: Path) -> Dict[str, Any]:
    print("=" * 72)
    print("L3 反思闭环 A/B 评测")
    print("=" * 72)

    engine = _get_reflection_engine()
    engine_info = {
        "engine_available": engine is not None,
        "engine_class": type(engine).__name__ if engine else None,
    }
    print(f"[init] reflection_engine available: {engine_info['engine_available']}")
    if engine is None:
        print("[warn] 反思引擎不可用，A 组会退化为对照组 —— 整个 A/B 失去意义。")
        print("[warn] 检查 services/reflection_service.py / 数据库连接 / .env 配置。")

    print(f"[init] {len(cases)} cases × 2 variants = {len(cases) * 2} runs\n")

    results: List[ABResult] = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']}: {case['scenario']}")
        ab = await run_ab(case, engine)
        results.append(ab)
        print(
            f"    A: success={ab.success_a} turns={ab.turns_a} latency={ab.latency_a:.2f}s"
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
    print(f"  composite      (A / B)   : {summary.get('composite_score_a')} / {summary.get('composite_score_b')}")
    print(f"  Δ success_rate           : {summary.get('delta_success_rate'):+.3f}")
    print(f"  Δ full_success_rate      : {summary.get('delta_full_success_rate'):+.3f}")
    print(f"  Δ turns_reduction        : {summary.get('delta_turns_reduction'):+.3f} (正=反思让对话更短)")
    print(f"  Δ composite              : {summary.get('delta_composite'):+.3f}")

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
    args = parser.parse_args()

    cases = DEFAULT_CASES
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in DEFAULT_CASES if c["id"] in wanted]
        if not cases:
            print(f"[error] 没有匹配的 case id: {args.cases}")
            print(f"[hint] 可选: {[c['id'] for c in DEFAULT_CASES]}")
            return

    asyncio.run(main(cases, Path(args.out)))


if __name__ == "__main__":
    cli()