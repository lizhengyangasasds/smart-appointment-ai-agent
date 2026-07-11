# Smart Appointment AI Agent — 面试级 Agent 评测体系

> 一套离线 + CSV 报告的评测体系，覆盖主链路 4 个 Agent（classifier / appointment / consultant / reflection）。
> 用于面试演示与讲解。

---

## 1. 设计目标

| 目标 | 怎么做 |
|---|---|
| **现场可跑** | `python -m eval.run_eval` → 30 条数据，~15 min 出 CSV |
| **结构化指标** | 任务成功率 / 延迟 / 轮数 + 综合分（composite_score）+ 反思子指标 |
| **数据事实源** | 复用项目已有的 `task_evaluations` / `reflection_logs` / `user_behaviors`，**不开新表** |
| **Agent 不重写** | runner 只调现有入口：`AppointmentAgent.run_stream` / `ConsultantAgent.consult_stream` / `TaskClassifier.classify_task` / `TaskEvaluator.evaluate_appointment_task` |
| **失败可见** | case 异常 → 记 `success=0, error=<traceback>`，失败本身就是评测对象 |

---

## 2. 目录结构

```
eval/
├── __init__.py
├── README.md                       # 本文件
├── datasets/                       # 30 条离线评测 case
│   ├── classification_cases.json   # 8 条
│   ├── appointment_cases.json      # 12 条
│   ├── consultation_cases.json     # 8 条
│   └── reflection_cases.json       # 6 条
├── runners/
│   ├── base.py                     # EvalCase / EvalResult / 通用计时 + 异常采集
│   ├── classification_runner.py
│   ├── appointment_runner.py
│   ├── consultation_runner.py
│   └── reflection_runner.py
├── metrics.py                      # 3 个基础指标 + composite + 反思子指标
├── reporting.py                    # 聚合 + 导出 CSV
└── run_eval.py                     # 入口
```

跑出来的 CSV 落到 `reports/<时间戳>/`。

---

## 3. 指标体系

### 3.1 三个基础指标

| 指标 | 定义 | 怎么取 |
|---|---|---|
| `task_success_rate` | case 是否达成 `expected` | appointment 取 `task_evaluations.success == 2`；consultation 取 `user_behaviors` 行为记录完整性 + 知识命中 category；reflection 取 `should_reflect == expected` |
| `latency_seconds` | `run_stream` 入口到 `[EVAL_OK]` / `[EVAL_FAILED]` 之间的 wall time | Python `time.monotonic()` |
| `turns` | 用户为完成目标所需轮次 | 单条 case = 1，多轮时 = `len(chat_history.messages) // 2` |

### 3.2 综合分（沿用项目已有公式方向）

```
composite_score = 0.4 * success_rate
               + 0.3 * (1 - latency_normalized)
               + 0.3 * (1 - turns_normalized)
```

归一化：min-max 把 latency/turns 映射到 [0,1]。综合分解释为"任务完成的总体质量"。

> 同向加权参考 `tests/test_effectiveness.py:447` 的 `0.4*success_improvement + 0.2*turns_reduction + 0.2*time_reduction + 0.2*failure_reduction`。

### 3.3 Reflection 子指标

- `trigger_precision`：`should_reflect=True` 中确实写 `reflection_logs` 的占比
- `trigger_recall`：期望反思的 case 中实际触发的占比
- `bad_case_extraction_rate`：`reflection_logs.bad_cases` 非空的占比

---

## 4. 每个 Agent 的成功判定

| Agent | 入口 | 成功条件 |
|---|---|---|
| Classifier | `TaskClassifier.classify_task(input)` → 原始 category | 与 `expected_task_type` 完全匹配 |
| Appointment | `AppointmentAgent(session_id=case_id).run_stream(input)` → DB 自动落 `task_evaluations` | `task_evaluations.success == 2` |
| Consultant | `ConsultantAgent.consult_stream(input)` → 落 `user_behaviors` | 行为记录 category 命中 `expected_category_set` |
| Reflection | 手工构造 `appointment_history/turns_count/completion_time` → `TaskEvaluator.evaluate_appointment_task` → `engine.reflect_on_task` | `should_reflect == expected` 且 `reflection_log_written == expected` |

---

## 5. 关键约束

- **不重写 Agent** —— runner 只调现有入口
- **不复用业务 DB 主会话** —— 每个 case 用 `session_id = f"eval-{agent}-{case_id}-{ts}"` 隔离
- **失败兜底**：case 异常 → `EvalResult(success=0, error=<traceback>)`，**不吞异常**
- **不调 LLM-as-Judge**（用户已选 L1）—— 避免再调一遍 LLM 把评测成本翻倍
- **不开新表** —— 复用 `task_evaluations` 作为事实源

---

## 6. 面试讲解稿（Q&A）

### Q1：为什么用 `task_evaluations` 表而不是新建 `eval_runs`？
> 项目已有 `EvaluationRepository.save_evaluation`（`db/repositories/reflection_repository.py:43`），每次 Agent 任务自动落库。评测直接复用事实源，避免"评测库说 90% / 业务库说 70%"的不一致。

### Q2：success 为什么是 0/1/2 三态而不是布尔？
> 见 `agents/reflection/evaluator.py:17-22` 的 `SuccessLevel` IntEnum —— 区分 FAILED / PARTIAL / SUCCESS。PARTIAL 提供"差但能用"的中间态，比布尔更细。评测可以同时报"完全成功率"和"非失败率"。

### Q3：composite 为什么成功率占 0.4 主导？
> 参考 `tests/test_effectiveness.py:447` 同向加权；产品体验里"任务能不能完成"比"快不快"更重要。

### Q4：怎么隔离业务数据？
> 每个 case 用独立 `session_id`（前缀 `eval-`），即使写到正式 DB 也不污染业务 session 查询。

### Q5：怎么评估对话级 / 反思闭环？
> 不在 L1 范围。下一档可以加：
> - **L2 对话级**：多轮交互一致性 + 上下文保真度（用对话状态对比）
> - **L3 反思闭环**：A/B 对比（启用 vs 不启用 `ReflectionAwareMixin`），看 `success_rate_improvement` / `turns_reduction`
> - **L4 LLM-as-Judge**：用 LLM 给主观维度（语气、完整度）打分

### Q6：阈值为什么是 0.8 / 10 / 120？
> 见 `agents/reflection/evaluator.py:56-60` 的 `DEFAULT_THRESHOLDS`。这是反思触发的工程经验值：成功率 < 80% 说明要改策略；轮数 > 10 说明用户体验差；耗时 > 120s 说明有性能问题。评测指标的"超过阈值的 case 占比"可以反向验证这套阈值是否合理。

---

## 7. 怎么跑

```bash
# 跑全部 4 个 Agent 共 30 条
python -m eval.run_eval

# 只跑某个 Agent
python -m eval.run_eval --agent appointment

# 限制 case 数（debug）
python -m eval.run_eval --agent classifier --limit 2
```

跑完看 `reports/<时间戳>/eval_summary.csv`。

---

## 8. 已知 trade-off（面试可以主动提）

1. **小样本统计**：30 条不够做 p-value；只展示 counts + mean + std，不做显著性推断。
2. **真实 LLM 成本**：每次跑要消耗 LLM tokens（appointment/consultation 走真实 LLM，reflection 走纯函数）。面试 demo 时控制 `limit` 在 2-5 条。
3. **不重复实现评估口径**：评测成功条件直接复用项目 `SuccessLevel`，避免双口径分裂。
4. **失败也是数据**：如果某条 case 一直 success=0，要么是数据问题，要么是 Agent 真有 bug，**要往数据库里看 traceback**。