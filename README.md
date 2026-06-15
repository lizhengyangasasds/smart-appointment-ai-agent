# Smart Appointment AI Agent

Smart Appointment AI Agent 是一个面向按摩门店场景的智能预约与咨询系统。项目基于 FastAPI、LangChain、FAISS、SQLite 和多 Agent 协作架构，实现了意图识别、RAG 知识问答、技师智能匹配、预约管理、用户行为分析和个性化提醒等能力。

这个项目的核心目标不是只做一个普通的预约表单，而是尝试把门店前台日常需要处理的高频工作自动化：理解用户想咨询还是预约，判断服务偏好，匹配合适技师，检查可用时间，生成预约结果，并在必要时结合天气、历史行为和偏好数据给出更贴近用户的提醒与推荐。

## 项目背景

在一次按摩门店体验中，我注意到前台人员需要同时处理大量复杂事务：技师排班、顾客预约、服务项目咨询、价格计算、临时改约、技师偏好记录和收款确认等。随着技师数量和顾客量增加，传统人工前台模式很容易出现沟通成本高、信息遗漏、排班冲突和服务体验不稳定的问题。

因此，本项目尝试用 AI Agent 的方式重构这一流程：让系统能够像一个智能前台一样主动理解用户需求，并把不同任务分发给对应的专业 Agent 处理。它既适用于按摩门店，也可以扩展到其他需要人员排班、预约调度和智能客服的服务行业。

## 核心能力

- **智能任务分类**：自动识别用户是在咨询服务、预约技师、查询无关问题，还是触发用户行为分析，并将请求路由到对应 Agent。
- **多 Agent 协作**：通过任务分类 Agent、咨询 Agent、预约 Agent 和用户行为 Agent 分工处理复杂流程，减少单个模块的职责膨胀。
- **RAG 知识咨询**：使用 FAISS 向量索引检索知识库内容，结合大模型生成自然语言回答，支持流式输出。
- **智能预约管理**：根据用户需求、技师专长、历史偏好和可用时间进行匹配，辅助完成预约确认。
- **用户行为分析**：记录用户交互与预约行为，分析偏好模式，并用于后续推荐和个性化反馈。
- **个性化提醒**：在预约完成后，可结合实时天气等外部信息生成更贴近实际场景的提醒。
- **Embedding 缓存优化**：通过数据库缓存和文件缓存减少重复向量计算，提高知识检索性能。
- **数据管理能力**：支持知识库、技师信息和用户行为数据的增删改查，并在数据变化后自动维护索引。
- **日志与兜底机制**：保留关键处理过程日志，在信息不足或异常情况下提供更稳定的降级处理。

## 系统架构

项目采用严格的五层架构，核心原则是：**下层不能反向调用上层**。这样可以避免循环依赖，让业务逻辑、数据访问和接口编排保持清晰边界。

```text
Web & Application Layer
    ↓  app.py, web/：页面、路由入口、系统启动
API Layer
    ↓  api/：外部接口、请求编排、响应封装
Agents Layer
    ↓  agents/：AI Agent、任务路由、对话流程控制
Services Layer
    ↓  services/：业务逻辑、推荐算法、向量处理
DB Layer
    ↓  db/：数据模型、数据库连接、Repository
```

### 允许的调用方向

- Web 层调用 API 层
- API 层调用 Agents 层或 Services 层
- Agents 层调用 Services 层
- Services 层调用 DB 层

### 禁止的调用方式

- 下层反向调用上层
- Web 层绕过 API 直接访问 Services 或 DB
- Agents 层绕过 Services 直接访问 DB
- Services 层调用 Agents、API 或 Web

## Agent 设计

### Task Classification Agent

任务分类 Agent 是系统的主调度器，负责分析用户输入、判断任务类型，并把请求分发给合适的专业 Agent。

```text
用户输入 → 意图分析 → Agent 路由 → 响应协调
```

主要职责：

- 判断用户意图
- 维护对话状态
- 控制不同 Agent 之间的切换
- 处理无法分类或超出能力范围的问题

### Consultation Agent

咨询 Agent 负责知识问答场景，使用 RAG 流程从知识库中检索相关内容，再结合大模型生成回答。

```text
任务分类 → 知识检索 → FAISS 相似度搜索 → 流式回答
```

主要职责：

- 区分咨询问题类型
- 从知识库检索相关内容
- 构建提示词
- 生成自然语言回答

### Appointment Agent

预约 Agent 负责预约相关流程，包括解析用户输入、匹配技师、检查预约信息、生成确认消息等。

```text
任务分类 → 解析预约需求 → 技师匹配 → 预约确认
```

主要职责：

- 提取预约时间、服务项目、技师偏好等信息
- 匹配合适技师
- 处理信息缺失时的追问
- 生成预约结果和提醒

### User Behavior Agent

用户行为 Agent 更偏向后台智能分析，不完全依赖用户显式请求。它会根据交互记录、预约历史和偏好数据分析用户行为，为后续推荐提供依据。

```text
行为记录 → 模式分析 → 偏好更新 → 个性化推荐
```

主要职责：

- 记录用户行为
- 分析偏好模式
- 生成推荐依据
- 支持主动反馈和个性化服务

## 核心设计思想

### 1. 用任务分类降低系统复杂度

系统并不让一个 Agent 处理所有事情，而是先判断用户意图，再分发给对应模块。这样可以让咨询、预约、行为分析等逻辑保持独立，也更容易扩展新的 Agent。

### 2. 用 RAG 解决专业知识回答

按摩服务相关的项目介绍、注意事项、适用人群等内容更适合通过知识库维护。RAG 能让回答基于可控知识来源，而不是完全依赖大模型自由生成。

### 3. 用用户行为让推荐更个性化

系统会记录用户的咨询、预约和偏好信息。后续在推荐技师或服务项目时，可以结合历史行为，而不是每次都从零开始询问。

### 4. 用分层架构保证可维护性

Agent 负责智能流程，Service 负责业务逻辑，Repository 负责数据访问。每层只关心自己的职责，减少后期修改时的连锁影响。

### 5. 为真实业务场景预留扩展空间

项目目前以本地 SQLite 和单体服务为主，但架构上预留了模型提供商切换、MCP 外部服务接入、后台任务、缓存优化和云端部署的扩展方向。

## 架构图

![System Architecture](./architecture%20.jpg)

## 技术栈

- **后端框架**：FastAPI、Uvicorn
- **AI 框架**：LangChain
- **大模型接入**：兼容 OpenAI 格式的模型提供商，例如 Qwen、DeepSeek、Zhipu、OpenAI、Azure OpenAI
- **向量检索**：FAISS
- **数据库**：SQLite、SQLAlchemy
- **RAG 能力**：Embedding、向量索引、知识库检索、提示词构建
- **流式响应**：Python AsyncGenerator
- **前端页面**：Jinja2 模板、静态 CSS
- **外部服务扩展**：MCP，用于天气等外部信息接入
- **配置管理**：python-dotenv
- **后台任务**：schedule

## 项目结构

```text
Smart appointment AI agent/
├── agents/                         # 多 Agent 智能层
│   ├── task_classification_agent.py # 任务分类与主路由
│   ├── consultant_agent.py          # RAG 咨询 Agent
│   ├── appointment_agent.py         # 智能预约 Agent
│   ├── user_behavior_agent.py       # 用户行为分析 Agent
│   ├── task_classification/         # 意图识别、状态管理、路由逻辑
│   ├── consultant/                  # 知识检索、提示词、回答生成
│   ├── appointment/                 # 预约解析、技师匹配、消息构建
│   └── user_behavior/               # 行为记录、偏好管理、模式分析
├── api/                             # API 编排层
│   ├── appointment.py               # 预约接口
│   ├── consultation.py              # 咨询接口
│   ├── task.py                      # 任务分类接口
│   ├── chat_handler.py              # 流式聊天处理
│   ├── technician.py                # 技师管理接口
│   ├── knowledge.py                 # 知识库管理接口
│   └── user_behavior_analysis.py    # 用户行为分析接口
├── services/                        # 业务逻辑层
│   ├── appointment_service.py       # 预约业务逻辑
│   ├── knowledge_service.py         # 知识库管理
│   ├── recommendation_service.py    # 推荐逻辑
│   ├── technician_service.py        # 技师信息管理
│   ├── text_embedding.py            # Embedding 与向量处理
│   └── user_behavior_service.py     # 用户行为服务
├── db/                              # 数据持久化层
│   ├── models.py                    # SQLAlchemy 模型
│   ├── db_router.py                 # 数据库路由
│   ├── local_db.py                  # 本地数据库操作
│   ├── base/                        # 数据库基础接口
│   └── repositories/                # Repository 数据访问封装
├── config/                          # 配置模块
│   ├── constants.py                 # 常量与枚举
│   ├── database.py                  # 数据库配置
│   ├── model_provider.py            # 模型与 Embedding Provider 工厂
│   ├── settings.py                  # 应用配置
│   └── time_config.py               # 时间与排班配置
├── web/                             # Web 页面层
│   ├── routes.py                    # 页面路由
│   ├── templates/                   # HTML 模板
│   └── static/                      # 静态资源
├── mcp-server/                      # MCP 外部服务扩展
├── data/                            # 数据库与缓存目录
├── tests/                           # 测试用例
├── app.py                           # 应用入口
├── requirements.txt                 # Python 依赖
├── .env.example                     # 环境变量模板
└── README.md                        # 项目说明
```

## 快速开始

### 1. 创建虚拟环境

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

Windows CMD：

```cmd
.venv\Scripts\activate.bat
```

macOS 或 Linux：

```bash
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

复制环境变量模板：

```bash
cp .env.example .env
```

Windows PowerShell 可以使用：

```powershell
Copy-Item .env.example .env
```

然后在 `.env` 中填写模型和数据库配置。项目支持 OpenAI 兼容格式的大模型与 Embedding 服务。

```env
MODEL_PROVIDER=qwen
LLM_API_KEY=your_llm_api_key_here
LLM_BASE_URL=your_openai_compatible_chat_base_url_here
LLM_MODEL=your_chat_model_name_here

EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_embedding_api_key_here
EMBEDDING_BASE_URL=your_openai_compatible_embedding_base_url_here
EMBEDDING_MODEL=your_embedding_model_name_here

DATABASE_URL=sqlite:///./data/smart_appointment.db

DEBUG=True
LOG_LEVEL=INFO
```

常见配置方向：

- Qwen：使用阿里云百炼或 DashScope 的模型、Base URL 和 API Key。
- DeepSeek：可用于聊天模型，Embedding 可搭配其他兼容服务。
- Zhipu：可配置智谱的聊天模型和向量模型。
- Azure OpenAI：将 `MODEL_PROVIDER` 设置为 `azure`，并补充对应的 Azure OpenAI 环境变量。

### 4. 启动服务

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

如果 8000 端口已被占用，可以换成 8001：

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8001 --reload
```

启动后可以访问：

- Web 页面：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs
- ReDoc 文档：http://127.0.0.1:8000/redoc

## 测试

运行全部测试：

```bash
pytest
```

运行单个测试文件：

```bash
pytest tests/test_task_classification_agent.py
```

## 主要页面

- 首页聊天与预约入口：`web/templates/index.html`
- 知识库管理：`web/templates/knowledge_management.html`
- 技师管理：`web/templates/technician.html`
- 技师排班：`web/templates/technician_schedule.html`
- 用户行为分析：`web/templates/user_behavior_analysis.html`

## 后续规划

### 更强的 Agent 自主能力

- 增加 Agent 自我反思机制，让系统能够评估回答质量和预约成功率。
- 引入更完整的多轮推理链，提升复杂预约和冲突处理能力。
- 根据真实用户反馈优化推荐策略。

### 更完整的多 Agent 协作

- 增加 Agent-to-Agent 通信机制，减少所有任务都依赖主分类器转发的问题。
- 将用户行为 Agent 的后台分析能力做得更稳定，支持定时任务和主动触达。
- 把预约、推荐、咨询之间的上下文记忆打通得更自然。

### 生产化能力

- 增加用户登录、权限控制和数据隔离。
- 增加更完整的异常处理和边界场景覆盖。
- 优化向量检索性能、缓存策略和响应速度。
- 支持 Docker 部署、云数据库和更标准的日志监控。

## 项目价值

这个项目把多 Agent、RAG、用户行为分析、预约调度和外部工具接入放在同一个真实业务场景中验证。它既是一个按摩门店智能前台原型，也可以作为学习 AI Agent 工程化、分层架构、RAG 系统和业务自动化的综合实践项目。



------------------------------------------------------------
这个项目是我最早开始自学大模型做的项目。上面的内容是当时（2025年7月左右完成的）。
其实我在计划做更加复杂，符合时代趋势的Agent，预计在9月份完成，会分享在笔记中。
但是很多朋友问我这个项目能不能参考，所以我索性总结开源了除了。值得一提的是，上面的内容是25年写的，当时如何配置环境，都是传统方法，写在README中，自己配置。 但是在2026年，不管是vibe coding的方法，还是环境配置的方法，都有了极大改进。

我的项目配置的方法，现在都推荐使用SKILL，所以这里有setup-envrionment skill，一键配置。
我也总结了更多的这个项目，放在2026年，如何包装，如何使用配套资源，面试真题，视频讲解，使用建议在我的大模型笔记中。
👉 **请关注小红书：[不转到大模型不改名]（id:4740535877) 获取以上所有资源。**
👉 b站： 骑猪撞宝马71