"""Eval 评测体系管道 smoke test。

不走真实 LLM / DB，纯函数级冒烟测试：
1. 数据集加载
2. EvalResult 序列化
3. metrics 计算（含 composite / 反思子指标）
4. reporting 写出 CSV / JSON
5. argparse 入口 import 通

用法：pytest tests/test_eval_smoke.py -v
"""

import csv
import json
import sys
from pathlib import Path

import pytest

# 让测试能找到 eval 包
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from eval.metrics import (  # noqa: E402
    avg_latency,
    avg_turns,
    bad_case_extraction_rate,
    composite_score,
    min_max_normalize,
    success_rate,
    summarize,
    summarize_reflection,
    trigger_precision,
    trigger_recall,
)
from eval.reporting import generate  # noqa: E402
from eval.runners.base import EvalResult, load_dataset  # noqa: E402


DATASETS_DIR = PROJECT_ROOT / "eval" / "datasets"


class FakeResult:
    """Mock 一个 EvalResult-like 对象，避开 dataclass 构造负担。"""

    def __init__(
        self,
        success=1,
        latency=1.0,
        turns=1,
        expected_should_reflect=False,
        got_should_reflect=False,
        reflection_log_written=False,
        bad_cases_non_empty=False,
        raw_response="",
    ):
        self.success = success
        self.latency_s = latency
        self.turns = turns
        self.expected_should_reflect = expected_should_reflect
        self.got_should_reflect = got_should_reflect
        self.reflection_log_written = reflection_log_written
        self.bad_cases_non_empty = bad_cases_non_empty
        self.raw_response = raw_response


# ---------------------------------------------------------------------------
# 1. 数据集
# ---------------------------------------------------------------------------

def test_load_all_datasets():
    for name, expected_count in [
        ("classification", 8),
        ("appointment", 12),
        ("consultation", 8),
        ("reflection", 6),
    ]:
        cases = load_dataset(DATASETS_DIR / f"{name}_cases.json")
        assert len(cases) == expected_count, f"{name} 数据集大小不对"
        for c in cases:
            assert c.id
            assert isinstance(c.expected, dict)


def test_reflection_dataset_has_input_fixture():
    """reflection 数据集需要带 input_fixture，被 loader 注入到 expected['_fixture']。"""
    cases = load_dataset(DATASETS_DIR / "reflection_cases.json")
    for c in cases:
        assert "_fixture" in c.expected, f"{c.id} 缺 _fixture"
        fx = c.expected["_fixture"]
        assert "appointment_history" in fx
        assert "turns_count" in fx
        # expected 不应包含 _ 前缀的脏 key
        non_fixture_keys = [k for k in c.expected if not k.startswith("_")]
        assert "should_reflect" in non_fixture_keys


# ---------------------------------------------------------------------------
# 2. EvalResult 序列化
# ---------------------------------------------------------------------------

def test_eval_result_to_csv_row():
    r = EvalResult(
        case_id="x", scenario="y", input="z", success=1,
        latency_s=1.234, turns=2, expected={"a": 1}, got={"b": 2},
    )
    row = r.to_csv_row()
    assert row["case_id"] == "x"
    assert row["latency_s"] == 1.234  # round to 3 decimals
    assert row["success"] == 1
    assert "expected_should_reflect" in row
    # expected / got 是 JSON 字符串
    assert json.loads(row["expected"]) == {"a": 1}


# ---------------------------------------------------------------------------
# 3. metrics
# ---------------------------------------------------------------------------

def test_success_rate():
    rs = [FakeResult(success=1), FakeResult(success=0), FakeResult(success=1)]
    assert abs(success_rate(rs) - 2 / 3) < 1e-9


def test_avg_latency_and_turns():
    rs = [FakeResult(latency=1.0, turns=1), FakeResult(latency=3.0, turns=3)]
    assert avg_latency(rs) == 2.0
    assert avg_turns(rs) == 2.0


def test_min_max_normalize_edge_cases():
    assert min_max_normalize([]) == []
    assert min_max_normalize([5, 5, 5]) == [0.5, 0.5, 0.5]
    assert min_max_normalize([1, 2, 3]) == [0.0, 0.5, 1.0]


def test_composite_score_bounds():
    for sr in [0.0, 0.5, 1.0]:
        s = composite_score(sr, [1.0, 2.0], [1, 2])
        assert 0.0 <= s <= 1.0, s


def test_composite_score_perfect_case():
    # 全成功、均匀延迟/轮数 → 0.4 + 0.3*(1-0.5) + 0.3*(1-0.5) = 0.7
    s = composite_score(1.0, [1.0, 2.0, 3.0], [1, 2, 3])
    assert abs(s - 0.7) < 1e-9


def test_summarize_keys():
    rs = [FakeResult(raw_response="hello world")]
    s = summarize(rs)
    expected_keys = {
        "cases",
        "success_rate",
        "avg_latency_s",
        "p50_latency_s",
        "p95_latency_s",
        "p99_latency_s",
        "avg_turns",
        "composite_score",
        "total_estimated_tokens",
        "avg_estimated_tokens",
        "total_cost_usd_estimate",
    }
    assert set(s.keys()) == expected_keys
    assert s["total_estimated_tokens"] > 0
    assert s["p50_latency_s"] == 1.0  # FakeResult 默认 latency=1.0
    assert s["total_cost_usd_estimate"] == 0.0  # 无 prompt/completion tokens


def test_reflection_metrics_all_pass():
    rs = [
        FakeResult(
            expected_should_reflect=True,
            got_should_reflect=True,
            reflection_log_written=True,
            bad_cases_non_empty=True,
        ),
        FakeResult(
            expected_should_reflect=True,
            got_should_reflect=True,
            reflection_log_written=True,
            bad_cases_non_empty=False,
        ),
        FakeResult(
            expected_should_reflect=False,
            got_should_reflect=False,
            reflection_log_written=False,
            bad_cases_non_empty=False,
        ),
    ]
    assert trigger_precision(rs) == 1.0
    assert trigger_recall(rs) == 1.0
    assert bad_case_extraction_rate(rs) == 0.5


def test_reflection_metrics_miss():
    # 期望触发但实际没触发 → precision/recall < 1
    rs = [
        FakeResult(
            expected_should_reflect=True,
            got_should_reflect=False,  # 漏触发
            reflection_log_written=False,
            bad_cases_non_empty=False,
        ),
    ]
    assert trigger_precision(rs) == 0.0  # 分母为 0
    assert trigger_recall(rs) == 0.0


def test_summarize_reflection_has_extra_keys():
    s = summarize_reflection([FakeResult()])
    assert "trigger_precision" in s
    assert "trigger_recall" in s
    assert "bad_case_extraction_rate" in s


# ---------------------------------------------------------------------------
# 4. reporting 端到端
# ---------------------------------------------------------------------------

def _make_results(n: int) -> list:
    return [
        EvalResult(
            case_id=f"fake_{i:03d}",
            scenario=f"scenario {i}",
            input=f"input {i}",
            success=1 if i % 2 == 0 else 0,
            latency_s=1.0 + i * 0.1,
            turns=i % 5 + 1,
            expected={"x": i},
            got={"y": i * 2},
        )
        for i in range(n)
    ]


def test_generate_reports(tmp_path):
    agent_results = {
        "classifier": _make_results(4),
        "appointment": _make_results(6),
    }
    run_dir = generate(agent_results, reports_root=tmp_path)

    # summary CSV
    summary_csv = run_dir / "eval_summary.csv"
    assert summary_csv.exists()
    with summary_csv.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    agents = {r["agent"] for r in rows}
    assert agents == {"classifier", "appointment"}
    for r in rows:
        assert int(r["cases"]) > 0
        assert 0.0 <= float(r["success_rate"]) <= 1.0
        assert 0.0 <= float(r["composite_score"]) <= 1.0

    # per_agent CSV
    for agent, results in agent_results.items():
        detail_csv = run_dir / "per_agent" / f"{agent}.csv"
        assert detail_csv.exists()
        with detail_csv.open(encoding="utf-8", newline="") as f:
            detail_rows = list(csv.DictReader(f))
        assert len(detail_rows) == len(results)

    # JSON snapshot
    snap_path = run_dir / "latest_run.json"
    assert snap_path.exists()
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert "summary" in snap
    assert "detail" in snap
    assert set(snap["detail"].keys()) == {"classifier", "appointment"}
    assert len(snap["detail"]["classifier"]) == 4

    # 顶层 latest_run.json 指针
    latest = tmp_path / "latest_run.json"
    assert latest.exists() or latest.is_symlink()


def test_generate_handles_empty_agent(tmp_path):
    """空结果列表不应该让 reporting 崩。"""
    agent_results = {"classifier": _make_results(0)}
    run_dir = generate(agent_results, reports_root=tmp_path)
    assert (run_dir / "eval_summary.csv").exists()
    with (run_dir / "eval_summary.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["agent"] == "classifier"
    assert int(rows[0]["cases"]) == 0


# ---------------------------------------------------------------------------
# 5. 入口
# ---------------------------------------------------------------------------

def test_run_eval_imports():
    """入口模块能 import 即可（不实际跑）。"""
    from eval import run_eval as _run_eval  # noqa: F401

    assert hasattr(_run_eval, "main")
    assert hasattr(_run_eval, "_register")
    reg = _run_eval._register()
    assert set(reg.keys()) == {
        "classifier",
        "appointment",
        "consultation",
        "reflection",
    }
    for name, info in reg.items():
        assert info["dataset"].exists(), f"{name} dataset 路径找不到"
        assert callable(info["runner"])