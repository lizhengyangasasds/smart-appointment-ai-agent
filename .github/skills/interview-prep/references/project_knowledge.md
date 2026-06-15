# 项目知识锚点 — Smart Appointment AI Agent

用于模拟面试时把问题落到真实代码和架构上。

## 总体架构

五层：Web/Application → API → Agents → Services → DB。

关键入口：
- `app.py`: FastAPI 应用创建、路由注册、系统启动初始化。
- `api/chat_handler.py`: 单用户 session、TaskClassificationAgent 入口、流式 token 输出。
- `web/routes.py`: Web 页面和聊天接口。

## 多 Agent 编排

关键代码：
- `agents/task_classification_agent.py`: 总控，初始化 LLM、StateManager、TaskClassifier、AgentRouter、UnrelatedHandler、ClassificationProcessor。
- `agents/task_classification/`: 分类、状态管理、路由、无关请求处理。
- `agents/appointment_agent.py`: 预约流程控制。
- `agents/consultant_agent.py`: 咨询/RAG 入口。
- `agents/user_behavior_agent.py`: 用户行为分析。

面试答题要点：
- 中心化编排适合“预约 / 咨询 / 用户行为分析”这种可拆业务。
- TaskClassificationAgent 负责意图识别和路由，专用 Agent 负责领域逻辑。
- 无关请求可回流到分类器，避免单 Agent 锁死在错误状态。

## 预约流程

关键代码：
- `agents/appointment_agent.py`: `run_stream`, `appointment_history`, reset 时机。
- `agents/appointment/input_parser.py`: 用户输入结构化抽取。
- `agents/appointment/appointment_processor.py`: 完整/不完整预约处理、推荐确认。
- `agents/appointment/technician_finder.py`: 技师匹配。
- `agents/appointment/appointment_database.py`: 预约数据操作。

要能解释：
- 哪些字段决定预约是否完整：gender、start_time、duration、project、preference、technician 等。
- 为什么解析结构化数据后再更新历史，再判断 unrelated/finished。
- 推荐技师确认时为什么不能直接 reset。

## RAG 与知识问答

关键代码：
- `services/knowledge_service.py`: 知识加载、向量索引构建、检索。
- `api/knowledge.py`: 知识接口。
- `agents/consultant/`: 咨询分类、知识检索、Prompt 构建、回答生成。

真实问题答题要点：
- 强结构数据如技师信息可不分块，保持完整实体画像。
- 长文本如规则/介绍可用递归字符分块，并设置 overlap。
- 小规模可用 SQLite + FAISS，本地轻量；大规模可迁移 Milvus/Pinecone/Weaviate。
- RAG 评估可以覆盖 hit rate、MRR、context precision、faithfulness、业务回答满意度。

## 技师匹配与 Embedding

关键代码：
- `services/text_embedding.py`: `find_best_match_indices`, `embed_input`, FAISS IndexFlatL2。
- `services/technician_service.py`: 技师数据初始化和管理。
- `agents/appointment/technician_finder.py`: 推荐入口。

注意点：
- 如果使用 L2 距离，需要说明 embedding 是否归一化；若归一化，L2 与余弦排序近似一致。
- 候选技师重复 embedding 会带来延迟成本，应说明缓存策略。

## 用户行为学习与推荐

关键代码：
- `agents/user_behavior/behavior_recorder.py`
- `agents/user_behavior/pattern_analyzer.py`
- `agents/user_behavior/preference_manager.py`
- `services/recommendation_service.py`

真实问题答题要点：
- “有没有学习/反思能力”不要只说理论；结合行为记录、偏好学习、定时推荐、反馈闭环。
- 可扩展为 Reflection / Goal Monitoring，但要讲当前项目已有和可改进部分。

## 模型与配置

关键代码：
- `config/model_provider.py`: `create_chat_model`, `create_embedding_model`。
- `.env`: MODEL_PROVIDER 与 EMBEDDING_PROVIDER 分离。

要点：
- Chat LLM 与 embedding 是两种能力，DeepSeek 适合 chat 但通常要另配 Qwen/Zhipu/OpenAI embedding。
- Qwen 可用 OpenAI-compatible base URL 同时支持 chat 与 embedding。

## 性能与评估

真实问题答题要点：
- 端到端延迟至少分 first-token latency 和 full-response latency。
- 流式路径：用户请求 → API/Web route → `ProcessUserInput_stream` → `TaskClassificationAgent.classify_task_stream` → 专用 Agent yield token。
- Agent 好坏标准：任务成功率、信息抽取准确率、路由准确率、推荐满意度、RAG 命中/忠实性、异常兜底、轨迹正确性、首 token 延迟。