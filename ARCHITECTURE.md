# Smart Appointment AI Agent — 架构总览

> 给面试官看的 1 页架构图。重点：**数据流** + **闭环** + **关键工程决策**。

## 1. 五层架构（自顶向下）

```
┌─────────────────────────────────────────────────────────────────────────┐
│  L1  API Layer          FastAPI · WebSocket                              │
│      api/chat_handler.py / app.py                                        │
├─────────────────────────────────────────────────────────────────────────┤
│  L2  Agent Layer        5 Agents · 状态机 · 路由                          │
│      agents/                                                            │
│      ├── task_classification_agent.py    (归类机器人)                    │
│      ├── appointment_agent.py             (预约机器人)                   │
│      ├── consultant_agent.py              (咨询机器人)                   │
│      ├── user_behavior_agent.py           (行为模式)                     │
│      └── reflection_agent.py              (反思·闭环核心)                │
├─────────────────────────────────────────────────────────────────────────┤
│  L3  Service Layer      业务服务                                          │
│      services/                                                          │
│      ├── knowledge_service.py            (RAG: FAISS + LangChain)       │
│      ├── text_embedding.py                (embedding cache)             │
│      ├── reflection_service.py            (反思编排)                     │
│      ├── memory_service.py                (长期记忆)                     │
│      └── evaluation_service.py            (评估落库)                     │
├─────────────────────────────────────────────────────────────────────────┤
│  L4  Repository Layer   数据访问                                          │
│      db/repositories/                                                    │
│      ├── appointment_repository.py                                     │
│      ├── knowledge_repository.py                                       │
│      ├── reflection_repository.py        (含 StrategyRepository)         │
│      ├── user_behavior_repository.py                                    │
│      └── evaluation_repository.py                                      │
├─────────────────────────────────────────────────────────────────────────┤
│  L5  DB Layer           SQLite + 表                                      │
│      data/smart_appointment.db                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## 2. 核心数据流：用户预约一句话的全链路

```
  用户: "明天下午3点男技师手劲大的，1小时"
   │
   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ chat_handler.process_user_input_stream(user_id, msg)                   │
│   ├─ memory_manager.add_user_message(user_id, msg)   # 写入工作记忆     │
│   └─ task_classification_agent.classify_task_stream(msg)               │
└────────────────────────────────────────────────────────────────────────┘
   │ classify: appointment
   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ TaskClassificationAgent                                                 │
│   ├─ TaskClassifier.classify_task()       # LLM 5 类分类               │
│   ├─ StateManager.transition_to_appointment()                           │
│   └─ AgentRouter.route_to_appointment()                                │
└────────────────────────────────────────────────────────────────────────┘
   │
   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ AppointmentAgent.run_stream()                                           │
│                                                                        │
│  ┌────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │InputParser │→ │TechnicianFinder │→ │AppointmentProcessor          │  │
│  │(LLM JSON)  │  │(硬筛 + 软重排) │  │(确认 + 存库 + 提示词注入)   │  │
│  └────────────┘  └─────────────────┘  └─────────────────────────────┘  │
│        ▲                ▲                       ▲                     │
│        │                │                       │                     │
│   注入坏案例         注入反思patterns        注入recommendations        │
│   (闭环1)            (闭环2, MAX=0.3)        (闭环3)                    │
└────────────────────────────────────────────────────────────────────────┘
   │
   ▼
  数据库写入 → 用户确认 → 流式回复 → 触发评估 → 反思调度
```

## 3. 反思（Reflection）闭环：6 组件数据流

```
┌────────────────────────────────────────────────────────────────────────┐
│                        ReflectionEngine                                │
│                                                                        │
│   TaskEvaluator ──→ ReflectionAnalyzer ──→ ReflectionReporter          │
│        │                                       │                      │
│        │ should_reflect?                        │                      │
│        ▼                                       ▼                      │
│   ReflectionContextProvider ←─── ReflectionStrategyUpdater            │
│        ▲                                       │                      │
│        │                                       │                      │
│        │ reflect_on_task()                      │                      │
│        │                                       ▼                      │
│        │                              ClosedLoopEvaluator              │
│        │                              (A/B + p-value + 置信度)         │
│        │                                       │                      │
│        │                                       ▼                      │
│        │                              activate / rollback              │
└────────────────────────────────────────────────────────────────────────┘
```

**触发时机**（`engine.py:142`）：
- `should_reflect` 判定：success_rate<0.8 || turns>10 || time>120s || SuccessLevel.FAILED

**超时保护**（`engine.py:178-200`）：每个 analyzer 调用裹 `asyncio.wait_for(30s)`，超时降级返回空 pattern，**反思链路不阻塞主对话**。

## 4. 三层闭环注入（"开环 → 闭环" 路径）

| 闭环 | 注入点 | 数据源 | 形式 | 安全阀 |
|---|---|---|---|---|
| 闭环 1 | `InputParser` prompt | `ReflectionLog.bad_cases[:5]` | 自然语言 few-shot 注入 | 空集合 → prompt 不变 |
| 闭环 2 | `TechnicianFinder` score | `patterns_discovered` | `final = base + boost` | `_MAX_REFLECTION_BOOST = 0.3` |
| 闭环 3 | AppointmentProcessor system prompt | `ReflectionInsight.recommendations` | prompt 段拼接 | 优先级 hint，非硬覆盖 |

## 5. 关键技术决策（面试可讲）

### 5.1 状态机（`state_manager.py`）
3 态：`CLASSIFY / APPOINTMENT / CONSULT`
**白名单转换**：`APPOINTMENT → CLASSIFY`（不允许 `APPOINTMENT → CONSULT`，保护预约 context 完整性）。

### 5.2 防循环路由（已修复 bug）
原 `handle_unrelated` 递归调用分类器，导致 `分类 → appointment → unrelated → 再分类` 无限循环。
**修复**：unrelated_callback **不再调分类器**，直接 yield 礼貌拒绝模板。

### 5.3 RAG 召回-精排分离（`knowledge_service.py:170-192`）
- `IndexFlatIP`（内积 = 余弦）
- FAISS 取 `top_k * 2` 候选 → `category` 后置过滤 → 截断到 top_k
- 解决小类别被漏召的问题

### 5.4 闭环安全阀（`technician_finder.py:107`）
```python
_MAX_REFLECTION_BOOST = 0.3  # 防止反思模式绑架业务排序
```

### 5.5 统计显著性验证（`closed_loop_evaluator.py:72-78`）
```python
config = {
    'min_sample_size': 10,           # 样本不足 → INSUFFICIENT_DATA
    'improvement_threshold': 0.05,   # <5% 不上线
    'degradation_threshold': 0.10,   # 降 10% 立即回滚
    'confidence_level': 0.95,
    'evaluation_window_days': 7
}
```
不对称阈值（改进 5%、降级 10%）——**降级容忍度更低**。

## 6. 项目体量速览

| 指标 | 数值 |
|---|---|
| Python 源码 | ~26.6k 行 |
| 测试代码 | ~5.2k 行 |
| 模块文件 | ~117 个 |
| LLM 调用点数 | 12+ |
| Agent 数 | 5 |
| RAG 组件 | 4（Embedding / Index / Search / Cache） |
| 反思组件 | 6（Evaluator / Analyzer / Reporter / Context / Strategy / ClosedLoop） |

## 7. 仓库结构

```
smart-appointment-ai-agent/
├── agents/
│   ├── appointment/           # 预约子模块
│   │   ├── input_parser.py
│   │   ├── technician_finder.py
│   │   ├── appointment_processor.py
│   │   └── message_builder.py
│   ├── task_classification/   # 任务分类子模块
│   ├── reflection/            # 反思闭环子模块（6 组件）
│   └── user_behavior/         # 用户行为分析子模块
├── services/                  # 业务服务层
├── db/
│   ├── repositories/          # 数据访问层
│   └── db_router.py           # 多库路由
├── api/                       # WebSocket / REST 入口
├── config/                    # 模型 / 常量 / 时间
├── eval/                      # 离线评估
├── tests/                     # 测试
├── examples/                  # Demo / 集成示例
└── data/                      # SQLite DB + 缓存
```
