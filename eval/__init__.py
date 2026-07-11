"""Smart Appointment AI Agent — 离线评测体系。

面试级 Agent 评测，覆盖 4 个核心 Agent：
  - classifier     (TaskClassificationAgent)
  - appointment    (AppointmentAgent)
  - consultation   (ConsultantAgent)
  - reflection     (TaskEvaluator + ReflectionEngine)

产物：
  - reports/eval_summary.csv         一行一个 Agent 的综合分
  - reports/per_agent/<agent>.csv    每条 case 详情
  - reports/latest_run.json          全量快照

入口：python -m eval.run_eval
"""

__version__ = "0.1.0"