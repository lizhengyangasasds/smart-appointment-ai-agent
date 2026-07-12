"""评测指标计算：3 个基础指标 + composite + 反思子指标。"""

from __future__ import annotations

import statistics
from typing import Any, Dict, Iterable, List, Sequence


# ---------------------------------------------------------------------------
# 基础指标
# ---------------------------------------------------------------------------

def success_rate(results: Sequence[Any]) -> float:
    """case 级别成功率（result.success 是 0/1 的 int 或 bool）。"""
    if not results:
        return 0.0
    return sum(1 for r in results if int(getattr(r, "success", 0)) == 1) / len(results)


def avg_latency(results: Sequence[Any]) -> float:
    """平均延迟（秒）。失败也计入（失败延迟本身就是观测值）。"""
    latencies = [float(getattr(r, "latency_s", 0.0) or 0.0) for r in results]
    return statistics.mean(latencies) if latencies else 0.0


def avg_turns(results: Sequence[Any]) -> float:
    """平均轮数。"""
    turns = [int(getattr(r, "turns", 0) or 0) for r in results]
    return statistics.mean(turns) if turns else 0.0


# ---------------------------------------------------------------------------
# 归一化（min-max → [0,1]）
# ---------------------------------------------------------------------------

def min_max_normalize(values: Sequence[float]) -> List[float]:
    """把一组非负数映射到 [0,1]。全相等时返回 0.5（避免除零）。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


# ---------------------------------------------------------------------------
# 综合分（沿用项目已有公式方向：success 主导，latency/turns 次之）
# ---------------------------------------------------------------------------

def composite_score(
    success_rate_val: float,
    latency_values: Sequence[float],
    turns_values: Sequence[float],
) -> float:
    """综合分 = 0.4*success + 0.3*(1-latency_norm) + 0.3*(1-turns_norm)。

    输入是原始 latency/turns 数组，先做 min-max 归一化再加权。
    """
    latency_norm = min_max_normalize(latency_values)
    turns_norm = min_max_normalize(turns_values)

    # 当样本只有 1 条时，min_max 会返回 0.5；此时 latency/turns 不贡献差异，
    # 综合分退化为 success_rate（这是预期行为，避免人为放大单点延迟）
    avg_lat_norm = statistics.mean(latency_norm) if latency_norm else 0.5
    avg_turns_norm = statistics.mean(turns_norm) if turns_norm else 0.5

    score = (
        0.4 * success_rate_val
        + 0.3 * (1.0 - avg_lat_norm)
        + 0.3 * (1.0 - avg_turns_norm)
    )
    # 限制在 [0,1]
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# 反思子指标
# ---------------------------------------------------------------------------

def trigger_precision(results: Sequence[Any]) -> float:
    """should_reflect=True 的 case 中，确实写了 reflection_logs 的占比。"""
    triggered = [r for r in results if bool(getattr(r, "got_should_reflect", False))]
    if not triggered:
        return 0.0
    written = sum(1 for r in triggered if bool(getattr(r, "reflection_log_written", False)))
    return written / len(triggered)


def trigger_recall(results: Sequence[Any]) -> float:
    """期望反思 (expected_should_reflect=True) 的 case 中，实际触发的占比。"""
    expected = [r for r in results if bool(getattr(r, "expected_should_reflect", False))]
    if not expected:
        return 0.0
    actual = sum(1 for r in expected if bool(getattr(r, "got_should_reflect", False)))
    return actual / len(expected)


def bad_case_extraction_rate(results: Sequence[Any]) -> float:
    """反思日志 bad_cases 字段非空的占比。"""
    valid = [r for r in results if bool(getattr(r, "reflection_log_written", False))]
    if not valid:
        return 0.0
    non_empty = sum(1 for r in valid if bool(getattr(r, "bad_cases_non_empty", False)))
    return non_empty / len(valid)


# ---------------------------------------------------------------------------
# 聚合辅助
# ---------------------------------------------------------------------------

def summarize(results: Sequence[Any]) -> Dict[str, Any]:
    """对一个 Agent 的结果列表算综合摘要。"""
    return {
        "cases": len(results),
        "success_rate": round(success_rate(results), 4),
        "avg_latency_s": round(avg_latency(results), 3),
        "avg_turns": round(avg_turns(results), 3),
        "composite_score": round(
            composite_score(
                success_rate(results),
                [float(getattr(r, "latency_s", 0.0) or 0.0) for r in results],
                [int(getattr(r, "turns", 0) or 0) for r in results],
            ),
            4,
        ),
    }


def summarize_reflection(results: Sequence[Any]) -> Dict[str, Any]:
    """反思 Agent 的扩展摘要。"""
    base = summarize(results)
    base.update(
        {
            "trigger_precision": round(trigger_precision(results), 4),
            "trigger_recall": round(trigger_recall(results), 4),
            "bad_case_extraction_rate": round(bad_case_extraction_rate(results), 4),
        }
    )
    return base