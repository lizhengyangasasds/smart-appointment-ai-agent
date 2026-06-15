# Smart Appointment AI Agent

智能预约 AI Agent 是一个面向服务行业（按摩/足疗门店）的多 Agent 协作对话系统，基于 FastAPI、LangChain、FAISS 和 SQLite 构建。系统能够自动识别用户意图（咨询/预约/其他），通过多 Agent 分工完成知识问答、技师智能匹配、预约管理和用户行为分析。

> 本项目用于个人 AI/Agent 技术学习与实践展示。

---

## 核心能力

| 能力 | 实现方案 |
|------|----------|
| **智能任务分类** | LLM + 结构化输出，判断用户意图并路由到对应 Agent |
| **多 Agent 协作** | TaskClassification → Appointment / Consultation / UserBehavior 分流 |
| **RAG 知识问答** | FAISS 向量索引 + LangChain Embedding，支持流式输出 |
| **预约双重预约防护** | DB 事务级原子检查+插入，彻底消除并发竞态 |
| **技师智能匹配** | 按专长相似度 + 性别偏好 + 时间可用性多维排序 |
| **用户行为分析** | 偏好提取、模式识别、个性化回访提醒 |
| **会话记忆系统** | 三层记忆（工作/语义/摘要）+ 自动压缩 |
| **流式响应** | FastAPI AsyncGenerator，后端边生成边推送 |

---

## 系统架构

```
Web Layer          (FastAPI + Jinja2 模板)
     ↓
API Layer          (请求编排、响应封装)
     ↓
Agents Layer       (TaskClassification / Appointment / Consultation / UserBehavior)
     ↓
Services Layer     (业务逻辑、Embedding、推荐算法)
     ↓
DB Layer           (SQLite WAL + SQLAlchemy + Repository 模式)
```

### 分层原则
- **上层调用下层**：Web → API → Agents → Services → DB，禁止反向
- **Repository 模式**：数据访问层统一封装，事务边界清晰
- **单一写锁**：所有写操作通过 `threading.RLock` + SQLite WAL 模式并发保护

---

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | FastAPI + Uvicorn |
| AI 框架 | LangChain |
| 大模型 | OpenAI 兼容格式（Qwen / DeepSeek / Zhipu / Azure OpenAI） |
| 向量检索 | FAISS |
| 数据库 | SQLite (WAL 模式) + SQLAlchemy |
| 前端 | Jinja2 HTML 模板 + 响应式 CSS |
| 外部扩展 | MCP (天气信息等) |

---

## 项目结构

```
smart-appointment-ai-agent/
├── agents/                          # 多 Agent 核心
│   ├── task_classification_agent.py # 任务分类 & 主路由
│   ├── appointment_agent.py         # 预约流程控制
│   ├── consultant_agent.py          # RAG 咨询 Agent
│   ├── user_behavior_agent.py       # 行为分析 Agent
│   ├── task_classification/         # 意图识别、状态管理
│   ├── appointment/                # 解析器、技师匹配器、数据库操作器
│   ├── consultant/                  # 提示词构建、回答生成
│   └── user_behavior/              # 模式分析、偏好管理
├── api/                             # API 编排层
│   ├── chat_handler.py              # 流式聊天处理核心
│   ├── knowledge.py                 # 知识库 CRUD + 搜索
│   ├── technician.py               # 技师管理接口
│   └── user_behavior_analysis.py    # 行为分析接口
├── services/                        # 业务逻辑层
│   ├── knowledge_service.py         # FAISS 索引 + 知识检索
│   ├── appointment_service.py       # 预约业务（原子性预约）
│   ├── text_embedding.py           # Embedding 生成与缓存
│   ├── conversation_memory_service.py  # 工作记忆 + 自动压缩
│   ├── semantic_memory_service.py    # 偏好提取 + 置信度管理
│   └── memory_manager.py            # 记忆系统统一调度
├── db/                              # 数据持久化层
│   ├── base/
│   │   ├── session_manager.py      # SQLite WAL + 写锁管理
│   │   ├── interfaces.py           # Repository 抽象接口
│   │   └── exceptions.py           # 自定义异常
│   ├── repositories/               # Repository 实现
│   │   ├── technician_repository.py
│   │   ├── knowledge_repository.py
│   │   ├── user_behavior_repository.py
│   │   └── memory_repository.py
│   ├── models.py                   # SQLAlchemy 数据模型
│   └── models_memory.py            # 记忆系统数据模型
├── web/
│   ├── routes.py                  # 页面路由
│   └── templates/                  # HTML 模板
├── app.py                          # 应用入口
└── requirements.txt
```

---

## 关键设计

### 1. 多 Agent 协作与任务路由

```
用户消息 → TaskClassificationAgent
    ├── 咨询类 → ConsultantAgent（RAG 知识检索 + 流式回答）
    ├── 预约类 → AppointmentAgent（提取需求 → 技师匹配 → 预约确认）
    └── 其他类 → 友好拒绝或转接
```

LLM 通过结构化输出（JSON Mode）判断意图，避免纯规则匹配的脆弱性。

### 2. RAG 知识问答

```
查询 → Embedding → FAISS Top-K 检索 → 上下文构建 → LLM 生成 → 流式响应
```

FAISS 索引在服务启动时构建，知识增删改后自动重建，支持按分类过滤。

### 3. 并发安全 — 原子性预约

传统预约的 Check-Then-Act 竞态：

```
请求A: is_available() → True
请求B: is_available() → True  ← 同时通过
请求A: add_schedule() → 成功
请求B: add_schedule() → 冲突！
```

本项目通过 `reserve_slot()` 在单个 DB 事务 + 写锁内完成检查与插入，彻底消除竞态：

```python
def reserve_slot(self, technician_id, start_time, end_time, status, appointment_id):
    with self.session_scope(exclusive=True):   # 持有写锁
        conflict = query_conflict()            # 冲突检测
        if conflict:
            raise SlotTakenException()         # 原子拒绝
        insert_schedule()                      # 插入记录
```

### 4. 三层会话记忆系统

```
ConversationMemoryService  工作记忆   当前会话消息
SemanticMemoryService     语义记忆   用户偏好提取
SessionSummary            摘要压缩   超过阈值时压缩历史
```

超过 20 轮对话后触发 LLM 摘要压缩，控制 token 消耗同时保留关键上下文。

### 5. 技师智能匹配

```
用户偏好 → 文本嵌入相似度排序 → 性别筛选 → 可用性检查 → 推荐
```

支持按专长相似度匹配指定技师、按偏好智能推荐、按性别筛选等多个维度。

---

## 快速启动

### 1. 环境配置

```bash
# 克隆项目
git clone https://github.com/lizhengyangasasds/smart-appointment-ai-agent.git
cd smart-appointment-ai-agent

# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key 和模型配置
```

### 2. 启动服务

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

启动后访问：
- Web 聊天界面：http://127.0.0.1:8000
- 知识库管理：http://127.0.0.1:8000/knowledge
- 技师排班：http://127.0.0.1:8000/technician_schedule
- 用户行为分析：http://127.0.0.1:8000/user_behavior

---

## 数据模型

| 表名 | 说明 |
|------|------|
| `technicians` | 技师信息（姓名、性别、专长） |
| `technician_schedules` | 排班记录（技师、时间段、状态、预约ID） |
| `knowledge_documents` | 知识库（内容、分类、关键词、向量嵌入） |
| `user_behaviors` | 用户行为日志（预约/咨询类型、操作数据） |
| `user_preferences` | 用户偏好（类型、值、置信度） |
| `conversation_messages` | 会话消息（角色、内容、轮次、压缩标记） |
| `semantic_memories` | 语义记忆（类型、Key-Value、置信度） |
| `session_summaries` | 会话摘要（压缩后的上下文） |

