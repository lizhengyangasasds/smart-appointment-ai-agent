"""
记忆系统数据库模型

三层记忆存储：
- ConversationMessage: 工作记忆（情景记忆）— 对话消息
- SemanticMemory: 语义记忆 — 用户偏好、关键事实
- SessionSummary: 压缩摘要 — 对话压缩后的摘要
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from db.models import Base


class ConversationMessage(Base):
    """
    对话消息表 — 存储每一轮对话的原始记录

    作为"工作记忆 + 情景记忆"的底层存储，支持：
    - 按 session_id 快速查询对话历史
    - 按时间范围检索
    - 标记消息是否已压缩
    """
    __tablename__ = 'conversation_messages'

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    role = Column(String(16), nullable=False)  # 'user' | 'assistant' | 'system'
    content = Column(Text, nullable=False)
    agent_tag = Column(String(32), nullable=True)  # '[咨询机器人]' | '[预约机器人]' 等
    turn_index = Column(Integer, nullable=False)  # 第几轮对话（从0开始）
    message_type = Column(String(32), nullable=True)  # 'appointment' | 'consultation' | 'classification'
    is_compressed = Column(Integer, default=0)  # 0=原始消息, 1=已被摘要压缩
    token_count = Column(Integer, nullable=True)  # 预估token数
    extra_data = Column(JSON, nullable=True)  # 额外信息（如分类结果、提取的实体等）
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index('idx_session_turn', 'session_id', 'turn_index'),
        Index('idx_session_compressed', 'session_id', 'is_compressed'),
        Index('idx_user_session', 'user_id', 'session_id'),
    )

    def to_summary_text(self) -> str:
        """将消息转换为摘要文本"""
        prefix = "用户" if self.role == "user" else "助手"
        return f"{prefix}：{self.content}"


class SemanticMemory(Base):
    """
    语义记忆表 — 存储用户偏好、关键事实等结构化知识

    作为"语义记忆"层的存储：
    - 从对话中提取的用户偏好（技师、时长、项目等）
    - 关键事实（上次预约时间、取消记录等）
    - 多轮交互后沉淀的重要信息
    """
    __tablename__ = 'semantic_memories'

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    memory_type = Column(String(32), nullable=False)  # 'preference' | 'fact' | 'constraint' | 'pattern'
    key = Column(String(128), nullable=False)  # 'preferred_technician' | 'preferred_time' | 'avoid_technician'
    value = Column(Text, nullable=False)  # 记忆值，如 '张伟技师' | '下午' | '从不在周末预约'
    confidence = Column(Integer, default=1)  # 置信度/出现次数
    source_turn = Column(Integer, nullable=True)  # 来源于哪轮对话
    is_active = Column(Integer, default=1)  # 软删除标记
    expires_at = Column(DateTime, nullable=True)  # 可选过期时间
    extra_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_user_memory', 'user_id', 'memory_type'),
        Index('idx_session_type_key', 'session_id', 'memory_type', 'key'),
    )

    def to_context_text(self) -> str:
        """将记忆转换为上下文字符串"""
        return f"{self.key}: {self.value}（置信度 {self.confidence}）"


class SessionSummary(Base):
    """
    会话摘要表 — 存储压缩后的对话摘要

    当对话轮次过多需要压缩时：
    1. 将旧消息汇总为一段摘要
    2. 删除旧消息记录（或标记为已压缩）
    3. 保留摘要和摘要之后的新消息
    """
    __tablename__ = 'session_summaries'

    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, index=True)
    summary_text = Column(Text, nullable=False)  # 摘要内容
    summary_turn_start = Column(Integer, nullable=False)  # 摘要覆盖的起始轮次
    summary_turn_end = Column(Integer, nullable=False)  # 摘要覆盖的结束轮次
    token_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_session_summary', 'session_id', 'summary_turn_end'),
    )
