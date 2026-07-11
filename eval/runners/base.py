"""Runner 基类 + EvalCase / EvalResult 数据模型 + 通用计时与异常采集。"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    """一条评测 case。

    字段：
      id           - case 标识
      scenario     - 人类可读的"这条 case 在测什么"
      input        - 用户原始输入
      expected     - 期望输出（每个 Agent runner 自己解析字段含义）
      turns        - 期望轮数（默认 1，单条输入）
    """

    id: str
    scenario: str
    input: str
    expected: Dict[str, Any]
    turns: int = 1


@dataclass
class EvalResult:
    """一条 case 的评测结果。

    通用字段：
      case_id / scenario / input           —— 来源信息
      success                              —— 0/1（每个 runner 自定义匹配规则）
      latency_s                            —— wall time
      turns                                —— 实测轮数
      expected / got                       —— 期望 vs 实际（dict，列化到 CSV 时转 JSON）
      error                                —— 异常 traceback（无异常为空字符串）

    reflection runner 额外用：
      expected_should_reflect / got_should_reflect
      reflection_log_written / bad_cases_non_empty
    """

    case_id: str
    scenario: str
    input: str
    success: int
    latency_s: float
    turns: int
    expected: Dict[str, Any] = field(default_factory=dict)
    got: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    # reflection-only
    expected_should_reflect: bool = False
    got_should_reflect: bool = False
    reflection_log_written: bool = False
    bad_cases_non_empty: bool = False

    def to_csv_row(self) -> Dict[str, Any]:
        """导出 CSV 用的扁平行。expected / got 序列化成 JSON 字符串。"""
        return {
            "case_id": self.case_id,
            "scenario": self.scenario,
            "input": self.input,
            "expected": json.dumps(self.expected, ensure_ascii=False),
            "got": json.dumps(self.got, ensure_ascii=False),
            "success": self.success,
            "latency_s": round(self.latency_s, 3),
            "turns": self.turns,
            "error": self.error[:500] if self.error else "",
            "expected_should_reflect": self.expected_should_reflect,
            "got_should_reflect": self.got_should_reflect,
            "reflection_log_written": self.reflection_log_written,
            "bad_cases_non_empty": self.bad_cases_non_empty,
        }


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def load_dataset(path: str | Path) -> List[EvalCase]:
    """从 JSON 文件读取数据集。

    JSON 顶层是 list[dict]，每个 dict 形如：
      {id, scenario, input, expected, turns, input_fixture}

    兼容性：
      - 如果只有 input（字符串），用 input
      - 如果同时有 input_fixture（dict），把 fixture 包成
        expected["_fixture"]，runner 自己识别（reflection 用）
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    cases: List[EvalCase] = []
    for item in raw:
        expected = dict(item.get("expected") or {})
        if "input_fixture" in item and isinstance(item["input_fixture"], dict):
            expected["_fixture"] = item["input_fixture"]
        cases.append(
            EvalCase(
                id=str(item["id"]),
                scenario=str(item.get("scenario", "")),
                input=str(item.get("input") or ""),
                expected=expected,
                turns=int(item.get("turns") or 1),
            )
        )
    return cases


async def run_async(coro):
    """统一计时 + 异常采集的 async 包装。"""
    t0 = time.monotonic()
    try:
        result = await coro
        elapsed = time.monotonic() - t0
        return result, elapsed, ""
    except Exception as e:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        tb = traceback.format_exc()
        return None, elapsed, tb


def run_sync(func):
    """统一计时 + 异常采集的 sync 包装（reflection runner 用）。"""
    t0 = time.monotonic()
    try:
        result = func()
        elapsed = time.monotonic() - t0
        return result, elapsed, ""
    except Exception as e:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        tb = traceback.format_exc()
        return None, elapsed, tb


def make_eval_session_id(agent: str, case_id: str) -> str:
    """生成隔离的 session_id（带 eval- 前缀，避免污染业务 DB）。"""
    ts = int(time.time() * 1000)
    return f"eval-{agent}-{case_id}-{ts}"


async def consume_stream_async(stream) -> str:
    """async 版耗尽（用于已经在 async 上下文里）。"""
    parts: List[str] = []
    async for tok in stream:
        if tok:
            parts.append(str(tok))
    return "".join(parts)