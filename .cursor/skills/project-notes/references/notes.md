# 项目问答笔记 — 叶子

> 每个节点代表一个模块的主干，叶子记录具体的 Q&A。
> 格式：Q（问题）→ A（答案）→ 涉及文件 → 时间
> 按模块编号（D0-D8）组织，同一模块下按记录时间倒序。

---

## D0: 架构总览

**Q: 项目的整体架构是怎样的？**

**A:**

```
+---------------------------------------------------------------------------------------+
|                              用户消息入口 (API Layer)                                  |
|                                    chat_handler.py                                     |
+---------------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------------+
|                              _MemoryAwareChatSession                                   |
|  ┌─────────────────────────────────────────────────────────────────────────────────┐  |
|  │ - memory_manager: 记忆管理（对话历史 + 用户画像 + 语义记忆）                        │  |
|  │ - _task_meta: 任务元数据（task_type, turn_count, completion_time）               │  |
|  │ - reflection_engine: 反思引擎注入                                                 │  |
|  └─────────────────────────────────────────────────────────────────────────────────┘  |
+---------------------------------------------------------------------------------------+
                                        |
                                        v
+---------------------------------------------------------------------------------------+
|                        TaskClassificationAgent（任务分类Agent）                         |
|                                    (归类机器人)                                        |
|  ┌─────────────────────────────────────────────────────────────────────────────────┐  |
|  │  TaskClassifier      - LLM分类器（5类：appointment/query/pay/statistics/other）  │  │
|  │  StateManager       - 状态管理器（CLASSIFY/APPOINTMENT/CONSULT）                │  │
|  │  AgentRouter        - Agent路由器（分发到具体Agent）                             │  │
|  │  UnrelatedHandler   - 无关请求处理器                                            │  │
|  └─────────────────────────────────────────────────────────────────────────────────┘  |
+---------------------------------------------------------------------------------------+
                    |                                   |
                    v                                   v
+---------------------------+               +-----------------------------+
|   AppointmentAgent        |               |   ConsultantAgent           |
|   (预约机器人)             |               |   (咨询机器人)               |
+---------------------------+               +-----------------------------+
                    |                                   |
                    v                                   v
+---------------------------+               +-----------------------------+
| - InputParser             |               | - KnowledgeRetriever        |
| - TechnicianFinder        |               | - ConsultationClassifier    |
| - AppointmentProcessor    |               | - ResponseGenerator         |
| - MessageBuilder          |               | - ConsultationProcessor     |
| - AppointmentDatabase     |               +-----------------------------+
+---------------------------+                         |
                                                        v
                                        +-----------------------------+
                                        |   RAG Knowledge Base         |
                                        |   (知识库)                   |
                                        +-----------------------------+

                    +-----------------------------------------------------------+
                    |                       ReflectionAgent                      |
                    |                       (反思Agent - D5闭环核心)            |
                    |  ┌─────────────────────────────────────────────────────┐ |
                    |  │ TaskEvaluator  - 任务评估（成功率/轮数/耗时）          │ |
                    |  │ ReflectionAnalyzer - 根因分析（Agent驱动+规则引擎）   │ |
                    |  │ ReflectionReporter  - 报告生成                       │ |
                    |  │ StrategyUpdater     - 策略更新（闭环反馈）            │ |
                    |  │ ClosedLoopEvaluator - 闭环效果验证                   │ |
                    |  └─────────────────────────────────────────────────────┘ |
                    +-----------------------------------------------------------+
                                        ^
                                        |
                    +-----------------------------------------------------------+
                    |                     Memory Manager                          |
                    |  (工作记忆: 对话历史 + 语义记忆: 用户偏好/行为模式)        |
                    +-----------------------------------------------------------+
```

- 涉及文件：`api/chat_handler.py`、`agents/task_classification_agent.py`、`agents/appointment_agent.py`、`agents/consultant_agent.py`、`agents/reflection_agent.py`
- 记录时间：2026-07-02

---

## D1: 多Agent架构

**Q: 三个Agent是如何协作的？任务分类流程是怎样的？**

**A:**

### Agent协作流程

```
用户输入
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                     入口: ProcessUserInput_stream                      │
│                     (api/chat_handler.py)                             │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│  1. 记忆存储 (memory_manager.add_user_message)                         │
│     - 自动提取语义记忆                                                 │
│     - 检查是否需要压缩                                                 │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│  2. TaskClassificationAgent.classify_task_stream                       │
│     ├── should_classify? → 是 → TaskClassifier.classify_task()        │
│     └── 根据分类结果路由:                                             │
│           appointment → AgentRouter.route_to_appointment()            │
│           query       → AgentRouter.route_to_consultation()           │
└───────────────────────────────────────────────────────────────────────┘
    │
    ├───> AppointmentAgent.run_stream() ──> 预约流程
    │
    └───> ConsultantAgent.consult_stream() ──> 咨询流程
```

### 任务分类流程

```
用户输入: "我想预约今天下午3点的推拿服务"
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      分类决策点: should_classify?                     │
│   StateManager.should_classify()                                      │
│   ├── 当前状态 == CLASSIFY → 执行分类                                 │
│   └── 当前状态 != CLASSIFY → 保持当前流程（状态持续）                  │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                     TaskClassifier.classify_task()                     │
│   LLM Prompt: 识别意图并分类为以下5类之一:                             │
│   ├── appointment: 预约任务（"请帮我预约..."）                         │
│   ├── query: 查询任务（"咨询价格/技师信息..."）                        │
│   ├── pay: 支付任务（"确认支付..."）                                  │
│   ├── statistics: 统计任务（工作人员上报）                            │
│   └── other: 其他无关任务                                             │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                        路由分发                                        │
│   ├── category == "appointment" → route_to_appointment()              │
│   ├── category == "query"      → route_to_consultation()              │
│   └── else                    → handle_unsupported_task()              │
└───────────────────────────────────────────────────────────────────────┘
```

### 状态转换

```
StateManager 状态转换图:

                    ┌──────────────┐
                    │   CLASSIFY   │  (初始状态/任务完成后状态)
                    └──────┬───────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          v                v                v
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │ APPOINTMENT │  │   CONSULT   │  │    OTHER     │
   │  (预约流程)  │  │  (咨询流程) │  │  (不支持)    │
   └──────┬───────┘  └──────┬───────┘  └──────────────┘
          │                │
          │                │
          └───────┬────────┘
                  │
                  v
           ┌──────────────┐
           │   CLASSIFY   │  (任务完成后回到初始状态)
           └──────────────┘
```

- 涉及文件：`agents/task_classification_agent.py`、`agents/task_classification/task_classifier.py`、`agents/task_classification/agent_router.py`、`agents/task_classification/state_manager.py`
- 记录时间：2026-07-02

---

## D2: RAG知识检索

**Q: 咨询流程中RAG是如何工作的？**

**A:**

### RAG检索流程

```
知识检索数据流:

用户问题: "推拿有什么好处？"
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                        KnowledgeService.search()                       │
│   1. 文本嵌入: text_embedding.embed_query(question)                   │
│      └── 生成查询向量: [0.12, -0.34, 0.56, ...]                     │
│   2. 向量相似度搜索 (Chroma/Milvus)                                   │
│   3. Top-K 筛选（返回相似度最高的K条文档）                            │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                          检索结果示例                                  │
│   [                                                                    │
│     {"content": "推拿是一种传统中医疗法，通过手法按摩经络穴位...",      │
│      "category": "service_info", "score": 0.892},                    │
│     {"content": "推拿可以促进血液循环，缓解肌肉紧张和疼痛...",         │
│      "category": "health_benefits", "score": 0.856},                 │
│     {"content": "定期推拿有助于放松身心，减轻压力和焦虑...",            │
│      "category": "health_benefits", "score": 0.823}                  │
│   ]                                                                    │
└───────────────────────────────────────────────────────────────────────┘
```

### 咨询完整流程

```
用户输入: "推拿有什么好处？"
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      ConsultationClassifier.is_consultation_related()   │
│   ├── related    → 继续咨询流程                                        │
│   └── unrelated  → 转交给TaskClassificationAgent                       │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      知识检索 (KnowledgeRetriever)                      │
│   knowledge_retriever.search_knowledge(query, top_k=3)                 │
│   - 文本嵌入 → 向量相似度搜索 → 返回相关文档                           │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      响应生成 (ResponseGenerator)                      │
│   build_consultation_prompt(user_input, knowledge_docs, memory_context) │
│   - 系统角色设定 + 用户问题 + 检索知识 + 反思洞察                       │
│   - LLM 生成流式响应                                                  │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      行为记录 (ConsultationProcessor)                  │
│   _record_consultation_behavior()                                      │
│   - question, doc_scores, max_score, categories                       │
└───────────────────────────────────────────────────────────────────────┘
```

- 涉及文件：`agents/consultant_agent.py`、`agents/consultant/knowledge_retriever.py`、`agents/consultant/consultation_processor.py`、`agents/consultant/response_generator.py`、`services/knowledge_service.py`、`services/text_embedding.py`
- 记录时间：2026-07-02

---

## D3: 预约流程

**Q: AppointmentAgent的完整工作流程是怎样的？**

**A:**

### 预约完整流程图

```
用户输入: "预约今天下午3点的推拿，1小时，要女技师"
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      阶段1: 输入解析 (InputParser)                     │
│   LLM Prompt 提取字段:                                                │
│   - gender: "女"                                                     │
│   - start_time: "2026-07-02 15:00"                                   │
│   - duration: "60分钟"                                                │
│   - project: "推拿"                                                   │
│   - info_complete: true/false                                         │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      决策点: unrelated? / info_complete?              │
│   ├── unrelated=True → 转交给TaskClassificationAgent                   │
│   ├── info_complete=False → handle_incomplete_info() (询问缺失信息)    │
│   └── info_complete=True → handle_complete_appointment()              │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                 阶段2: 技师查找 (TechnicianFinder)                     │
│   ├── 指定技师? → find_specific_technician() → 检查档期               │
│   │                └── 不可用 → find_similar_available_technician()   │
│   └── 通用查找 → filter_technicians_by_gender()                       │
│                  → filter_technicians_by_preference()                 │
│                  → find_available_technician()                         │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                 阶段3: 预约响应与保存                                   │
│   ├── requires_confirmation=True → 推荐技师，等待用户确认               │
│   ├── confirmed_technician → 保存预约到数据库                          │
│   └── tech is None → 返回预约失败消息                                  │
└───────────────────────────────────────────────────────────────────────┘
    │
    v
┌───────────────────────────────────────────────────────────────────────┐
│                      阶段4: 消息生成 (MessageBuilder)                  │
│   ├── create_appointment_success_message()                             │
│   ├── create_technician_recommendation_message() (LLM驱动)           │
│   ├── create_appointment_failure_message()                             │
│   └── create_missing_info_questions()                                 │
└───────────────────────────────────────────────────────────────────────┘
```

### appointment_history 状态演变

```
初始: {gender, start_time, duration, project, preference, technician_name} = None

轮次1: 用户: "我想预约推拿"
    → {project: "推拿", ...}
    → 响应: 询问时间、时长、技师性别

轮次2: 用户: "今天下午3点，1小时，要女技师"
    → {gender: "女", start_time: "2026-07-02 15:00", duration: "60", project: "推拿"}
    → finished = True → 执行预约 → reset() → 回到初始状态
```

- 涉及文件：`agents/appointment_agent.py`、`agents/appointment/input_parser.py`、`agents/appointment/technician_finder.py`、`agents/appointment/appointment_processor.py`、`agents/appointment/message_builder.py`、`agents/appointment/appointment_database.py`
- 记录时间：2026-07-02

---

## D4: Agent记忆系统

**Q: 记忆系统是如何工作的？**

**A:**

### 记忆架构

```
MemoryManager 架构:

┌─────────────────────────────────────────────────────────────────────┐
│                        MemoryManager                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    ConversationMemory                          │  │
│  │  - 对话历史 (session_id → messages)                           │  │
│  │  - 当前会话摘要                                              │  │
│  │  - 上下文压缩阈值                                            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              ↓                                       │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    SemanticMemory                              │  │
│  │  - 用户画像 (偏好、习惯)                                      │  │
│  │  - 语义记忆 (关键对话要点)                                    │  │
│  │  - LLM 摘要生成                                              │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 记忆流程

```
用户消息 → add_user_message()
    │
    ├─> 存入 ConversationMemory (messages)
    ├─> LLM 提取语义记忆 → update_semantic_memory()
    └─> 检查上下文压缩 → compress_history_if_needed()

Agent响应 → add_assistant_message()
    │
    └─> 存入 ConversationMemory (messages)

获取上下文 → get_context_for_agent()
    │
    ├─> ConversationMemory.get_history() → 对话历史
    └─> SemanticMemory.get_summary() → 用户画像
```

- 涉及文件：`services/memory_manager.py`、`services/conversation_memory_service.py`、`services/semantic_memory_service.py`、`api/memory.py`
- 记录时间：2026-07-02

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

### Q: 这个项目用到了 harness 吗，哪些地方用到了 react 的工作方式

**A:** 项目没有使用 Harness，也没有使用传统 ReAct 模式。项目里真正具备"思考/改进"能力的是 Reflection（反思）闭环系统。

**Harness：** 全仓库检索后未发现 harness 相关依赖或配置。项目是一个完整的业务系统，没有引入 Harness 框架。

**ReAct：** 项目没有实现 Thought → Action → Observation 的 ReAct 循环。传统 ReAct 常见于工具调用/搜索类 Agent，而本项目是业务流程型 Agent。

**项目中的"思考"方式： Reflection 反思闭环**

本质上是任务完成后的评估 → 分析 → 策略更新 → 效果验证 → 策略应用。

触发时机：任务完成后，由 `chat_handler` 在响应返回后通过 `asyncio.create_task` 静默触发，不阻塞用户响应。

触发阈值：`success_rate < 0.7` / `轮数 > 10` / `完成时间 > 120s`。

核心组件：
- `ReflectionAwareMixin` — 让 Agent 能查询反思洞察
- `ReflectionEngine` — 协调评估、分析、报告
- `TaskEvaluator` — 任务完成后评估
- `ReflectionAnalyzer` — 分析失败根因
- `StrategyUpdater` — 基于洞察更新策略

与 ReAct 的核心区别：

| 特性 | ReAct | 本项目的反思机制 |
|------|-------|-----------------|
| 核心思想 | Thought-Action-Observation 循环 | Task-Evaluate-Insight 闭环 |
| 触发时机 | 每步决策前 | 任务完成后 |
| 关注点 | 当前步的推理 | 整体任务的效果分析 |
| 记忆 | 短时记忆链 | 长期记忆数据库 |
| 适用场景 | 工具调用/搜索 | 业务流程优化 |

本质区别：ReAct 侧重于推理过程，反思机制侧重于结果分析。两者都是让 Agent 具备"思考"能力，只是角度不同。

- 涉及文件：`agents/reflection/reflection_aware.py`、`agents/reflection/engine.py`、`agents/reflection/evaluator.py`、`agents/reflection/analyzer.py`、`agents/reflection/strategy_updater.py`、`api/chat_handler.py`
- 记录时间：2026-07-01

---


## D6: 用户行为分析

### Q: 用户行为模块的内容是什么？它是怎么和其他模块协作的？

**A:**

**模块架构：**

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户行为模块                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    UserBehaviorAgent (Agent层)                   │  │
│  │  - 入口类，协调各组件                                            │  │
│  │  - 生成个性化回访提醒                                            │  │
│  │  - 获取用户分析数据                                              │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                      Service 层                                │  │
│  │  ┌──────────────────┐ ┌──────────────────┐ ┌───────────────┐  │  │
│  │  │ BehaviorRecorder │ │ PatternAnalyzer   │ │PreferenceMgr  │  │  │
│  │  │ (行为记录器)       │ │ (行为模式分析)    │ │(偏好管理器)   │  │  │
│  │  └──────────────────┘ └──────────────────┘ └───────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                 UserBehaviorService (业务逻辑层)                │  │
│  │  - 封装 Repository 调用                                        │  │
│  │  - 提供业务分析方法                                            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │              UserBehaviorRepository (数据访问层)                │  │
│  │  - SQLite 数据库操作                                           │  │
│  │  - CRUD 封装                                                   │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**核心组件：**

1. **UserBehaviorAgent（入口）**

`agents/user_behavior_agent.py`

```python
class UserBehaviorAgent:
    def record_behavior(action_type, action_data, ...)  # 记录行为
    def get_user_analysis(user_id)                      # 获取分析
    def generate_personalized_reminder(user_id)         # 生成提醒
    def get_reminder_with_schedule(user_id)             # 带时间的提醒
```

2. **BehaviorRecorder（行为记录器）**

`agents/user_behavior/behavior_recorder.py`

```python
class BehaviorRecorder:
    def record_behavior(...)              # 通用记录
    def record_appointment_behavior(...) # 预约记录
    def record_consultation_behavior(...)# 咨询记录
    def get_user_behaviors(...)          # 查询行为
```

3. **PatternAnalyzer（模式分析器）**

`agents/user_behavior/pattern_analyzer.py`

```python
class PatternAnalyzer:
    def analyze_user_preferences(user_id)     # 分析偏好
    def should_send_return_reminder(user_id)  # 判断是否回访
    def generate_return_message(user_id)       # 生成回访消息
```

4. **PreferenceManager（偏好管理器）**

`agents/user_behavior/preference_manager.py`

```python
class PreferenceManager:
    def update_preferences_from_appointment(data, tech_id)  # 更新偏好
    def update_technician_preference(tech_id)              # 技师偏好
    def update_time_preference(start_time)                 # 时间偏好
    def update_duration_preference(duration)               # 时长偏好
    def get_preference_summary()                            # 偏好摘要
```

**数据模型：**

```sql
-- user_behaviors 表
CREATE TABLE user_behaviors (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    action_type TEXT,          -- 'appointment' / 'consultation'
    action_data TEXT,           -- JSON 存储详细信息
    technician_id INTEGER,
    session_id TEXT,
    created_at TIMESTAMP
);

-- user_preferences 表
CREATE TABLE user_preferences (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    preference_type TEXT,      -- 'technician' / 'time_period' / 'duration' / 'service'
    preference_value TEXT,
    confidence_score INTEGER DEFAULT 1,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- user_recommendations 表
CREATE TABLE user_recommendations (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    recommendation_type TEXT,
    content TEXT,
    technician_id INTEGER,
    is_sent INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    sent_at TIMESTAMP
);
```

**与其他模块的协作：**

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           完整协作流程                                    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. 预约流程 → 记录预约行为                                               │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐    │
│  │ AppointmentAgent│ ──▶ │AppointmentDB    │ ──▶ │UserBehaviorService│    │
│  │ (预约代理)      │     │ (数据库操作)    │     │ (记录行为)       │    │
│  └─────────────────┘     └─────────────────┘     └─────────────────┘    │
│                                                        ↓                 │
│                              action_data = {                             │
│                                  'start_time': ...,                      │
│                                  'duration': 60,                         │
│                                  'project': '推拿',                     │
│                                  'technician_id': 1                      │
│                              }                                          │
│                                                                          │
│  2. 咨询流程 → 记录咨询行为                                               │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐    │
│  │ConsultantAgent  │ ──▶ │ConsultationProc │ ──▶ │UserBehaviorAgent│    │
│  │ (咨询代理)      │     │ (咨询处理)      │     │ (记录行为)       │    │
│  └─────────────────┘     └─────────────────┘     └─────────────────┘    │
│                                                        ↓                 │
│                              action_data = {                             │
│                                  'question': ...,                         │
│                                  'doc_scores': [0.92, 0.85],            │
│                                  'response_content': ...,                │
│                                  'user_id': ...                          │
│                              }                                          │
│                                                                          │
│  3. 回访提醒 → 主动触达用户                                               │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐  │
│  │ 定时任务/外部调用│ ──▶ │UserBehaviorAgent│ ──▶ │ Recommendation   │  │
│  │                 │     │ (生成提醒)       │     │ (推荐消息)        │  │
│  └─────────────────┘     └─────────────────┘     └─────────────────┘  │
│                                   ↓                                       │
│                         ┌─────────────────┐                             │
│                         │ PatternAnalyzer  │                             │
│                         │ (分析用户偏好)   │                             │
│                         └─────────────────┘                             │
│                                   ↓                                       │
│                         "尊敬的Tom，{技师名}最近有空档，                  │
│                          您之前很喜欢他/她的{服务}服务..."                │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**协作场景示例：**

**场景1：用户完成预约**

```python
# 1. AppointmentAgent 处理预约
appointment_data = {
    'start_time': '2026-06-30 14:00',
    'end_time': '2026-06-30 15:00',
    'duration': 60,
    'project': '全身按摩',
    'technician_id': 5
}

# 2. 自动记录用户行为
user_behavior_service.record_behavior(
    user_id='user123',
    action_type='appointment',
    action_data=appointment_data,
    technician_id='5',
    session_id='user123-session001'
)

# 3. PreferenceManager 更新偏好
preference_manager.update_preferences_from_appointment(
    action_data=appointment_data,
    technician_id=5
)
# → 用户偏好：技师=5, 时间段=下午, 时长=60分钟, 服务=全身按摩
```

**场景2：用户咨询后**

```python
# 1. ConsultationProcessor 处理咨询
knowledge_docs = await retriever.search('营业时间')

# 2. 记录咨询行为（包含RAG评估数据）
behavior_agent.record_behavior(
    action_type='consultation',
    action_data={
        'question': '你们几点开门?',
        'doc_scores': [0.923, 0.856],  # 相似度分数
        'doc_ids': [1, 3],              # 文档ID
        'user_id': 'user123',           # 用户关联
        'response_content': '我们的营业时间是...'
    },
    session_id='user123-session001'
)
```

**场景3：生成回访提醒**

```python
# 1. 判断是否需要回访
if pattern_analyzer.should_send_return_reminder('user123'):
    # 用户上次预约是35天前，超过30天阈值

    # 2. 获取用户偏好
    preferences = pattern_analyzer.analyze_user_preferences('user123')
    # → favorite_technician=5, favorite_service='全身按摩',
    #    favorite_duration=60

    # 3. 获取技师空闲时间
    available_times = technician_db.get_available_slots(tech_id=5)
    # → [{'time': '今天15:00'}, {'time': '明天10:00'}]

    # 4. 生成个性化消息
    message = await agent.generate_personalized_reminder(
        user_id='user123',
        available_times=available_times
    )
    # → "尊敬的Tom，您好！张技师最近有空档，
    #     您之前很喜欢他/她的全身按摩服务。
    #     按您习惯的60分钟，今天15:00有空，
    #     要不要预约一下放松一下？"
```

**API 接口：**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/user_behavior/analysis` | GET | 获取用户分析数据 |
| `/api/user_behavior/dashboard_data` | GET | 仪表板数据 |
| `/api/user_behavior/send-reminder` | POST | 发送回访提醒 |

```bash
# 获取用户分析
curl http://localhost:8000/api/user_behavior/analysis

# 响应
{
    "favorite_technician_id": 5,
    "favorite_technician_name": "张技师",
    "favorite_service": "全身按摩",
    "favorite_duration": 60,
    "total_appointments": 8,
    "days_since_last_appointment": 35,
    "should_send_reminder": true
}
```

**关键设计特点：**

1. **双重记录机制**：预约和咨询分别记录，但共享同一套数据存储
2. **偏好置信度**：同一种偏好多次出现时增加置信度
3. **回访时机判断**：基于历史预约间隔自动判断是否需要回访
4. **个性化消息**：结合用户偏好和技师空闲时间生成定制化内容
5. **向后兼容**：Service 层和 Repository 层都有完整实现，便于迁移

- 涉及文件：`agents/user_behavior_agent.py`、`agents/user_behavior/behavior_recorder.py`、`agents/user_behavior/pattern_analyzer.py`、`agents/user_behavior/preference_manager.py`、`services/user_behavior_service.py`、`api/user_behavior_analysis.py`、`agents/appointment/appointment_database.py`、`agents/consultant/consultation_processor.py`
- 记录时间：2026-07-01

---

### Q: 这个项目的自动进化过程有哪些？是怎么样的？

项目实现了一套完整的**反思闭环（Reflection Loop）**自动进化机制，分为 6 个核心环节：

#### 1. 触发机制（3 种触发方式）

| 触发方式 | 来源 | 触发条件 |
|----------|------|---------|
| **对话结束自动触发** | `chat_handler.py` | 每次对话结束静默触发，`asyncio.create_task` 不阻塞用户 |
| **负面反馈触发** | `reflection_agent.py` | 用户投诉/修正/评分≤2 时立即触发 |
| **定时闭环触发** | `app.py` | 每 6 小时执行一次完整闭环周期 |

#### 2. 评估环节（TaskEvaluator）

每次对话结束后，对任务进行多维度评估：

```
评估维度：
├─ success_rate（成功率） < 0.7  → 触发反思
├─ turns_count（对话轮数） > 10  → 触发反思
├─ completion_time（耗时） > 120s → 触发反思
└─ error_type（错误类型）       → 分类记录
```

#### 3. 分析环节（ReflectionAnalyzer）

**Agent 驱动分析**（混合模式）：样本量 >= 5 时使用 LLM 深度分析，否则用规则引擎 fallback。

#### 4. 策略生成环节（StrategyUpdater）

**Agent 驱动策略生成**：根据洞察数据生成策略配置（matching/recommendation/routing/prompt/timeout），自动激活新策略。

#### 5. 效果验证环节（ClosedLoopEvaluator）

这是**自动回滚**的关键：新策略启用后数据 >= 10 条时，对比前后成功率。
- **IMPROVED**（提升 ≥5%，置信度 ≥0.95）→ 保持策略
- **DEGRADED**（下降 ≥10%）→ 自动回滚
- **NO_CHANGE** → 继续观察

#### 6. 知识注入环节（ReflectionAwareMixin）

反思洞察实时注入到 Agent 决策中：检查当前动作是否匹配已知坏 case，应用改进建议和策略配置。

**进化效果：** 自动发现问题 → 自动生成对策 → 自动验证效果 → 自动回滚 → 自动学习（坏 case 警告）→ 周期性优化（每 6 小时）

- 涉及文件：`agents/reflection/analyzer.py`、`agents/reflection/strategy_updater.py`、`agents/reflection/engine.py`、`agents/reflection/evaluator.py`、`agents/reflection/closed_loop_evaluator.py`、`agents/reflection/reflection_aware.py`、`services/reflection_service.py`、`api/chat_handler.py`、`app.py`
- 记录时间：2026-07-02

---

## D7: 数据库层

（暂无叶子记录）

---

## D8: API与配置

（暂无叶子记录）

---

## 附录: 数据流向总览

**Q: 完整的用户请求处理路径是怎样的？**

**A:**

```
用户消息
    │
    ├──────────────────────────────────────────────────────────────────┐
    │                                                                  │
    v                                                                  │
┌────────────────────────────────────────────────────────────────────┐│
│                      MemoryManager (记忆管理)                         ││
│  add_user_message()                                                 ││
│  ├── 存储对话历史                                                   ││
│  ├── 提取语义记忆                                                   ││
│  └── 检查上下文压缩                                                 ││
└────────────────────────────────────────────────────────────────────┘│
    │                                                                 │
    v                                                                 │
┌────────────────────────────────────────────────────────────────────┐│
│                 TaskClassificationAgent (分类)                       ││
│  classify_task_stream()                                             ││
│  └── 输出分类结果 (appointment/query/other)                         ││
└────────────────────────────────────────────────────────────────────┘│
    │                                                                 │
    ├────────────────────────┬────────────────────────────────────────┤
    │                        │                                        │
    v                        v                                        v
┌────────────────┐  ┌────────────────┐  ┌────────────────────────────────┐
│ AppointmentAgent│  │ConsultantAgent│  │  (其他Agent/拒绝回复)          │
│                │  │                │  │                                │
│ 组件:          │  │ 组件:          │  └────────────────────────────────┘
│ - InputParser  │  │ - KnowledgeRetriever
│ - TechnicianFinder│ │ - ConsultationClassifier
│ - MessageBuilder │ │ - ResponseGenerator
│ - AppointmentDatabase│
│                │  │
│ 输出:预约结果   │  │ 输出:咨询响应
└────────────────┘  └────────────────┘
    │                        │
    ├────────────────────────┤
    │                        │
    v                        v
┌────────────────────────────────────────────────────────────────────┐│
│                    MemoryManager (响应存储)                          ││
│  add_assistant_message()                                            ││
└────────────────────────────────────────────────────────────────────┘│
    │                                                                 │
    v                                                                 │
┌────────────────────────────────────────────────────────────────────┐│
│                    TaskMeta 更新                                    ││
│  - task_type: appointment/consultation                             ││
│  - turn_count: +1                                                  ││
│  - appointment_history / consultation_data                          ││
└────────────────────────────────────────────────────────────────────┘│
    │                                                                 │
    v                                                                 │
┌────────────────────────────────────────────────────────────────────┐│
│                 异步触发反思 (静默)                                 ││
│  _trigger_reflection() → ReflectionAgent.reflect_on_*()          ││
└────────────────────────────────────────────────────────────────────┘│
    │                                                                 │
    v                                                                 │
┌────────────────────────────────────────────────────────────────────┐│
│                 反思洞察 → Agent (下一轮)                           ││
│  AppointmentAgent.apply_insights()                                 ││
│  ConsultantAgent.apply_insights()                                   ││
└────────────────────────────────────────────────────────────────────┘
```

- 涉及文件：`api/chat_handler.py`、`services/memory_manager.py`、`agents/task_classification_agent.py`、`agents/appointment_agent.py`、`agents/consultant_agent.py`、`agents/reflection_agent.py`
- 记录时间：2026-07-02

---
