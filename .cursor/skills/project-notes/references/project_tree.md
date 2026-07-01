# 项目模块树 — 主干（基础知识）

> 这棵树的每个节点都是模块的主干。叶子（Q&A）记录在 `notes.md` 中。
> 模块按深度编号：D0=架构总览，D1=多Agent，D2=RAG，D3=预约，D4=记忆，D5=反思，D6=行为，D7=DB，D8=API。

---

## D0: 架构总览

**核心定位：** 整个系统是一个基于多Agent架构的按摩房智能预约系统，用户可以通过对话完成预约和咨询两类任务。

**系统分层：**
```
用户输入 → TaskClassificationAgent（路由） → AppointmentAgent | ConsultantAgent
                    ↓
              MemoryManager（记忆层）
                    ↓
              ReflectionAgent（反思层）
                    ↓
              数据库（SQLite）
```

---

## D1: 多Agent架构

**文件位置：** `agents/`
**作用：** 将对话任务按类型分发到对应专用Agent，避免单一Agent负担过重。
**在架构中的位置：** 最上层入口，用户所有对话首先经过 TaskClassificationAgent 做意图分类，再路由到对应Agent。

### D1.1 TaskClassificationAgent（任务分类路由器）

**文件：** `agents/task_classification_agent.py`，`agents/task_classification/`

**核心组件：**
- `task_classifier.py` — LLM判断用户意图（预约/咨询/其他）
- `agent_router.py` — 根据分类结果路由到对应Agent
- `state_manager.py` — 管理对话状态机（CLASSIFY / APPOINTMENT / CONSULTATION）
- `classification_processor.py` — 分类逻辑编排

**路由决策：** `is_appointment_related`（YES/NO二分类）→ 走预约；`is_consultation_related` → 走咨询；均不满足 → `other`

---

## D2: RAG知识检索

**文件位置：** `services/knowledge_service.py`，`agents/consultant/`
**作用：** 从按摩房知识库中检索相关信息，辅助ConsultantAgent回答用户关于服务、价格、地址等咨询问题。
**在架构中的位置：** ConsultantAgent 的底层支撑，提供事实依据。

**核心组件：**
- `knowledge_service.py` — FAISS向量索引 + SQLite存储，支持增删改查
- `knowledge_retriever.py` — 封装检索调用，过滤+排序
- `text_embedding.py` — `embed_input()` 调用embedding模型生成向量
- `embedding_matcher.py` — `find_best_match_indices()` 用L2距离在候选技师中匹配

**索引策略：** `IndexFlatIP`（内积相似度），`top_k * 2` 多检候选
**默认知识库：** 10条基础文档（营业时间、价格、技师、地址等）
**兜底：** 检索为空时，system prompt 允许LLM基于专业知识补充回答

---

## D3: 预约流程

**文件位置：** `agents/appointment/`，`agents/appointment_agent.py`
**作用：** 引导用户完成预约全流程——收集时间、项目、技师等槽位信息，匹配可用技师，保存预约。
**在架构中的位置：** TaskClassificationAgent 将预约意图路由到此处，是系统核心业务功能。

**核心组件：**
- `input_parser.py` — 用LLM从用户输入中解析结构化槽位（gender/time/duration/project/technician_name）
- `technician_finder.py` — 按偏好匹配可用技师，支持指定技师和智能推荐
- `appointment_processor.py` — 流程编排（补全槽位 → 推荐技师 → 确认 → 保存）
- `appointment_database.py` — 预约持久化到DB
- `message_builder.py` — 生成各类提示信息（成功/失败/追问）

**槽位状态机：** `start_time` / `project` / `duration` → 全部填满 → 技师匹配 → 用户确认 → 落库
**推荐逻辑：** 用户指定技师不可用时，从同性别、高评分技师中推荐替代方案
**兜底：** JSON解析失败时返回全"未知"，触发追问；天气API失败时返回内置假数据

---

## D4: Agent记忆系统

**文件位置：** `services/memory_manager.py`，`services/conversation_memory_service.py`，`services/semantic_memory_service.py`
**作用：** 三层记忆架构，支撑多轮对话的上下文管理、用户偏好提取与跨轮推荐。
**在架构中的位置：** MemoryManager 贯穿每个Agent调用，add_user_message时自动提取语义记忆，prepare_context时注入用户画像。

**三层记忆：**

| 层级 | 模型类 | 存储内容 | 对应人类记忆 |
|------|--------|---------|------------|
| 工作记忆+情景记忆 | `ConversationMessage` | 每轮对话原始记录 | 情景记忆（Episodic） |
| 语义记忆 | `SemanticMemory` | 用户偏好/关键事实 | 语义记忆（Semantic） |
| 压缩摘要 | `SessionSummary` | 旧消息LLM压缩摘要 | 情景记忆压缩版 |

**Token计数：** 中文 `字符数`，英文 `word数 * 1.3`，RESERVE_RATIO=0.85
**压缩策略：** max=6000 tokens，触发阈值=4800，压缩后保留1200 tokens最近消息
**语义提取：** `SemanticExtractor` 用正则从用户输入提取偏好，`confidence` 字段记录出现次数
**置信度：** avoid_technician=2（负向偏差），普通偏好=1，boost_confidence增加2，decay机制衰减过期偏好
**Decay bug：** `_apply_confidence_decay` 只改内存对象，未写回DB

---

## D5: 反思与评估闭环

**文件位置：** `agents/reflection/`，`agents/reflection_agent.py`
**作用：** 每次任务执行后评估质量，发现失败模式，生成改进建议，动态更新Agent策略。
**在架构中的位置：** 独立运行层，不直接参与用户对话，而是周期性分析历史数据，生成策略供Agent查询。

**核心组件：**
- `evaluator.py` — `TaskEvaluator`，评估任务成功/部分成功/失败，写入评估记录
- `analyzer.py` — `ReflectionAnalyzer`，分析失败任务根因、用户行为模式
- `strategy_updater.py` — `StrategyUpdater`，根据反思结果生成/激活/回滚Agent策略
- `engine.py` — 反思引擎，协调评估→分析→策略更新全流程
- `reflection_aware.py` — `ReflectionAwareMixin`，让Agent可查询和应用反思洞察

**评估指标：** `SuccessLevel`（FAILED=0 / PARTIAL=1 / SUCCESS=2），`_should_trigger_reflection` 阈值（成功率<0.7、轮数>10、完成时间>120s）
**策略类型：** MATCHING / RECOMMENDATION / ROUTING / PROMPT / TIMEOUT
**兜底：** `_reflection_engine` 不可用时返回默认洞察，策略系统有默认初始化配置

---

## D6: 用户行为分析

**文件位置：** `agents/user_behavior/`，`agents/user_behavior_agent.py`，`services/user_behavior_service.py`
**作用：** 记录用户对话行为，分析偏好模式，为推荐系统提供数据支撑。
**在架构中的位置：** 与记忆系统并行，行为记录为反思层提供数据源。

**核心组件：**
- `behavior_recorder.py` — `BehaviorRecorder`，记录行为事件到DB
- `pattern_analyzer.py` — 从历史行为中发现用户偏好模式（技师/时间/项目）
- `preference_manager.py` — 管理用户偏好数据
- `user_behavior_service.py` — 行为分析服务层，封装业务逻辑
- `user_behavior_agent.py` — Agent入口，提供 `record_behavior()` 接口

**记录内容：** 咨询类问题检索条数/categories、预约成功/失败原因、槽位补全效率

---

## D7: 数据库层

**文件位置：** `db/`
**作用：** 提供所有数据的持久化存储（SQLite），Repository模式隔离数据访问。
**在架构中的位置：** 所有上层组件的数据底座，通过 `DatabaseRouter` 统一访问。

**核心组件：**
- `db_router.py` — `DatabaseRouter`，统一入口，按表路由到对应repository
- `session_manager.py` — SQLAlchemy Session 管理，处理连接池和事务
- `models.py` — SQLAlchemy ORM 模型（Appointment/Technician等业务表）
- `models_memory.py` — 记忆系统模型（ConversationMessage/SemanticMemory/SessionSummary）

**Repositories：** `technician_repository.py`、`memory_repository.py`、`knowledge_repository.py`、`reflection_repository.py`、`user_behavior_repository.py`
**会话模式：** 每个repo方法用 `with self._sm.session_scope()` 管理session，超时自动回滚

---

## D8: API与配置

**文件位置：** `api/`，`config/`，`app.py`
**作用：** HTTP层暴露REST接口，配置层管理LLM/API密钥等运行时参数。
**在架构中的位置：** 最外层，负责与Web客户端或外部系统交互。

**核心组件：**
- `chat_handler.py` — `handle_chat()` 统一对话入口，协调记忆→分类→Agent调用→返回
- `memory.py` — 记忆管理API（reset/status/context/recommendation）
- `reflection_api.py` — 反思数据API（insights/bad_cases/periodic_report）
- `model_provider.py` — `create_chat_model()` / `create_embedding_model()` 统一LLM创建
- `constants.py` — 状态枚举 `StateEnum`，消息类型常量

**配置来源：** `settings.py` 读 `.env`，所有敏感配置（API key）不硬编码
