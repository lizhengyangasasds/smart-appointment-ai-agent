"""报告生成：聚合 → CSV / JSON。

产物：
  - reports/<run_at>/eval_summary.csv
  - reports/<run_at>/per_agent/<agent>.csv
  - reports/<run_at>/latest_run.json
  - reports/latest_run.json（顶层指针，永远指向最新一次）
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .metrics import summarize, summarize_reflection
from .runners.base import EvalResult


# ---------------------------------------------------------------------------
# 落盘工具
# ---------------------------------------------------------------------------

def _now_stamp() -> str:
    """生成时间戳目录名：YYYYMMDD-HHMMSS。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def make_run_dir(reports_root: str | Path = "reports") -> Path:
    """建 reports/<时间戳>/ 目录。"""
    root = Path(reports_root)
    run_dir = root / _now_stamp()
    (run_dir / "per_agent").mkdir(parents=True, exist_ok=True)
    return run_dir


def write_per_agent_csv(results: List[EvalResult], path: Path) -> None:
    """每个 Agent 一份 detail CSV。"""
    if not results:
        # 写一行表头空数据，方便面试展示"这个 Agent 跑了 0 条"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "case_id",
                    "scenario",
                    "input",
                    "expected",
                    "got",
                    "success",
                    "latency_s",
                    "turns",
                    "error",
                ]
            )
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        header = list(results[0].to_csv_row().keys())
        w.writerow(header)
        for r in results:
            w.writerow(r.to_csv_row().values())


def write_summary_csv(
    summary_rows: List[Dict[str, Any]], path: Path
) -> None:
    """总览 CSV：agent, cases, success_rate, avg_latency_s, avg_turns, composite_score, run_at[, 反思子指标]。"""
    if not summary_rows:
        return
    fieldnames = list(summary_rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in summary_rows:
            w.writerow(row)


def write_json_snapshot(
    agent_results: Dict[str, List[EvalResult]],
    summary_rows: List[Dict[str, Any]],
    path: Path,
) -> None:
    """完整快照 JSON（含每条 case 详情）。"""
    payload = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary_rows,
        "detail": {
            agent: [asdict(r) for r in results]
            for agent, results in agent_results.items()
        },
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

def build_summary_row(
    agent: str,
    results: List[EvalResult],
    run_at: str,
    is_reflection: bool = False,
) -> Dict[str, Any]:
    """一行 summary。"""
    s = summarize_reflection(results) if is_reflection else summarize(results)
    row: Dict[str, Any] = {
        "agent": agent,
        "cases": s["cases"],
        "success_rate": s["success_rate"],
        "avg_latency_s": s["avg_latency_s"],
        "avg_turns": s["avg_turns"],
        "composite_score": s["composite_score"],
        "run_at": run_at,
    }
    if is_reflection:
        row.update(
            {
                "trigger_precision": s["trigger_precision"],
                "trigger_recall": s["trigger_recall"],
                "bad_case_extraction_rate": s["bad_case_extraction_rate"],
            }
        )
    return row


def generate(
    agent_results: Dict[str, List[EvalResult]],
    reports_root: str | Path = "reports",
) -> Path:
    """生成整次评测的全套报告，返回 run_dir。"""
    root = Path(reports_root)
    run_dir = make_run_dir(root)
    run_at = run_dir.name

    summary_rows: List[Dict[str, Any]] = []

    for agent, results in agent_results.items():
        is_reflection = agent == "reflection"
        # per-agent detail CSV
        detail_path = run_dir / "per_agent" / f"{agent}.csv"
        write_per_agent_csv(results, detail_path)

        # 一行 summary
        row = build_summary_row(agent, results, run_at, is_reflection=is_reflection)
        summary_rows.append(row)

    # 总览 CSV
    summary_csv = run_dir / "eval_summary.csv"
    write_summary_csv(summary_rows, summary_csv)

    # 全量 JSON 快照
    snapshot_path = run_dir / "latest_run.json"
    write_json_snapshot(agent_results, summary_rows, snapshot_path)

    # 顶层 latest_run.json 软链 → 当前 run
    latest = root / "latest_run.json"
    if latest.exists() or latest.is_symlink():
        try:
            latest.unlink()
        except Exception:
            pass
    try:
        # 用相对路径，让 reports 目录可移植
        latest.symlink_to(snapshot_path.relative_to(root))
    except OSError:
        # Windows 可能不允许 symlink；fallback 复制内容
        import shutil

        shutil.copy(snapshot_path, latest)

    return run_dir