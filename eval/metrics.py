"""评测指标计算：3 个基础指标 + composite + 反思子指标 + 延迟分位数 + Token 消耗。"""

from __future__ import annotations

import math
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
# 延迟分位数（P50 / P95 / P99）—— 面试高频问题，比平均值更有说服力
# ---------------------------------------------------------------------------

def latency_percentiles(results: Sequence[Any], *, unit: str = "s") -> Dict[str, float]:
    """计算 P50 / P95 / P99 延迟分位数。

    Args:
        results: EvalResult 列表。
        unit: 返回单位，"s"（秒，默认）或 "ms"（毫秒）。

    Returns:
        {"p50": float, "p95": float, "p99": float}，单位由 unit 决定。
    """
    latencies = sorted(float(getattr(r, "latency_s", 0.0) or 0.0) for r in results)
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    factor = 1000.0 if unit == "ms" else 1.0
    return {
        "p50": round(_percentile(latencies, 50) * factor, 2),
        "p95": round(_percentile(latencies, 95) * factor, 2),
        "p99": round(_percentile(latencies, 99) * factor, 2),
    }


def _percentile(sorted_data: List[float], pct: float) -> float:
    """最近秩法（nearest-rank）计算分位数。"""
    if len(sorted_data) == 1:
        return sorted_data[0]
    k = (pct / 100.0) * (len(sorted_data) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return d0 + d1


# ---------------------------------------------------------------------------
# Token 消耗估算（无 tiktoken 依赖时用字符数/4 近似）
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（粗略近似：中文约 1.5 token/字，英文约 0.25 token/char）。

    更精确的做法是引入 tiktoken，但为了零依赖这里用分段估算。
    """
    if not text:
        return 0
    # 统计 CJK 字符数
    cjk_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    non_cjk_len = len(text) - cjk_count
    return int(cjk_count * 1.5 + non_cjk_len * 0.25)


def token_consumption(results: Sequence[Any]) -> Dict[str, Any]:
    """统计评测结果的 token 消耗。

    每个 EvalResult 的 got 里可能包含：
      - got["prompt_tokens"]: int 或 None —— prompt token 数（如果 runner 捕获了）
      - got["completion_tokens"]: int 或 None —— 完成 token 数
      - got["raw_response"]: str —— 原始 LLM 响应（用于估算）

    也兼容 EvalResult 直接字段：
      - r.raw_response: str

    返回：
      {
        "total_prompt_tokens": int,
        "total_completion_tokens": int,
        "avg_prompt_tokens": float,
        "avg_completion_tokens": float,
        "total_estimated_tokens": int,
        "avg_estimated_tokens": float,
        "total_cost_usd_estimate": float,
      }
    """
    prompt_toks: List[int] = []
    completion_toks: List[int] = []
    estimated: List[int] = []

    for r in results:
        got = getattr(r, "got", {}) or {}
        pt = got.get("prompt_tokens") or getattr(r, "prompt_tokens", 0)
        ct = got.get("completion_tokens") or getattr(r, "completion_tokens", 0)
        if pt:
            prompt_toks.append(int(pt))
        if ct:
            completion_toks.append(int(ct))

        raw = got.get("raw_response") or getattr(r, "raw_response", "") or ""
        if raw:
            estimated.append(estimate_tokens(str(raw)))

    total_pt = sum(prompt_toks)
    total_ct = sum(completion_toks)
    total_est = sum(estimated)

    # GPT-4o 近似价格：$2.50/M prompt tokens, $10.00/M completion tokens
    cost = total_pt * 2.50 / 1_000_000 + total_ct * 10.00 / 1_000_000

    return {
        "total_prompt_tokens": total_pt,
        "total_completion_tokens": total_ct,
        "avg_prompt_tokens": round(total_pt / len(prompt_toks), 1) if prompt_toks else 0,
        "avg_completion_tokens": round(total_ct / len(completion_toks), 1) if completion_toks else 0,
        "total_estimated_tokens": total_est,
        "avg_estimated_tokens": round(total_est / len(estimated), 1) if estimated else 0,
        "total_cost_usd_estimate": round(cost, 6),
    }


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
    latencies = [float(getattr(r, "latency_s", 0.0) or 0.0) for r in results]
    turns = [int(getattr(r, "turns", 0) or 0) for r in results]
    lp = latency_percentiles(results)
    tc = token_consumption(results)
    return {
        "cases": len(results),
        "success_rate": round(success_rate(results), 4),
        "avg_latency_s": round(avg_latency(results), 3),
        "p50_latency_s": lp["p50"],
        "p95_latency_s": lp["p95"],
        "p99_latency_s": lp["p99"],
        "avg_turns": round(avg_turns(results), 3),
        "composite_score": round(
            composite_score(
                success_rate(results),
                latencies,
                turns,
            ),
            4,
        ),
        "total_estimated_tokens": tc["total_estimated_tokens"],
        "avg_estimated_tokens": tc["avg_estimated_tokens"],
        "total_cost_usd_estimate": tc["total_cost_usd_estimate"],
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