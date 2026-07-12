"""
反思系统演示脚本（基于真实 API 重写版）

覆盖的链路：
1. 评估（Evaluator）
2. 反思分析（Analyzer）
3. 报告生成（Reporter）
4. 策略生成与激活（StrategyUpdater）
5. 闭环评估（ClosedLoopEvaluator）
6. 上下文注入（ContextProvider）
7. ReflectionAwareMixin 集成

仅验证"管道不漏"，不验证"反思系统是否提升效果"。
效果验证需要：基线数据 → 开启反思 → 对比指标，详见项目 notes.md。
"""

import sys
import io
import asyncio
from datetime import datetime
from typing import Dict, Any

# 解决 PowerShell 中文乱码（输出按 UTF-8 强制刷新）
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


def banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def step(title: str) -> None:
    print(f"\n--- {title} ---")


def show(label: str, value: Any) -> None:
    if isinstance(value, (dict, list)):
        import json
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except Exception:
            text = str(value)
    else:
        text = str(value)
    print(f"  {label}: {text}")


# =================================================================
# Demo 1: 基础反思 Agent 全链路
# =================================================================
async def demo_basic_reflection() -> None:
    banner("Demo 1: 反思 Agent 全链路（评估 → 反思 → 报告 → 洞察 → 仪表盘）")

    # 延迟导入，让脚本能在缺包时报清晰错误
    from agents.reflection_agent import ReflectionAgent

    reflection = ReflectionAgent()

    # 1) 成功预约
    step("1.1 成功预约任务")
    success_data = {
        "gender": "male",
        "start_time": "2024-01-15T14:00:00",
        "duration": 60,
        "project": "全身按摩",
        "technician": "1",
        "technician_name": "张师傅",
    }
    r = await reflection.reflect_on_appointment(
        session_id="demo_success",
        appointment_history=success_data,
        turns_count=4,
        completion_time=45.5,
    )
    show("评估结果", {
        "success_level": r["evaluation"]["success_level"],
        "success_rate": f"{r['evaluation']['success_rate']:.1%}",
        "should_reflect": r["evaluation"]["should_reflect"],
        "missing_fields": r["evaluation"].get("missing_fields", []),
    })

    # 2) 失败预约
    step("1.2 失败预约任务（缺 start_time 和 project）")
    failed_data = {
        "gender": "female",
        "start_time": None,
        "duration": 60,
        "project": None,
    }
    r = await reflection.reflect_on_appointment(
        session_id="demo_failed",
        appointment_history=failed_data,
        turns_count=8,
        completion_time=120.0,
    )
    show("评估结果", {
        "success_level": r["evaluation"]["success_level"],
        "success_rate": f"{r['evaluation']['success_rate']:.1%}",
        "error_type": r["evaluation"]["error_type"],
        "missing_fields": r["evaluation"]["missing_fields"],
        "should_reflect": r["evaluation"]["should_reflect"],
    })

    # 3) 用户反馈
    step("1.3 显式用户反馈")
    fid = reflection.record_explicit_feedback(
        session_id="demo_success",
        feedback_type="rating",
        rating=5,
        content="服务很好",
    )
    show("反馈 ID", fid)

    # 4) 周报
    step("1.4 周报（周期性反思）")
    weekly = reflection.get_weekly_report()
    show("报告类型", weekly.get("type"))
    show("总结", weekly.get("summary"))

    # 5) 洞察
    step("1.5 反思洞察")
    insights = reflection.get_insights(days=7)
    show("摘要", insights.get("summary"))
    show("可执行建议数", len(insights.get("actionable_recommendations", [])))
    show("坏 case 数", len(insights.get("recent_bad_cases", [])))

    # 6) 统计
    step("1.6 评估统计")
    stats = reflection.get_statistics(days=30)
    for task_type, stat in stats.items():
        if stat.get("total", 0) > 0:
            show(
                task_type,
                {
                    "总数": stat["total"],
                    "成功率": f"{stat.get('success_rate', 0):.1%}",
                },
            )


# =================================================================
# Demo 2: 策略生成 → 激活 → 闭环评估
# =================================================================
def demo_strategy_pipeline() -> None:
    banner("Demo 2: 策略生成 / 激活 / 闭环评估")

    from agents.reflection import ReflectionEngine, StrategyType

    # 从 ReflectionAgent 取出已经初始化好的 engine
    from agents.reflection_agent import ReflectionAgent

    reflection = ReflectionAgent()
    engine: ReflectionEngine = reflection.engine

    # 1) 生成策略
    step("2.1 基于现有洞察生成策略")
    insights = engine.get_reflection_insights(days=30)
    new_strategies = engine.strategy_updater.generate_strategies_from_insights(insights)
    show("生成策略数", len(new_strategies))
    if new_strategies:
        for s in new_strategies[:3]:
            show(
                f"策略 {s.version_id}",
                {
                    "type": s.strategy_type.value,
                    "name": s.name,
                    "priority": s.priority,
                    "config": s.config,
                },
            )

    # 2) 激活策略
    step("2.2 激活一个新策略")
    activated = []
    for s in new_strategies[:2]:
        ok = engine.strategy_updater.activate_strategy(s.version_id, s.strategy_type)
        if ok:
            activated.append(s.version_id)
    show("已激活版本", activated)

    # 3) 查询活跃策略
    step("2.3 当前活跃策略")
    active = engine.get_active_strategies()
    show("活跃策略数", len(active))
    for st_type, info in active.items():
        show(st_type, info)

    # 4) 闭环评估（注意：完整 Before/After 需要 2.x 节中跑出来的真实坏 case + 时间窗口数据，
    #          demo 数据库里数据量小，结果可能为 NO_CHANGE，这属正常）
    step("2.4 闭环效果评估（Before/After 对比）")
    if activated:
        try:
            result = engine.evaluate_strategy_effectiveness(
                strategy_version_id=activated[0],
                task_type="appointment",
            )
            show("评估结果", result)
        except Exception as e:
            show("评估异常（已捕获）", f"{type(e).__name__}: {e}")

    # 5) 跑一次完整的闭环周期
    step("2.5 运行完整闭环周期")
    try:
        cycle = engine.run_closed_loop_cycle(task_type="appointment")
        show("闭环结果", {
            "analysis_strategies": cycle.get("analysis", {}).get("strategies_updated", 0),
        })
    except AttributeError as e:
        # 已知 bug：closed_loop_evaluator.py:216 调用
        #   evaluation_repo.get_evaluations_by_task_type(...)
        # 但 EvaluationRepository 没有这个方法（实际叫 get_failed_evaluations）
        show("⚠ 真实代码 Bug", f"{e}")
        show("位置", "agents/reflection/closed_loop_evaluator.py:216")
        show("建议", "把 get_evaluations_by_task_type 改为 EvaluationRepository 上真实存在的方法")


# =================================================================
# Demo 3: ContextProvider 提示词注入
# =================================================================
def demo_context_injection() -> None:
    banner("Demo 3: 反思上下文 → 提示词注入")

    from agents.reflection_agent import ReflectionAgent
    from agents.reflection import ContextFormat

    reflection = ReflectionAgent()
    engine = reflection.engine

    step("3.1 拉取 Agent 可用的反思上下文")
    try:
        ctx = engine.get_context_for_agent(
            session_id="demo_ctx",
            task_type="appointment",
            format=ContextFormat.COMPACT.value,
        )
        show("上下文生成方式", ctx.get("generation_method"))
        show("confidence", ctx.get("confidence"))
        show("recent_insights 数", len(ctx.get("recent_insights", [])))
        show("do_list", ctx.get("do_list"))
        show("avoid_list", ctx.get("avoid_list"))
    except AttributeError as e:
        # 已知 bug：context_provider.py:225 调用
        #   self.reflection_engine.get_task_type_insights(task_type)
        # 但 ReflectionEngine 上没有这个方法
        show("⚠ 真实代码 Bug", f"{e}")
        show("位置", "agents/reflection/context_provider.py:225")
        show("建议", "在 ReflectionEngine 上补一个 get_task_type_insights 方法，或直接注释掉 _build_context 中的这段")
        return

    step("3.2 注入到基础提示词")
    base_prompt = "你是按摩房预约助手，请礼貌地服务用户。"
    try:
        injected = engine.inject_insights_into_prompt(
            base_prompt=base_prompt,
            session_id="demo_ctx",
            task_type="appointment",
            format=ContextFormat.COMPACT.value,
        )
        print("\n--- 注入后 prompt 预览 ---")
        print(injected[:500] + ("..." if len(injected) > 500 else ""))
        print("--- end ---\n")
    except Exception as e:
        show("注入异常", f"{type(e).__name__}: {e}")


# =================================================================
# Demo 4: ReflectionAwareMixin 集成
# =================================================================
async def demo_mixin_integration() -> None:
    banner("Demo 4: 业务 Agent 通过 ReflectionAwareMixin 接入反思")

    from agents.reflection.reflection_aware import ReflectionAwareMixin
    from agents.reflection_agent import ReflectionAgent

    class AppointmentAgentWithReflection(ReflectionAwareMixin):
        def __init__(self, reflection_engine):
            # ★ 关键：必须把 reflection_engine 传给 mixin
            super().__init__(reflection_engine=reflection_engine)
            self.session_id = "demo_mixin_session"

        # ABC 要求实现的抽象方法
        def apply_insights(self, insights: Dict[str, Any]) -> None:
            self._last_applied_insights = insights

        async def handle(self, user_request: str) -> Dict[str, Any]:
            # 业务 Agent 在决策前查询反思洞察
            insights = self.get_insights()
            avoid = self.should_avoid_pattern("low_completion")
            preferred = self.get_preferred_strategy("appointment")
            self.apply_insights(insights)

            # 模拟完成预约并触发反思
            completion = {
                "gender": "male",
                "start_time": "2024-01-15T20:00:00",
                "duration": 90,
                "project": "肩颈按摩",
            }
            result = await self._reflection_engine.reflect_on_task(
                session_id=self.session_id,
                task_type="appointment",
                task_result=completion,
                turns_count=3,
                completion_time=20.0,
            )

            return {
                "request": user_request,
                "insight_summary": insights.get("summary"),
                "should_avoid_low_completion": avoid,
                "preferred_strategy": preferred,
                "evaluation": result["evaluation"]["success_level"],
                "applied_insight_keys": list(self._last_applied_insights.keys()),
            }

    reflection = ReflectionAgent()
    try:
        agent = AppointmentAgentWithReflection(reflection_engine=reflection.engine)
        out = await agent.handle("我想预约明晚 8 点的肩颈按摩")
        show("业务 Agent 输出", out)
    except TypeError as e:
        show("⚠ 抽象类实例化失败", f"{e}")


# =================================================================
# Demo 5: 仪表盘
# =================================================================
def demo_dashboard() -> None:
    banner("Demo 5: 仪表盘数据")

    from agents.reflection_agent import ReflectionAgent

    reflection = ReflectionAgent()
    dashboard = reflection.get_dashboard()
    show("概览", dashboard.get("overview"))
    if dashboard.get("alerts"):
        show("告警", dashboard["alerts"])
    else:
        show("告警", "（无）")


# =================================================================
# main
# =================================================================
async def main() -> None:
    print("#" * 60)
    print("# 反思系统演示（管道冒烟测试）")
    print("#" * 60)

    await demo_basic_reflection()
    demo_strategy_pipeline()
    demo_context_injection()
    await demo_mixin_integration()
    demo_dashboard()

    print("\n" + "#" * 60)
    print("# 演示完成")
    print("#" * 60)


if __name__ == "__main__":
    asyncio.run(main())