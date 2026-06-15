"""
记忆系统 Repository — 数据访问层

提供 ConversationMessage、SemanticMemory、SessionSummary 的 CRUD 操作。
支持按 session_id、user_id 聚合查询，以及批量压缩和软删除。
"""

from typing import List, Optional, Tuple
from datetime import datetime
from sqlalchemy import and_, func, update

from db.models_memory import ConversationMessage, SemanticMemory, SessionSummary


class MemoryRepository:
    """
    记忆数据访问层

    职责：
    1. 对话消息的增删改查（工作记忆/情景记忆）
    2. 语义记忆的增删改查（用户偏好、关键事实）
    3. 会话摘要的创建与查询（压缩后保留的摘要）
    4. 批量标记已压缩消息

    每次操作都通过 SessionManager 获取独立的 session，确保连接安全。
    """

    def __init__(self, session_manager):
        """
        Args:
            session_manager: SessionManager 实例（注意：不是 raw session）
        """
        self._sm = session_manager

    # ======================================
    # 对话消息 (ConversationMessage)
    # ======================================

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: Optional[str] = None,
        agent_tag: Optional[str] = None,
        turn_index: int = 0,
        message_type: Optional[str] = None,
        token_count: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> ConversationMessage:
        """写入一条对话消息"""
        with self._sm.session_scope(exclusive=True) as db:
            msg = ConversationMessage(
                session_id=session_id,
                user_id=user_id,
                role=role,
                content=content,
                agent_tag=agent_tag,
                turn_index=turn_index,
                message_type=message_type,
                token_count=token_count,
                metadata=metadata,
            )
            db.add(msg)
            db.flush()
            return msg

    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        offset: int = 0,
        include_compressed: bool = True,
    ) -> List[ConversationMessage]:
        """获取指定 session 的对话消息（按 turn_index 升序）"""
        with self._sm.session_scope() as db:
            query = db.query(ConversationMessage).filter(
                ConversationMessage.session_id == session_id
            )
            if not include_compressed:
                query = query.filter(ConversationMessage.is_compressed == 0)
            query = query.order_by(ConversationMessage.turn_index.asc())
            if offset:
                query = query.offset(offset)
            if limit:
                query = query.limit(limit)
            return query.all()

    def get_latest_turn_index(self, session_id: str) -> int:
        """获取指定 session 最新的 turn_index"""
        with self._sm.session_scope() as db:
            row = (
                db.query(func.max(ConversationMessage.turn_index))
                .filter(ConversationMessage.session_id == session_id)
                .scalar()
            )
            return row or -1

    def mark_messages_compressed(
        self,
        session_id: str,
        turn_start: int,
        turn_end: int,
    ) -> int:
        """批量标记指定轮次范围的消息为已压缩"""
        with self._sm.session_scope(exclusive=True) as db:
            result = db.execute(
                update(ConversationMessage)
                .where(
                    and_(
                        ConversationMessage.session_id == session_id,
                        ConversationMessage.turn_index >= turn_start,
                        ConversationMessage.turn_index <= turn_end,
                    )
                )
                .values(is_compressed=1)
            )
            return result.rowcount

    def get_message_count(self, session_id: str, include_compressed: bool = False) -> int:
        """获取指定 session 的消息总数"""
        with self._sm.session_scope() as db:
            query = db.query(func.count(ConversationMessage.id)).filter(
                ConversationMessage.session_id == session_id
            )
            if not include_compressed:
                query = query.filter(ConversationMessage.is_compressed == 0)
            return query.scalar()

    def get_uncompressed_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> List[ConversationMessage]:
        """获取未压缩的消息（用于构建上下文）"""
        with self._sm.session_scope() as db:
            query = (
                db.query(ConversationMessage)
                .filter(
                    and_(
                        ConversationMessage.session_id == session_id,
                        ConversationMessage.is_compressed == 0,
                    )
                )
                .order_by(ConversationMessage.turn_index.asc())
            )
            if limit:
                query = query.limit(limit)
            return query.all()

    def clear_session_messages(self, session_id: str) -> int:
        """清除指定 session 的所有消息（用于会话重置）"""
        with self._sm.session_scope(exclusive=True) as db:
            result = db.query(ConversationMessage).filter(
                ConversationMessage.session_id == session_id
            ).delete(synchronize_session=False)
            return result

    # ======================================
    # 语义记忆 (SemanticMemory)
    # ======================================

    def upsert_semantic_memory(
        self,
        session_id: str,
        memory_type: str,
        key: str,
        value: str,
        user_id: Optional[str] = None,
        confidence_delta: int = 1,
        source_turn: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> SemanticMemory:
        """
        插入或更新语义记忆

        如果 (session_id, memory_type, key) 已存在，则增加置信度并更新值。
        """
        with self._sm.session_scope(exclusive=True) as db:
            existing = (
                db.query(SemanticMemory)
                .filter(
                    and_(
                        SemanticMemory.session_id == session_id,
                        SemanticMemory.memory_type == memory_type,
                        SemanticMemory.key == key,
                        SemanticMemory.is_active == 1,
                    )
                )
                .first()
            )
            if existing:
                existing.value = value
                existing.confidence = existing.confidence + confidence_delta
                existing.updated_at = datetime.utcnow()
                if metadata:
                    existing.metadata = metadata
                db.flush()
                return existing
            else:
                mem = SemanticMemory(
                    session_id=session_id,
                    user_id=user_id,
                    memory_type=memory_type,
                    key=key,
                    value=value,
                    confidence=confidence_delta,
                    source_turn=source_turn,
                    metadata=metadata,
                )
                db.add(mem)
                db.flush()
                return mem

    def get_semantic_memories(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        min_confidence: int = 1,
        limit: Optional[int] = None,
    ) -> List[SemanticMemory]:
        """查询语义记忆"""
        with self._sm.session_scope() as db:
            query = db.query(SemanticMemory).filter(SemanticMemory.is_active == 1)
            if session_id:
                query = query.filter(SemanticMemory.session_id == session_id)
            if user_id:
                query = query.filter(SemanticMemory.user_id == user_id)
            if memory_type:
                query = query.filter(SemanticMemory.memory_type == memory_type)
            if min_confidence > 1:
                query = query.filter(SemanticMemory.confidence >= min_confidence)
            query = query.order_by(SemanticMemory.confidence.desc(), SemanticMemory.updated_at.desc())
            if limit:
                query = query.limit(limit)
            return query.all()

    def get_preference_memories(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[SemanticMemory]:
        """专门获取用户偏好类语义记忆"""
        return self.get_semantic_memories(
            session_id=session_id,
            user_id=user_id,
            memory_type='preference',
        )

    def deactivate_semantic_memory(self, memory_id: int) -> bool:
        """软删除语义记忆"""
        with self._sm.session_scope(exclusive=True) as db:
            result = db.execute(
                update(SemanticMemory)
                .where(SemanticMemory.id == memory_id)
                .values(is_active=0)
            )
            return result.rowcount > 0

    def boost_semantic_memory_confidence(self, session_id: str, key: str, memory_type: str = 'preference', delta: int = 2) -> bool:
        """增加指定记忆的置信度"""
        with self._sm.session_scope(exclusive=True) as db:
            result = db.execute(
                update(SemanticMemory)
                .where(
                    and_(
                        SemanticMemory.session_id == session_id,
                        SemanticMemory.memory_type == memory_type,
                        SemanticMemory.key == key,
                        SemanticMemory.is_active == 1,
                    )
                )
                .values(
                    confidence=SemanticMemory.confidence + delta,
                    updated_at=datetime.utcnow(),
                )
            )
            return result.rowcount > 0

    def clear_semantic_memories(self, session_id: str) -> int:
        """清除指定 session 的所有语义记忆"""
        with self._sm.session_scope(exclusive=True) as db:
            result = db.query(SemanticMemory).filter(
                SemanticMemory.session_id == session_id
            ).delete(synchronize_session=False)
            return result

    # ======================================
    # 会话摘要 (SessionSummary)
    # ======================================

    def add_summary(
        self,
        session_id: str,
        summary_text: str,
        summary_turn_start: int,
        summary_turn_end: int,
        token_count: Optional[int] = None,
    ) -> SessionSummary:
        """创建会话摘要"""
        with self._sm.session_scope(exclusive=True) as db:
            summary = SessionSummary(
                session_id=session_id,
                summary_text=summary_text,
                summary_turn_start=summary_turn_start,
                summary_turn_end=summary_turn_end,
                token_count=token_count,
            )
            db.add(summary)
            db.flush()
            return summary

    def get_summaries(self, session_id: str) -> List[SessionSummary]:
        """获取指定 session 的所有摘要（按时间升序）"""
        with self._sm.session_scope() as db:
            return (
                db.query(SessionSummary)
                .filter(SessionSummary.session_id == session_id)
                .order_by(SessionSummary.summary_turn_end.asc())
                .all()
            )

    def get_latest_summary(self, session_id: str) -> Optional[SessionSummary]:
        """获取最新的会话摘要"""
        with self._sm.session_scope() as db:
            return (
                db.query(SessionSummary)
                .filter(SessionSummary.session_id == session_id)
                .order_by(SessionSummary.summary_turn_end.desc())
                .first()
            )

    def clear_summaries(self, session_id: str) -> int:
        """清除指定 session 的所有摘要"""
        with self._sm.session_scope(exclusive=True) as db:
            result = db.query(SessionSummary).filter(
                SessionSummary.session_id == session_id
            ).delete(synchronize_session=False)
            return result

    # ======================================
    # 批量清理（会话重置时）
    # ======================================

    def clear_session(self, session_id: str) -> dict:
        """清除指定 session 的所有记忆数据"""
        with self._sm.session_scope(exclusive=True) as db:
            msg_count = db.query(ConversationMessage).filter(
                ConversationMessage.session_id == session_id
            ).delete(synchronize_session=False)
            mem_count = db.query(SemanticMemory).filter(
                SemanticMemory.session_id == session_id
            ).delete(synchronize_session=False)
            sum_count = db.query(SessionSummary).filter(
                SessionSummary.session_id == session_id
            ).delete(synchronize_session=False)
            return {
                'messages_deleted': msg_count,
                'memories_deleted': mem_count,
                'summaries_deleted': sum_count,
            }
