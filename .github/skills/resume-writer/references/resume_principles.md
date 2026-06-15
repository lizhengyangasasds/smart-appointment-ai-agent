# 简历编写原则（大模型工程师方向）

## 1. 项目描述四段式结构

遵循"背景 -> 目标 -> 过程 -> 结果"逻辑：

- **背景**：项目所属业务场景、模型目标、使用需求。例：本项目旨在构建面向本地服务业的智能预约 Agent 系统，服务门店客户咨询、技师排班和预约确认流程。
- **目标**：技术目标或改进方向。例：通过多 Agent 编排、RAG 知识库和用户偏好记忆降低重复询问，提高预约推荐准确性。
- **过程**：关键技术方案和工程实现细节，明确候选人负责部分、使用工具、面临挑战。例：设计 TaskClassificationAgent 统一入口，基于共享状态路由预约、咨询和用户行为分析流程。
- **结果**：量化数据展示最终效果。例：意图路由准确率达到 90% 左右，知识库 Top-3 命中率达到 85% 以上，首 token 延迟控制在 1s 级别。

## 2. 技术标签维度

简历需显式体现与岗位匹配度较高的技术要素：

| 维度 | 关键词示例 |
|------|-----------|
| Agent 架构 | Multi-Agent, Task Routing, Agent Orchestration, State Manager, Tool Calling, MCP |
| LLM 应用 | LangChain, Prompt Engineering, LLM Provider, OpenAI-compatible API, Streaming Response |
| RAG 检索 | RAG, FAISS, Embedding, Vector Search, Knowledge Base, Citation, Retrieval Quality |
| 预约业务 | Scheduling, Technician Matching, Preference Learning, Dialogue State, Recommendation |
| 数据与工程 | FastAPI, AsyncGenerator, SQLAlchemy, SQLite, Repository Pattern, Layered Architecture |
| 测试评估 | Scenario Test, Agent Trajectory, Intent Accuracy, Hit Rate@K, Latency, Regression Test |

这些关键词既可出现在项目描述中，也可在"技术栈"部分单独列出，便于 ATS 和面试官识别。

## 3. 亮点挖掘与差异化策略

面试官关注候选人主导了什么、有哪些独到之处、是否体现技术判断力：

1. **介绍决策过程**：说明为什么采用中心化多 Agent 编排，而不是把所有逻辑塞进单个 Chatbot。
2. **展示问题解决能力**：描述如何处理信息缺失、技师不可用、知识库无命中、模型调用失败等场景。
3. **强调可复用性/通用性**：将按摩门店场景抽象为本地服务业预约自动化，可迁移到医美、理疗、维修、课程预约等场景。
4. **突出结果与影响力**：用预约成功率、重复询问减少、RAG 命中率、延迟、测试数量等指标体现项目价值。
5. **保持可解释边界**：包装可以体现设计规划，但每个 claim 都要能落到源码、数据结构或明确的增强方向。

## 4. 常见误区

| 误区 | 说明 |
|------|------|
| 大而空 | 不要只写"负责大模型 Agent 系统"，要说明路由、状态、检索、偏好记忆怎么做 |
| 工具堆砌 | 仅列 LangChain/FAISS/FastAPI 而不解释业务价值 |
| 缺乏结果 | 没有量化指标或建议指标，项目难以评估 |
| 逻辑混乱 | 过程描述堆很多模块，但看不出用户请求如何流转 |
| 过度包装 | 把规划中的记忆、反思、MCP 接入说成完全生产化落地 |

## 5. 改进建议

- 多用"设计"、"实现"、"构建"、"优化"、"集成"等主动表达，少用"参与"、"协助"。
- 每条 bullet 按"动作 + 技术方案 + 业务/指标结果"写。
- 背景段必须讲清楚按摩门店前台痛点，否则项目容易显得像玩具 demo。
- 技术栈顺序要跟目标岗位一致：Agent 岗先写 Multi-Agent/MCP，RAG 岗先写 RAG/FAISS/Embedding，后端岗先写 FastAPI/SQLAlchemy/分层架构。
- 如果使用建议指标，必须提醒用户根据真实测试、压测或日志确认。