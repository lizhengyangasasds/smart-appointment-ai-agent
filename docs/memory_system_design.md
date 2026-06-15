# 记忆系统设计文档

> 本文档描述了为 Smart Appointment AI Agent 设计的**三层记忆系统**的实现方案，涵盖架构设计、数据库模型、服务层实现、以及上下文窗口压缩策略。

---

## 1. 设计背景与问题

### 原系统缺陷

| 问题 | 影响 |
|------|------|
| 全局 singleton `global_session_id` | 所有用户共享同一个 session，多用户场景数据混乱 |
| `InMemoryChatMessageHistory` 仅在 `AppointmentAgent` 内使用 | `ConsultantAgent` 和 `TaskClassifier` 完全没有历史感知 |
| 对话历史不持久化 | 服务重启后记忆全失 |
| 无上下文窗口管理 | 对话轮次增加后 token 无控制增长 |
| 无语义记忆 | 无法记忆用户偏好（技师、时间、力度等） |

---

## 2. 三层记忆架构

```
┌─────────────────────────────────────────────────────────┐
│                    上下文窗口上限（~6000 tokens）            │
│  ┌──────────────────┐    ┌──────────────────────────┐   │
│  │  会话摘要 (压缩后)  │ + │   最近 N 轮对话 (未压缩)   │   │
│  └──────────────────┘    └──────────────────────────┘   │
│         工作记忆                 情景记忆                 │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│                    语义记忆层 (持久化)                     │
│  用户偏好 │ 关键事实 │ 行为模式（置信度 + 过期机制）          │
└─────────────────────────────────────────────────────────┘
```

### 2.1 工作记忆（Working Memory）

- **存储位置**: `ConversationMessage` 表
- **生命周期**: 当前会话窗口内的未压缩消息
- **作用**: 向 LLM 提供最近的对话上下文（3-8 轮）
- **粒度**: 每条消息按 `turn_index` 编号

### 2.2 情景记忆（Episodic Memory）

- **存储位置**: `SessionSummary` 表
- **生命周期**: 被压缩后的对话历史
- **作用**: 当上下文窗口满时，将旧消息压缩为一段摘要，释放 token 空间
- **触发条件**: 未压缩消息 token 总数 ≥ 4800

### 2.3 语义记忆（Semantic Memory）

- **存储位置**: `SemanticMemory` 表
- **内容**: 用户偏好、关键事实、行为模式
- **作用**: 不依赖对话原文，而是在对话过程中**主动提取**结构化知识
- **置信度机制**: 同一偏好多次出现时增加置信度；长时间未触发则衰减

---

## 3. 数据库模型

### 3.1 ConversationMessage（对话消息）

```sql
CREATE TABLE conversation_messages (
    id INTEGER PRIMARY KEY,
    session_id VARCHAR(64),           -- 会话 ID
    user_id VARCHAR(64),               -- 用户 ID（多用户场景）
    role VARCHAR(16),                  -- 'user' | 'assistant'
    content TEXT,                      -- 消息内容
    agent_tag VARCHAR(32),             -- '[咨询机器人]' 等
    turn_index INTEGER,                -- 第几轮对话
    message_type VARCHAR(32),          -- 'appointment' | 'consultation'
    is_compressed INTEGER DEFAULT 0,   -- 0=原始, 1=已被摘要
    token_count INTEGER,               -- 预估 token 数
    metadata JSON,                     -- 额外信息
    created_at DATETIME
);
-- 索引
CREATE INDEX idx_session_turn ON conversation_messages(session_id, turn_index);
CREATE INDEX idx_session_compressed ON conversation_messages(session_id, is_compressed);
```

### 3.2 SemanticMemory（语义记忆）

```sql
CREATE TABLE semantic_memories (
    id INTEGER PRIMARY KEY,
    session_id VARCHAR(64),
    user_id VARCHAR(64),
    memory_type VARCHAR(32),           -- 'preference' | 'fact' | 'constraint' | 'pattern'
    key VARCHAR(128),                 -- 'preferred_technician' | 'time_preference'
    value TEXT,                        -- '张伟技师' | 'afternoon'
    confidence INTEGER DEFAULT 1,      -- 置信度/出现次数
    source_turn INTEGER,               -- 来源轮次
    is_active INTEGER DEFAULT 1,       -- 软删除
    expires_at DATETIME,               -- 可选过期时间
    metadata JSON,
    created_at DATETIME,
    updated_at DATETIME
);
-- 索引
CREATE INDEX idx_user_memory ON semantic_memories(user_id, memory_type);
```

### 3.3 SessionSummary（会话摘要）

```sql
CREATE TABLE session_summaries (
    id INTEGER PRIMARY KEY,
    session_id VARCHAR(64),
    summary_text TEXT,                 -- 压缩摘要内容
    summary_turn_start INTEGER,
    summary_turn_end INTEGER,
    token_count INTEGER,
    created_at DATETIME
);
```

---

## 4. Token 计数与上下文窗口

### 4.1 轻量级 Token 计数器

不需要外部 tokenization 库，使用启发式估算：

```python
class TokenCounter:
    # 中文：1 字符 ≈ 1 token（保守估算）
    # 英文：1 word ≈ 1.3 tokens
    # 留 15% buffer

    @classmethod
    def estimate(cls, text: str) -> int:
        chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
        english_words = len(re.findall(r'[a-zA-Z]+', text))
        tokens = chinese + int(english_words * 1.3) + int((len(text) - chinese) * 0.85)
        return max(1, tokens)
```

### 4.2 上下文窗口配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_context_tokens` | 6000 | 上下文总上限（约 3000 中文字） |
| `summary_threshold_tokens` | 4800 | 触发压缩的阈值（80% 水位线） |
| `preserve_after_summary` | 1200 | 摘要后保留的最新 token 数 |

### 4.3 上下文构建策略

```
build_context(session_id):
    1. 查最新 SessionSummary，如果有 → 拼接摘要
    2. 从最新消息往前取，直到 token 达到上限
    3. 返回 "【会话摘要】... \n【最近对话】..." 格式字符串
```

---

## 5. 压缩策略

### 5.1 触发条件

```python
def should_compress(session_id) -> bool:
    messages = repo.get_uncompressed_messages(session_id)
    total = sum(estimate(m.content) for m in messages)
    return total >= summary_threshold_tokens  # 默认 4800 tokens
```

### 5.2 压缩流程

```
compress(session_id):
    1. 收集所有 is_compressed=0 的消息
    2. 调用 LLM 生成 3-5 句摘要：
       "以下是对话记录，请用3-5句话总结：..."
    3. 写入 SessionSummary 表
    4. 批量 UPDATE messages SET is_compressed=1
    5. 释放 token 空间，可继续对话
```

### 5.3 摘要提示词

```
你是一个对话摘要助手。请简洁地用3-5句话总结以下对话的核心内容：
1. 用户的主要需求/问题是什么？
2. 机器人给出了什么信息或处理结果？
3. 对话是否有明确的结论或未完成事项？
```

---

## 6. 语义记忆提取

### 6.1 自动提取规则

使用正则 + 启发式规则从对话文本中提取：

```python
class SemanticExtractor:
    # 技师偏好
    TECHNICIAN_PATTERN = r'([\u4e00-\u9fff]{2,4}(?:技师|师傅))|...'

    # 时间偏好词
    TIME_WORDS = {'上午': 'morning', '下午': 'afternoon', '晚上': 'evening', ...}

    # 时长提取
    DURATION_PATTERN = r'(\d+)\s*(?:分钟|min|个小时?|小时)'

    # 服务项目
    PROJECT_WORDS = ['推拿', '按摩', '足疗', 'SPA', '刮痧', '拔罐', ...]

    # 力度偏好
    STRENGTH_WORDS = {'力气大': 'heavy', '力气小': 'light', ...}
```

### 6.2 置信度管理

```python
# 同一偏好多次出现 → 置信度 +1
store_preference(key='preferred_technician', value='张伟', confidence_delta=1)

# 长时间未触发 → 置信度衰减
if (now - updated_at).days > 7:
    confidence = max(1, confidence - (days - 7))
```

### 6.3 用户画像输出格式

```
【用户画像】

  [preference]
  - preferred_technician: 张伟技师（置信度 3）
  - time_preference: afternoon（置信度 2）
  - strength_preference: heavy（置信度 1）

  [constraint]
  - avoid_technician: 李小美（置信度 2）
```

---

## 7. 多用户隔离机制

### 7.1 修复前（bug）

```python
# 全局唯一 session_id，所有用户共享！
global_session_id = str(uuid.uuid4())
task_agent = TaskClassificationAgent(
    AppointmentAgent(session_id=global_session_id),
    ConsultantAgent(session_id=global_session_id)
)
```

### 7.2 修复后

```python
# 每个 session_id 对应独立的 _MemoryAwareChatSession
_chat_handlers: Dict[str, _MemoryAwareChatSession] = {}

def _get_or_create_session(session_id: str) -> _MemoryAwareChatSession:
    if session_id not in _chat_handlers:
        _chat_handlers[session_id] = _MemoryAwareChatSession(session_id)
    return _chat_handlers[session_id]
```

### 7.3 前端 session 管理

```javascript
// 浏览器 localStorage，每个 tab 独立 session
const SESSION_KEY = 'smart_appointment_session_id';
function getSessionId() {
    let sid = localStorage.getItem(SESSION_KEY);
    if (!sid) {
        sid = 'sess_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
        localStorage.setItem(SESSION_KEY, sid);
    }
    return sid;
}

// 发送请求时携带 session_id
fetch('/chat/stream', {
    method: 'POST',
    body: JSON.stringify({message: text, session_id: sessionId})
});
```

---

## 8. 记忆上下文注入点

记忆上下文通过 `memory_context` 参数在 Agent 调用链中传递：

```
用户输入
    ↓
chat_handler.ProcessUserInput_stream(session_id)
    ↓
_MemoryAwareChatSession._stream_response()
    ↓ 写入用户消息到 DB
    ↓ 注入 memory_context
TaskClassificationAgent.classify_task_stream(task, memory_context)
    ↓ 传入记忆上下文
ClassificationProcessor.process_task_stream(task, memory_context)
    ↓
TaskClassifier.classify_task(task, history_str)
    ↓ 根据分类结果
AgentRouter.route_to_appointment() / route_to_consultation()
    ↓ 传入记忆上下文
AppointmentAgent.run_stream(task, memory_context)
    ↓
InputParser.parse_stream(task, history, memory_context)
    ↓
AppointmentAgent._record_assistant_response()
    ↓ 写入助手消息到 DB
    ↓ 检查是否需要压缩
```

---

## 9. API 管理接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/memory/reset` | POST | 重置指定 session 的所有记忆 |
| `/api/memory/status/{session_id}` | GET | 获取 session 上下文使用状态 |
| `/api/memory/context/{session_id}` | GET | 获取完整记忆上下文 |
| `/api/memory/recommendation/{session_id}` | GET | 获取推荐相关上下文 |
| `/api/memory/active-sessions` | GET | 列出活跃 session |

---

## 10. 文件清单

### 新增文件

| 文件路径 | 说明 |
|----------|------|
| `db/models_memory.py` | 三张记忆表 SQLAlchemy 模型 |
| `db/repositories/memory_repository.py` | 记忆数据访问层 |
| `services/conversation_memory_service.py` | 工作记忆服务 + TokenCounter |
| `services/semantic_memory_service.py` | 语义记忆服务 + SemanticExtractor |
| `services/memory_manager.py` | 统一记忆管理器 |
| `api/chat_handler_core.py` | ChatHandler 核心实现（备用） |
| `api/memory.py` | 记忆管理 API 端点 |
| `docs/memory_system_design.md` | 本文档 |

### 修改文件

| 文件路径 | 修改内容 |
|----------|----------|
| `db/__init__.py` | 导出新模型和 Repository |
| `db/repositories/__init__.py` | 导出 MemoryRepository |
| `db/base/session_manager.py` | 自动创建记忆表 |
| `api/__init__.py` | 注册 memory_router |
| `api/chat_handler.py` | 重构为 per-session 架构 |
| `web/routes.py` | ChatRequest 增加 session_id 字段 |
| `web/templates/index.html` | 前端生成并传递 session_id |
| `agents/task_classification_agent.py` | classify_task_stream 增加 memory_context |
| `agents/task_classification/task_classifier.py` | classify_task 增加 memory_context |
| `agents/task_classification/classification_processor.py` | process_task_stream 传递 memory_context |
| `agents/task_classification/agent_router.py` | 各路由方法增加 memory_context |
| `agents/consultant_agent.py` | consult_stream 增加 memory_context |
| `agents/consultant/consultation_processor.py` | process_consultation_stream 增加 memory_context |
| `agents/consultant/response_generator.py` | generate_response_stream 增加 memory_context |
| `agents/consultant/prompt_builder.py` | build_consultation_prompt 增加 memory_context |
| `agents/appointment_agent.py` | run_stream 增加 memory_context |
| `agents/appointment/input_parser.py` | parse_stream 增加 memory_context |
| `agents/appointment/appointment_processor.py` | handle_unrelated_request 增加 memory_context |

---

## 11. 后续扩展方向

1. **Redis 缓存层**: 将热 session 的上下文缓存在 Redis，减少 DB 查询
2. **Vector Memory**: 将对话摘要向量化，支持语义检索历史
3. **跨 session 长期记忆**: 将高置信度偏好（如 preferred_technician）跨 session 持久化
4. **增量压缩**: 不压缩全部旧消息，而是渐进式压缩，减少 LLM 调用成本
5. **对话质量评分**: 根据用户反馈调整语义记忆的置信度
