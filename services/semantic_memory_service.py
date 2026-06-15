"""
语义记忆服务 — 从对话中提取并管理用户偏好与关键事实

负责：
1. 从对话内容中提取实体（技师、时间、偏好、约束）
2. 将提取的记忆写入 DB
3. 向 Agent 提供格式化后的"用户画像"上下文
4. 支持置信度衰减（长时间未触发的偏好降低权重）
"""

import re
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta

from db.repositories.memory_repository import MemoryRepository


class SemanticExtractor:
    """
    语义提取器 — 从对话文本中提取结构化记忆

    使用正则 + 启发式规则提取：
    - 技师偏好（指定/避免）
    - 时间偏好（上午/下午/晚上）
    - 服务时长偏好
    - 服务项目偏好
    - 性别偏好
    - 特殊约束（如"不要某技师"、"只约周末"）
    """

    TECHNICIAN_PATTERN = re.compile(
        r'([\u4e00-\u9fff]{2,4}(?:技师|师傅|按摩师))|'
        r'(?:预约|找|指定)([\u4e00-\u9fff]{2,4})'
    )
    TIME_PREFERENCE_WORDS = {
        '上午': 'morning', '早上': 'morning', '早晨': 'morning',
        '下午': 'afternoon', '中午': 'noon',
        '晚上': 'evening', '夜里': 'night',
        '周末': 'weekend', '工作日': 'weekday',
    }
    DURATION_PATTERN = re.compile(r'(\d+)\s*(?:分钟|min|个小时?|小时)')
    PROJECT_WORDS = ['推拿', '按摩', '足疗', 'SPA', '刮痧', '拔罐', '肩颈', '全身', '局部']
    GENDER_WORDS = {'男': 'male', '女性': 'female', '女': 'female', '男生': 'male'}
    STRENGTH_WORDS = {'力气大': 'heavy', '力气小': 'light', '轻柔': 'light', '力度大': 'heavy', '力度小': 'light'}

    @classmethod
    def extract_from_text(cls, text: str, turn_index: Optional[int] = None) -> List[Dict[str, Any]]:
        """从文本中提取所有记忆实体"""
        memories = []

        for match in cls.TECHNICIAN_PATTERN.finditer(text):
            name = match.group(1) or match.group(2)
            if name and len(name) >= 2:
                memories.append({
                    'memory_type': 'preference',
                    'key': 'preferred_technician',
                    'value': name,
                    'confidence_delta': 1,
                    'source_turn': turn_index,
                })

        for word, value in cls.TIME_PREFERENCE_WORDS.items():
            if word in text:
                memories.append({
                    'memory_type': 'preference',
                    'key': 'time_preference',
                    'value': value,
                    'confidence_delta': 1,
                    'source_turn': turn_index,
                })

        duration_match = cls.DURATION_PATTERN.search(text)
        if duration_match:
            minutes = int(duration_match.group(1))
            if '小时' in text or '个钟' in text:
                minutes *= 60
            memories.append({
                'memory_type': 'preference',
                'key': 'duration_preference',
                'value': f'{minutes}分钟',
                'confidence_delta': 1,
                'source_turn': turn_index,
            })

        for project in cls.PROJECT_WORDS:
            if project in text:
                memories.append({
                    'memory_type': 'preference',
                    'key': 'project_preference',
                    'value': project,
                    'confidence_delta': 1,
                    'source_turn': turn_index,
                })

        for word, value in cls.GENDER_WORDS.items():
            if word in text:
                memories.append({
                    'memory_type': 'preference',
                    'key': 'technician_gender',
                    'value': value,
                    'confidence_delta': 1,
                    'source_turn': turn_index,
                })

        for word, value in cls.STRENGTH_WORDS.items():
            if word in text:
                memories.append({
                    'memory_type': 'preference',
                    'key': 'strength_preference',
                    'value': value,
                    'confidence_delta': 1,
                    'source_turn': turn_index,
                })

        if any(neg in text for neg in ['不', '不要', '拒绝', '避开', '不要找']):
            for match in cls.TECHNICIAN_PATTERN.finditer(text):
                name = match.group(1) or match.group(2)
                if name and len(name) >= 2:
                    memories.append({
                        'memory_type': 'constraint',
                        'key': 'avoid_technician',
                        'value': name,
                        'confidence_delta': 2,
                        'source_turn': turn_index,
                    })

        return memories


class SemanticMemoryService:
    """
    语义记忆服务

    职责：
    1. 管理语义记忆的 CRUD
    2. 从对话中自动提取并存储用户偏好
    3. 向 Agent 提供格式化后的用户画像
    4. 支持置信度更新（同一偏好多次出现时增加置信度）
    5. 支持过期机制（长时间未触发的记忆降低权重）
    """

    DEFAULT_EXPIRY_DAYS = 30
    DEFAULT_CONFIDENCE_DECAY_THRESHOLD = 7  # 超过7天未触发则衰减

    def __init__(
        self,
        memory_repo: MemoryRepository,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
        decay_threshold_days: int = DEFAULT_CONFIDENCE_DECAY_THRESHOLD,
    ):
        self.repo = memory_repo
        self.expiry_days = expiry_days
        self.decay_threshold_days = decay_threshold_days

    # ======================================
    # 记忆写入
    # ======================================

    def store_preference(
        self,
        session_id: str,
        key: str,
        value: str,
        user_id: Optional[str] = None,
        confidence_delta: int = 1,
        source_turn: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """存储或更新用户偏好"""
        self.repo.upsert_semantic_memory(
            session_id=session_id,
            user_id=user_id,
            memory_type='preference',
            key=key,
            value=value,
            confidence_delta=confidence_delta,
            source_turn=source_turn,
            metadata=metadata,
        )

    def store_fact(
        self,
        session_id: str,
        key: str,
        value: str,
        user_id: Optional[str] = None,
        source_turn: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """存储关键事实（如已完成预约、已取消等）"""
        self.repo.upsert_semantic_memory(
            session_id=session_id,
            user_id=user_id,
            memory_type='fact',
            key=key,
            value=value,
            confidence_delta=0,
            source_turn=source_turn,
            metadata=metadata,
        )

    def store_pattern(
        self,
        session_id: str,
        key: str,
        value: str,
        user_id: Optional[str] = None,
        source_turn: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """存储行为模式（如"用户总是选择同一位技师"）"""
        self.repo.upsert_semantic_memory(
            session_id=session_id,
            user_id=user_id,
            memory_type='pattern',
            key=key,
            value=value,
            confidence_delta=0,
            source_turn=source_turn,
            metadata=metadata,
        )

    def extract_and_store(self, text: str, session_id: str, user_id: Optional[str] = None, turn_index: Optional[int] = None) -> int:
        """
        从文本中提取语义记忆并存储

        返回：提取到的记忆条数
        """
        extracted = SemanticExtractor.extract_from_text(text, turn_index)
        for mem in extracted:
            self.repo.upsert_semantic_memory(
                session_id=session_id,
                user_id=user_id,
                memory_type=mem['memory_type'],
                key=mem['key'],
                value=mem['value'],
                confidence_delta=mem['confidence_delta'],
                source_turn=mem.get('source_turn'),
            )
        return len(extracted)

    # ======================================
    # 记忆查询
    # ======================================

    def get_user_profile(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        min_confidence: int = 1,
    ) -> str:
        """
        生成用户画像上下文字符串

        格式：
        【用户画像】
        - 技师偏好：张伟技师（置信度 3）
        - 时间偏好：下午（置信度 2）
        - ...
        """
        memories = self.repo.get_semantic_memories(
            session_id=session_id,
            user_id=user_id,
            min_confidence=min_confidence,
        )
        if not memories:
            return ""

        self._apply_confidence_decay(memories)

        lines = ["【用户画像】"]
        by_type = {}
        for m in memories:
            by_type.setdefault(m.memory_type, []).append(m)

        for mem_type, items in by_type.items():
            lines.append(f"\n  [{mem_type}]")
            for m in items:
                lines.append(f"  - {m.key}: {m.value}（置信度 {m.confidence}）")

        return "\n".join(lines)

    def get_preferences(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        获取用户偏好的字典格式（用于直接注入结构化数据）

        返回：{'preferred_technician': '张伟', 'time_preference': 'afternoon', ...}
        """
        memories = self.repo.get_preference_memories(
            session_id=session_id,
            user_id=user_id,
        )
        self._apply_confidence_decay(memories)

        prefs = {}
        for m in sorted(memories, key=lambda x: x.confidence, reverse=True):
            if m.key not in prefs:
                prefs[m.key] = m.value
        return prefs

    def get_recommendation_context(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        生成推荐相关的上下文（专供推荐系统使用）

        包含高置信度的偏好信息，用于技师匹配和服务推荐
        """
        prefs = self.get_preferences(session_id, user_id)
        if not prefs:
            return ""

        lines = ["【用户推荐上下文】"]
        if 'preferred_technician' in prefs:
            lines.append(f"用户偏好技师：{prefs['preferred_technician']}")
        if 'technician_gender' in prefs:
            lines.append(f"用户偏好性别：{prefs['technician_gender']}")
        if 'duration_preference' in prefs:
            lines.append(f"用户偏好时长：{prefs['duration_preference']}")
        if 'project_preference' in prefs:
            lines.append(f"用户偏好项目：{prefs['project_preference']}")
        if 'strength_preference' in prefs:
            lines.append(f"用户力度偏好：{prefs['strength_preference']}")
        if 'avoid_technician' in prefs:
            lines.append(f"用户避开技师：{prefs['avoid_technician']}")

        return "\n".join(lines)

    def boost_confidence(self, session_id: str, key: str, memory_type: str = 'preference') -> None:
        """当偏好被再次确认时，增加置信度"""
        self.repo.boost_semantic_memory_confidence(session_id, key, memory_type, delta=2)

    def reset_session_memory(self, session_id: str) -> dict:
        """重置指定 session 的语义记忆"""
        return {
            'memories_deleted': self.repo.clear_semantic_memories(session_id),
        }

    # ======================================
    # 内部方法
    # ======================================

    def _apply_confidence_decay(self, memories: List) -> None:
        """对过期的记忆进行置信度衰减"""
        now = datetime.utcnow()
        for m in memories:
            if m.updated_at and hasattr(m, 'decay_applied'):
                continue
            if m.updated_at:
                days_since_update = (now - m.updated_at).days
                if days_since_update > self.decay_threshold_days:
                    decay = min(m.confidence - 1, days_since_update - self.decay_threshold_days)
                    m.confidence = max(1, m.confidence - decay)
