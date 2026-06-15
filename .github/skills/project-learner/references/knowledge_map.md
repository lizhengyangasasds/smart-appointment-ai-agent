# 项目知识点地图 — Smart Appointment AI Agent

总计 40 个知识点。真实面试题 ID 来自 `../interview-prep/references/real_interview_questions.md`。

## D1 项目定位与整体架构

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D1.1 | 项目解决的业务问题与用户流程 | `README.md`, `app.py` | RQ01, RQ02, RQ03 |
| D1.2 | 五层架构职责与依赖方向 | `README.md`, `api/`, `agents/`, `services/`, `db/` | RQ01 |
| D1.3 | FastAPI 启动与系统初始化 | `app.py` | RQ01 |
| D1.4 | Web/API/Agent/Service/DB 的端到端链路 | `app.py`, `api/chat_handler.py`, `web/routes.py` | RQ01, RQ10 |

## D2 多 Agent 协作与任务分类

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D2.1 | TaskClassificationAgent 总控职责 | `agents/task_classification_agent.py` | RQ08, RQ15 |
| D2.2 | TaskClassifier 与 ClassificationProcessor | `agents/task_classification/` | RQ08 |
| D2.3 | StateManager / SharedState 状态共享 | `config/constants.py`, `agents/task_classification/` | RQ09 |
| D2.4 | AgentRouter 路由与回流 | `agents/task_classification/agent_router.py` | RQ08, RQ09 |
| D2.5 | 单 Agent vs 多 Agent 取舍 | `agents/` | RQ14, RQ15 |

## D3 预约流程与状态管理

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D3.1 | AppointmentAgent 主流程 | `agents/appointment_agent.py` | RQ01 |
| D3.2 | appointment_history 字段设计 | `agents/appointment_agent.py` | RQ01 |
| D3.3 | InputParser 结构化抽取 | `agents/appointment/input_parser.py` | RQ11 |
| D3.4 | AppointmentProcessor 完整/不完整预约处理 | `agents/appointment/appointment_processor.py` | RQ11 |
| D3.5 | 推荐确认、无关请求和 reset 时机 | `agents/appointment_agent.py` | RQ09, RQ11 |

## D4 RAG 知识库与分块策略

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D4.1 | 强结构数据不分块策略 | `services/knowledge_service.py`, `services/technician_service.py` | RQ04 |
| D4.2 | 长文本递归字符分块策略 | `services/knowledge_service.py` | RQ04 |
| D4.3 | 知识加载、embedding 与 FAISS 索引 | `services/knowledge_service.py` | RQ05 |
| D4.4 | RAG 质量评估指标 | `tests/`, `services/knowledge_service.py` | RQ06 |
| D4.5 | 知识问答功能与咨询 Agent | `agents/consultant_agent.py`, `agents/consultant/` | RQ13 |

## D5 技师推荐与 Embedding/FAISS

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D5.1 | create_embedding_model 配置 | `config/model_provider.py` | RQ05 |
| D5.2 | find_best_match_indices 匹配流程 | `services/text_embedding.py` | RQ05 |
| D5.3 | 技师信息实体画像与推荐 | `services/technician_service.py`, `agents/appointment/technician_finder.py` | RQ04 |
| D5.4 | embedding 缓存与性能成本 | `services/text_embedding.py`, `data/` | RQ10 |

## D6 用户行为学习与主动推荐

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D6.1 | 行为记录 | `agents/user_behavior/behavior_recorder.py` | RQ12 |
| D6.2 | 偏好管理 | `agents/user_behavior/preference_manager.py` | RQ12 |
| D6.3 | 模式分析 | `agents/user_behavior/pattern_analyzer.py` | RQ12 |
| D6.4 | RecommendationService 调度推荐 | `services/recommendation_service.py` | RQ12 |

## D7 API/Web/流式响应与延迟

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D7.1 | ProcessUserInput_stream 流式入口 | `api/chat_handler.py` | RQ10 |
| D7.2 | classify_task_stream token 传递 | `agents/task_classification_agent.py` | RQ10 |
| D7.3 | Web routes 与前端接收 | `web/routes.py`, `web/templates/`, `web/static/` | RQ10 |
| D7.4 | first-token latency 与 full-response latency | `api/`, `agents/` | RQ10, RQ11 |

## D8 数据库与持久化

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D8.1 | SQLAlchemy models | `db/models.py` | RQ05 |
| D8.2 | db_router/local_db 连接管理 | `db/db_router.py`, `db/local_db.py` | RQ05 |
| D8.3 | Repository 模式 | `db/repositories/` | RQ05 |
| D8.4 | SQLite 本地化与生产迁移 | `db/`, `data/` | RQ05 |

## D9 模型配置、框架选型与工程化

| ID | 知识点 | 关键文件 | 关联真题 |
|----|--------|----------|----------|
| D9.1 | Chat LLM 与 Embedding provider 分离 | `config/model_provider.py`, `.env` | RQ05 |
| D9.2 | Qwen/OpenAI-compatible/Azure 配置 | `config/model_provider.py`, `.env.example` | RQ05 |
| D9.3 | LangChain vs Semantic Kernel | `requirements.txt`, `agents/`, `services/` | RQ07 |
| D9.4 | Agent 评估体系 | `tests/`, `agents/`, `services/` | RQ11 |
| D9.5 | 测试、环境配置与运行验证 | `tests/`, `.github/skills/setup-environment/` | RQ11 |