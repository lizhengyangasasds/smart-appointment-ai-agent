"""评测入口。

用法：
    python -m eval.run_eval
    python -m eval.run_eval --agent appointment
    python -m eval.run_eval --agent classifier --limit 2
    python -m eval.run_eval --reports-dir /tmp/eval_out
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List

from .reporting import generate
from .runners.base import EvalCase, EvalResult, load_dataset


# ---------------------------------------------------------------------------
# Agent → (数据集路径, runner 函数) 的注册表
# ---------------------------------------------------------------------------

DATASETS_DIR = Path(__file__).parent / "datasets"


def _register() -> Dict[str, dict]:
    """延迟导入 runner，避免 load_dotenv 副作用。"""
    from .runners.classification_runner import run as run_cls
    from .runners.appointment_runner import run as run_appt
    from .runners.consultation_runner import run as run_cons
    from .runners.reflection_runner import run as run_refl

    return {
        "classifier": {
            "dataset": DATASETS_DIR / "classification_cases.json",
            "runner": run_cls,
        },
        "appointment": {
            "dataset": DATASETS_DIR / "appointment_cases.json",
            "runner": run_appt,
        },
        "consultation": {
            "dataset": DATASETS_DIR / "consultation_cases.json",
            "runner": run_cons,
        },
        "reflection": {
            "dataset": DATASETS_DIR / "reflection_cases.json",
            "runner": run_refl,
        },
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smart Appointment AI Agent — 离线评测"
    )
    parser.add_argument(
        "--agent",
        choices=["classifier", "appointment", "consultation", "reflection", "all"],
        default="all",
        help="要评测的 Agent（默认 all）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制每个 Agent 的 case 数（debug 用）",
    )
    parser.add_argument(
        "--reports-dir",
        default="reports",
        help="报告输出根目录（默认 ./reports）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印每个 case 的简要结果",
    )
    args = parser.parse_args(argv)

    # 日志降噪
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    registry = _register()
    agents_to_run = (
        list(registry.keys()) if args.agent == "all" else [args.agent]
    )

    agent_results: Dict[str, List[EvalResult]] = {}
    overall_t0 = time.monotonic()

    for agent in agents_to_run:
        info = registry[agent]
        cases: List[EvalCase] = load_dataset(info["dataset"])
        if args.limit:
            cases = cases[: args.limit]

        print(f"\n>>> [{agent}] 共 {len(cases)} 条 case，开始评测 …")
        t0 = time.monotonic()
        results: List[EvalResult] = info["runner"](cases)
        elapsed = time.monotonic() - t0
        agent_results[agent] = results

        # 简表
        succ = sum(1 for r in results if r.success == 1)
        print(
            f"<<< [{agent}] 完成 {len(results)} 条，"
            f"成功 {succ} 条，平均延迟 "
            f"{sum(r.latency_s for r in results) / max(len(results), 1):.2f}s，"
            f"总耗时 {elapsed:.2f}s"
        )
        if args.verbose:
            for r in results:
                marker = "OK" if r.success == 1 else "FAIL"
                print(
                    f"    [{marker}] {r.case_id}  "
                    f"latency={r.latency_s:.2f}s turns={r.turns}  "
                    f"{r.scenario}"
                )

    overall_elapsed = time.monotonic() - overall_t0

    # 报告
    run_dir = generate(agent_results, reports_root=args.reports_dir)
    print("\n========================================")
    print(f"评测完成，总耗时 {overall_elapsed:.2f}s")
    print(f"报告目录：{run_dir}")
    print(f"  - 总览：{run_dir / 'eval_summary.csv'}")
    print(f"  - 详情：{run_dir / 'per_agent'}/")
    print(f"  - 快照：{run_dir / 'latest_run.json'}")
    print(f"  - 软链：{Path(args.reports_dir) / 'latest_run.json'}")
    print("========================================\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())