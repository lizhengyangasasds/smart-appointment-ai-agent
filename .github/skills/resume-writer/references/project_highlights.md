# 项目技术亮点清单（Smart Appointment AI Agent）

> 从 README、源码和项目包装思路提炼，供简历编写时按需选取。每个亮点附带源码锚点、简历话术方向和可量化角度。

## 亮点 1：中心化 Multi-Agent 编排与任务路由

**源码锚点**：
- `agents/task_classification_agent.py`
- `agents/task_classification/agent_router.py`
- `agents/task_classification/state_manager.py`
- `agents/appointment_agent.py`
- `agents/consultant_agent.py`

**技术要点**：
- `TaskClassificationAgent` 作为统一入口，负责意图识别、状态管理和专用 Agent 调度。
- `AgentRouter` 将任务路由到预约 Agent 或咨询 Agent，并通过共享状态保持多轮上下文。
- 通过流式 token 输出暴露思考过程，提升用户对复杂预约流程的可解释性。

**简历话术方向**：
- "设计中心化 Multi-Agent 编排架构，基于 TaskClassificationAgent 实现预约/咨询任务识别、共享状态管理和专用 Agent 路由，保证用户面对统一入口但内部职责清晰。"
- "将复杂预约请求拆分为意图分类、信息抽取、技师匹配、知识检索和结果汇总，降低单 Agent 上下文膨胀和状态混乱风险。"

**可量化角度**：意图路由准确率、支持任务类型数、首 token 延迟、Agent 切换失败率、端到端成功率。

## 亮点 2：预约状态管理与智能技师匹配

**源码锚点**：
- `agents/appointment/appointment_processor.py`
- `agents/appointment/input_parser.py`
- `agents/appointment/technician_finder.py`
- `agents/appointment/appointment_database.py`
- `services/appointment_service.py`

**技术要点**：
- 预约 Agent 维护项目、时间、时长、技师、性别、力度偏好等结构化状态。
- `TechnicianFinder` 支持指定技师可用性查询、性别过滤、偏好过滤和相似技师兜底推荐。
- 使用 Embedding 相似度对技师专长进行排序，在目标技师不可用时推荐相似且有空档的替代技师。
- 预约成功后记录行为数据，为后续偏好记忆和回访推荐提供数据基础。

**简历话术方向**：
- "实现面向自然语言预约的状态管理与技师匹配流程，抽取时间、时长、服务项目和偏好约束，并结合档期、性别和专长相似度推荐可用技师。"
- "针对指定技师不可用场景，基于技师专长 Embedding 相似度提供替代推荐，提升预约完成率和用户体验。"

**可量化角度**：预约信息抽取完整率、技师匹配成功率、替代推荐接受率、重复追问次数、预约完成率。

## 亮点 3：五层分层架构与数据访问边界

**源码锚点**：
- `README.md`
- `api/`
- `agents/`
- `services/`
- `db/db_router.py`
- `db/repositories/`
- `db/models.py`

**技术要点**：
- 项目划分为 Web/Application、API、Agents、Services、DB 五层。
- 通过 `DatabaseRouter` 和 Repository Pattern 统一知识库、技师、用户行为数据访问。
- Agents 层只调用 Services 层，避免绕过业务逻辑直接访问数据库。
- 适合包装为可迁移到医美、理疗、维修、课程预约等本地服务业的业务编排框架。

**简历话术方向**：
- "设计严格五层架构，约束 Web/API/Agents/Services/DB 调用方向，基于 Repository Pattern 隔离数据访问，降低多 Agent 业务系统的耦合度。"
- "将预约、咨询、技师、知识库、用户行为等能力沉淀为可复用服务层，支持后续扩展到多门店或其他本地服务预约场景。"

**可量化角度**：核心模块数、API 数、数据模型数、可复用服务数、回归测试覆盖模块数。

## 亮点 4：RAG 咨询与 FAISS 知识库检索

**源码锚点**：
- `services/knowledge_service.py`
- `agents/consultant/knowledge_retriever.py`
- `api/knowledge.py`
- `db/models.py`

**技术要点**：
- `KnowledgeService` 使用 SQLite 存储知识正文、分类、关键词和 Embedding。
- 基于 FAISS `IndexFlatIP` 构建本地向量索引，支持 Top-K 语义检索和分类过滤。
- 知识增删改后触发索引重建，保证咨询 Agent 使用最新业务知识。
- 默认知识覆盖营业时间、服务项目、技师信息、门店地址、预约政策、会员服务等咨询场景。

**简历话术方向**：
- "构建面向门店咨询场景的轻量 RAG 知识库，基于 SQLite + FAISS 存储服务规则、价格、技师和会员政策，实现服务咨询的语义检索与可更新知识管理。"
- "将咨询 Agent 从模型参数记忆中解耦出来，通过知识库检索提供可信业务上下文，降低规则更新后的回答偏差。"

**可量化角度**：知识条目数、Top-K 命中率、检索延迟、知识更新后索引重建耗时、咨询回答准确率。

## 亮点 5：用户行为记录与长期偏好记忆

**源码锚点**：
- `agents/user_behavior/behavior_recorder.py`
- `agents/user_behavior/pattern_analyzer.py`
- `agents/user_behavior/preference_manager.py`
- `services/user_behavior_service.py`
- `db/models.py`

**技术要点**：
- `BehaviorRecorder` 记录预约、咨询等用户行为及上下文。
- `PreferenceManager` 从预约行为中更新技师、时间段、时长、服务项目、技师类型等偏好。
- `PatternAnalyzer` 分析用户常用服务、常用时长、偏好技师和回访时机。
- 数据层包含 `UserBehavior`、`UserPreference`、`UserRecommendation`，支撑长期偏好记忆和个性化推荐。

**简历话术方向**：
- "构建用户行为记录与长期偏好记忆模块，从历史预约中沉淀服务项目、时间段、技师类型和时长偏好，减少重复询问并提升推荐个性化。"
- "将单轮预约状态与跨会话用户偏好分层存储，为后续反思复盘和主动回访提供数据基础。"

**可量化角度**：偏好字段数、用户行为记录数、重复询问减少比例、推荐采纳率、回访触达率。

## 亮点 6：自我沉淀与主动推荐闭环

**源码锚点**：
- `services/recommendation_service.py`
- `agents/user_behavior/pattern_analyzer.py`
- `agents/user_behavior/preference_manager.py`
- `db/models.py`

**技术要点**：
- 当前项目已有用户行为分析、偏好管理、推荐数据模型和定时调度框架。
- 可包装为"基于用户行为的反思/沉淀闭环设计"：任务结束后复盘预约成功率、偏好纠正、推荐采纳和咨询命中情况。
- 简历中应表达为基础能力 + 增强设计，不要声称已完整生产化自学习。

**简历话术方向**：
- "设计用户行为分析与主动推荐闭环，基于历史预约提取偏好、识别回访时机并生成个性化提醒，为 Agent 自我沉淀和推荐策略优化提供数据资产。"
- "将预约成功/失败、用户纠正偏好、咨询命中情况沉淀为可复盘信号，用于后续优化预约成功率和知识问答质量。"

**可量化角度**：回访提醒触发数、推荐采纳率、坏 case 复盘数、偏好置信度、预约成功率提升。

## 亮点 7：可配置模型 Provider 与 Embedding 匹配

**源码锚点**：
- `config/model_provider.py`
- `config/settings.py`
- `services/text_embedding.py`
- `requirements.txt`

**技术要点**：
- 通过模型 Provider 工厂创建 Chat LLM 和 Embedding 模型，支持 OpenAI-compatible 服务适配。
- `services/text_embedding.py` 提供统一 Embedding 封装和候选相似度排序能力。
- 技师专长匹配和知识库检索共享 Embedding 能力，减少重复实现。

**简历话术方向**：
- "抽象 Chat/Embedding Provider 工厂，支持不同 OpenAI-compatible 模型后端切换，并复用 Embedding 能力完成知识检索和技师专长相似度匹配。"
- "将模型调用封装在配置层，降低业务 Agent 对具体模型供应商的依赖。"

**可量化角度**：支持 Provider 数、Embedding 调用耗时、相似度匹配候选数、模型切换成本。

## 亮点 8：FastAPI 流式响应与可解释思考过程

**源码锚点**：
- `app.py`
- `api/chat_handler.py`
- `agents/task_classification/agent_router.py`
- `agents/consultant/response_generator.py`

**技术要点**：
- 基于 FastAPI + AsyncGenerator 实现流式响应。
- AgentRouter 和各专用 Agent 输出 `[THOUGHT]`、`[REPLY]` 等 token，展示意图识别和任务交接过程。
- 适合在简历中体现 LLM 应用的实时交互体验和可解释性。

**简历话术方向**：
- "基于 FastAPI 与 AsyncGenerator 实现 Agent 流式输出，将意图分类、任务路由、知识检索和预约处理过程以 thought token 形式可视化，优化复杂预约场景下的等待体验。"

**可量化角度**：首 token 延迟、平均响应时延、流式接口成功率、用户等待时间降低。

## 亮点 9：自研 Modular RAG MCP Server 知识层接入叙事

**源码锚点**：
- 当前项目：`services/knowledge_service.py`、`agents/consultant/knowledge_retriever.py`、`api/knowledge.py`
- 同工作区 RAG 项目：`MODULAR-RAG-MCP-SERVER-main/README.md`、`DEV_SPEC.md`、`src/mcp_server/`
- 包装参考：`project_packaging_report.md`

**技术要点**：
- 当前预约系统已有轻量 RAG 基础：SQLite + Embedding + FAISS + 咨询 Agent 调用。
- 可包装为增强方向：将内部轻量知识库升级为独立 Modular RAG MCP Server，通过 MCP Tool 给预约/咨询 Agent 提供服务规则、技师资质、会员政策、护理禁忌和 SOP 上下文。
- RAG Server 负责可信知识检索、引用、Trace 和评估；预约 Agent 负责业务编排、状态管理和最终回复。

**简历话术方向**：
- "在预约 Agent 后接入自研 Modular RAG MCP Server 作为独立知识层，将门店规则、服务项目、技师资质和会员政策通过 MCP Tool 暴露给业务 Agent，提升咨询回答可信度和可追踪性。"
- "当前项目内置轻量 FAISS 知识库，同时按 MCP 知识服务方向设计扩展边界，实现 Agent 编排与知识检索职责分离。"

**可量化角度**：知识集合数、MCP 工具数、RAG 命中率、引用覆盖率、Trace 覆盖阶段、坏 case 定位时间。

## 亮点 10：测试与工程化可信度

**源码锚点**：
- `tests/test_appointment_agent.py`
- `tests/test_consultant_agent.py`
- `tests/test_task_classification_agent.py`
- `tests/test_user_behavior_agent.py`
- `.github/skills/setup-environment/`

**技术要点**：
- 当前测试覆盖预约 Agent、咨询 Agent、任务分类 Agent、用户行为 Agent 四个核心方向。
- 测试场景包含自然语言信息抽取、状态管理、无关请求处理、流式模式、知识库搜索、行为偏好分析等。
- 项目具备一键环境配置 skill，可降低复现和面试演示成本。

**简历话术方向**：
- "围绕预约、咨询、任务分类和用户行为四类核心 Agent 编写 30 个场景测试函数，覆盖信息抽取、状态流转、知识检索、流式响应和偏好分析等关键路径。"
- "沉淀一键环境配置流程，统一依赖安装、`.env` 模板和启动验证，提高项目可复现性。"

**可量化角度**：测试函数数、核心 Agent 覆盖数、场景覆盖数、环境初始化耗时、演示成功率。