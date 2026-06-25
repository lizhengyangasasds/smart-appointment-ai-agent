"""
反思 Agent 使用示例

展示如何在预约流程中集成反思功能
"""

import asyncio
from datetime import datetime


async def demo_reflection_agent():
    """演示反思 Agent 的基本用法"""
    from agents.reflection_agent import ReflectionAgent

    # 初始化反思 Agent
    reflection = ReflectionAgent()

    print("=" * 60)
    print("反思 Agent 演示")
    print("=" * 60)

    # 1. 模拟一个成功的预约任务反思
    print("\n1. 模拟成功预约任务反思")
    print("-" * 40)

    success_history = {
        "gender": "male",
        "start_time": "2024-01-15T14:00:00",
        "duration": 60,
        "project": "全身按摩",
        "technician": "1",
        "technician_name": "张师傅"
    }

    result = await reflection.reflect_on_appointment(
        session_id="session_001",
        appointment_history=success_history,
        turns_count=4,
        completion_time=45.5
    )

    print(f"评估结果: {result['evaluation']['success_level']}")
    print(f"成功率: {result['evaluation']['success_rate']:.1%}")
    print(f"触发反思: {result['evaluation']['should_reflect']}")

    # 2. 模拟一个失败的预约任务反思
    print("\n2. 模拟失败预约任务反思")
    print("-" * 40)

    failed_history = {
        "gender": "female",
        "start_time": None,  # 缺失关键信息
        "duration": 60,
        "project": None
    }

    result = await reflection.reflect_on_appointment(
        session_id="session_002",
        appointment_history=failed_history,
        turns_count=8,
        completion_time=120.0
    )

    print(f"评估结果: {result['evaluation']['success_level']}")
    print(f"成功率: {result['evaluation']['success_rate']:.1%}")
    print(f"错误类型: {result['evaluation']['error_type']}")
    print(f"缺失字段: {result['evaluation']['missing_fields']}")
    print(f"触发反思: {result['evaluation']['should_reflect']}")

    if result.get('reflection'):
        print("\n反思发现:")
        findings = result['reflection'].get('findings', {})
        print(f"  - 失败任务数: {findings.get('failure_analysis', {}).get('total_failed', 0)}")
        print(f"  - 根因分析: {findings.get('failure_analysis', {}).get('root_causes', [])[:2]}")

    # 3. 模拟用户反馈
    print("\n3. 记录用户反馈")
    print("-" * 40)

    feedback_id = reflection.record_explicit_feedback(
        session_id="session_001",
        feedback_type="rating",
        rating=5,
        content="服务很好，技师很专业"
    )
    print(f"反馈ID: {feedback_id}")

    # 4. 获取周报
    print("\n4. 获取周报")
    print("-" * 40)

    weekly_report = reflection.get_weekly_report()
    print(f"报告类型: {weekly_report.get('type')}")
    print(f"总结: {weekly_report.get('summary')}")

    # 5. 获取洞察
    print("\n5. 获取反思洞察")
    print("-" * 40)

    insights = reflection.get_insights(days=7)
    print(f"近期洞察: {insights.get('summary')}")
    print(f"可执行建议数: {len(insights.get('actionable_recommendations', []))}")

    # 6. 获取统计数据
    print("\n6. 获取评估统计")
    print("-" * 40)

    stats = reflection.get_statistics(days=30)
    for task_type, stat in stats.items():
        if stat.get('total', 0) > 0:
            print(f"{task_type}: 总数={stat['total']}, 成功率={stat.get('success_rate', 0):.1%}")


async def demo_integration_with_appointment():
    """演示如何与预约 Agent 集成"""
    from agents.reflection_agent import ReflectionMixin

    class AppointmentAgentWithReflection(ReflectionMixin):
        """带有反思功能的预约 Agent"""

        def __init__(self):
            self.session_id = "demo_session"
            self.turns_count = 0
            self.start_time = datetime.now()

        async def complete_appointment(self, appointment_history: dict):
            """完成预约后自动反思"""
            completion_time = (datetime.now() - self.start_time).total_seconds()

            result = await self.reflect_after_completion(
                session_id=self.session_id,
                task_type='appointment',
                task_data=appointment_history,
                turns_count=self.turns_count,
                completion_time=completion_time
            )

            print(f"预约完成反思: 成功率={result['evaluation']['success_rate']:.1%}")
            return result

    print("\n" + "=" * 60)
    print("集成演示: 带有反思功能的预约 Agent")
    print("=" * 60)

    agent = AppointmentAgentWithReflection()

    # 模拟预约流程
    appointment_data = {
        "gender": "male",
        "start_time": "2024-01-15T15:00:00",
        "duration": 90,
        "project": "肩颈按摩"
    }

    await agent.complete_appointment(appointment_data)


def demo_dashboard():
    """演示仪表盘数据获取"""
    from agents.reflection_agent import ReflectionAgent

    print("\n" + "=" * 60)
    print("仪表盘演示")
    print("=" * 60)

    reflection = ReflectionAgent()
    dashboard = reflection.get_dashboard()

    print(f"\n概览:")
    for period, stats in dashboard.get('overview', {}).items():
        print(f"  {period}: {stats.get('total_reflections', 0)} 次反思")

    if dashboard.get('alerts'):
        print(f"\n告警:")
        for alert in dashboard['alerts']:
            print(f"  [{alert['level']}] {alert['message']}")


if __name__ == "__main__":
    print("\n" + "#" * 60)
    print("# 反思 Agent 使用示例")
    print("#" * 60)

    # 运行演示
    asyncio.run(demo_reflection_agent())
    asyncio.run(demo_integration_with_appointment())
    demo_dashboard()

    print("\n" + "#" * 60)
    print("# 演示完成")
    print("#" * 60)
