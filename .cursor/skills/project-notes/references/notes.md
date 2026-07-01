# 项目问答笔记 — 叶子

> 每个节点代表一个模块的主干，叶子记录具体的 Q&A。
> 格式：Q（问题）→ A（答案）→ 涉及文件 → 时间
> 按模块编号（D0-D8）组织，同一模块下按记录时间倒序。

---

## D0: 架构总览

（暂无叶子记录）

---

## D1: 多Agent架构

（暂无叶子记录）

---

## D2: RAG知识检索

（暂无叶子记录）

---

## D3: 预约流程

（暂无叶子记录）

---

## D4: Agent记忆系统

（暂无叶子记录）

---

## D5: 反思与评估闭环

### Q: 这个项目做到了评估闭环吗？

**A:** 原来做到了技术骨架，但主流程完全断路。本次（2026-07-01）修复后真正闭合。

**原状（骨架完整，断路）：**
- `ReflectionEngine`、`ClosedLoopEvaluator`、`StrategyUpdater` 等组件代码全部存在，结构正确
- `chat_handler.py`（真实用户对话的唯一入口）从未调用任何反思接口，`reflection_engine` 永远为 `None`
- 反思只能通过 `api/reflection_api.py` 手动 POST 调用，没有自动触发机制

**修复后的完整链路：**
1. `chat_handler` 识别预约/咨询任务完成 → `asyncio.create_task(_trigger_reflection())` 静默触发
2. `ReflectionService` 单例调用 `reflect_on_appointment()` → 评估写入 `evaluation_repo`
3. 满足阈值（成功率<0.7 / 轮数>10 / 时间>120s）→ 自动触发 `_perform_reflection()`
4. 分析失败任务 + 发现模式 → 生成新策略 + 激活
5. app.py 启动后每 6 小时后台线程跑 `run_closed_loop_cycle()` → 对比前后数据 → IMPROVED 保持 / DEGRADED 回滚
6. `ReflectionAwareMixin.get_insights()` → 下次 Agent 决策时应用

**关键改动文件：**
- `services/reflection_service.py` — 新增，全局单例封装
- `api/chat_handler.py` — 引擎注入 + 自动触发 + `_SessionMeta` 追踪任务元数据
- `app.py` — 反思服务预热 + 周期性闭环后台线程

- 涉及文件：`services/reflection_service.py`、`api/chat_handler.py`、`app.py`、`agents/reflection_agent.py`、`agents/reflection/engine.py`、`agents/reflection/closed_loop_evaluator.py`
- 记录时间：2026-07-01

---

### Q: 反思闭环的五步分别是什么？

**A:** 评估 → 分析 → 策略生成 → 效果验证 → 策略应用，形成完整反馈循环。

**Step 1 评估（TaskEvaluator）：**
每次任务完成后评估质量。指标包括：成功率（SuccessLevel）、轮数、完成时间。满足阈值（成功率<0.7 或 轮数>10 或 时间>120s）时触发反思。评估结果写入 `evaluation_repo`，含 session_id、task_type、success_rate、turns_count、completion_time、error_type。

**Step 2 分析（ReflectionAnalyzer）：**
从评估数据中发现问题根因。分析维度包括：失败任务根因分析（`analyze_failed_tasks`）、用户行为模式发现（`discover_user_patterns`）、坏 case 识别（`analyze_bad_cases`）。输出包括：patterns_discovered、bad_cases、actionable_recommendations。

**Step 3 策略生成（StrategyUpdater）：**
根据分析结果生成新的 Agent 策略。策略类型：MATCHING（技师匹配）、RECOMMENDATION（推荐逻辑）、ROUTING（路由决策）、PROMPT（回复风格）。新策略生成后立即激活，记录 version_id 和时间窗口，供效果验证器对比。

**Step 4 效果验证（ClosedLoopEvaluator）：**
周期性（默认每 6 小时）运行，对比策略启用前后的数据。核心指标：改进率（improvement_rate）、置信度（confidence）、统计显著性（statistical_significance）。判定结果：IMPROVED → 保持策略 / DEGRADED → 自动回滚 / NO_CHANGE → 继续观察。最小样本量 10 条，数据不足时返回 INSUFFICIENT_DATA。

**Step 5 策略应用（ReflectionAwareMixin）：**
Agent 下次运行时查询 `get_insights()`，调用 `apply_insights()` 注入到具体决策。AppointmentAgent：注入匹配提示（`_matching_hints`）+ 避免模式列表（`_avoid_patterns`），在 `_adjust_matching_strategy()` 中过滤和排序技师候选。ConsultantAgent：注入回复风格提示（`_response_style_hints`）+ 强调/避免话题列表，RAG 检索后通过 `validate_knowledge_retrieval()` 验证完整性。

- 涉及文件：`agents/reflection/evaluator.py`、`agents/reflection/analyzer.py`、`agents/reflection/strategy_updater.py`、`agents/reflection/closed_loop_evaluator.py`、`agents/reflection/reflection_aware.py`
- 记录时间：2026-07-01

---

### Q: ReflectionService 单例的作用是什么？为什么要用单例而不是每次请求创建？

**A:** 避免每次请求重复创建 LLM 实例、实现懒加载感知初始化失败、实现线程安全的全局共享。

**单例的核心价值：**
1. **LLM 实例复用**：`ReflectionAgent.__init__` 会创建 LLM（`create_chat_model(temperature=0.3)`），如果每次反思请求都创建新实例会导致内存浪费和延迟波动。单例确保全进程共享一个 LLM 实例。
2. **懒加载**：`ReflectionService` 内部使用双检查锁（`threading.Lock`），`agent` 属性首次访问时才真正初始化 ReflectionAgent，避免应用启动时就拉起 LLM。
3. **初始化失败隔离**：ReflectionAgent 依赖 `evaluation_repo`、`reflection_repo` 等数据库表。如果表未建，初始化会失败但不会崩溃——`_init_error` 记录错误，`is_available` 返回 False。chat_handler 感知到不可用时静默跳过，不影响用户对话。
4. **异步/同步双接口**：chat_handler 中对话是异步的（`async def`），所以 `reflect_on_appointment` 是 `async def` 直接 await；app.py 的周期性任务是后台线程同步的，所以提供 `_sync` 版本用 `asyncio.run()` 包装。

- 涉及文件：`services/reflection_service.py`
- 记录时间：2026-07-01

---

### Q: 反思是如何在对话流结束后被自动触发的？会不会影响用户响应速度？

**A:** 通过 `asyncio.create_task()` 在响应返回后才触发，完全不影响速度。

**触发机制：**
`chat_handler.py` 的 `_stream_response()` 方法末尾：
```python
if full_response:
    asyncio.create_task(self._trigger_reflection())
```
`create_task` 是"发火即忘"——它把协程加入事件循环的待执行队列，立即返回，不等待 `await`。用户的完整响应已经通过 `yield token` 全部返回后，事件循环才在后台处理反思任务。

**具体流程时序：**
1. 用户发送消息
2. `async for token in session._stream_response(...)` 开始流式返回
3. 所有 token 返回完毕，`full_response` 收集完成
4. `asyncio.create_task(_trigger_reflection())` 被调用——立即返回
5. 用户端收到完整响应（无等待）
6. 事件循环空闲后，在后台执行反思（评估写入 DB、触发分析等）

- 涉及文件：`api/chat_handler.py`（`_trigger_reflection`、`asyncio.create_task`）
- 记录时间：2026-07-01

---

---

## D6: 用户行为分析

（暂无叶子记录）

---

## D7: 数据库层

（暂无叶子记录）

---

## D8: API与配置

（暂无叶子记录）

---
