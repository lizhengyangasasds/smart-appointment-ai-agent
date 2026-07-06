from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from datetime import datetime

Base = declarative_base()

class Technician(Base):
    __tablename__ = 'technicians'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    gender = Column(String, nullable=True)      # 新增性别字段
    strength = Column(String, nullable=True)    # 新增力气/倾向性字段
    schedules = relationship("TechnicianSchedule", back_populates="technician", cascade="all, delete-orphan")

class TechnicianSchedule(Base):
    __tablename__ = 'technician_schedules'
    id = Column(Integer, primary_key=True)
    technician_id = Column(Integer, ForeignKey('technicians.id'))
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False)  # 'busy' or 'free'
    appointment_id = Column(Integer, nullable=True)
    technician = relationship("Technician", back_populates="schedules")

class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    keywords = Column(JSON, nullable=True)  # 存储关键词列表
    embedding = Column(JSON, nullable=True)  # 存储嵌入向量
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 软删除标记

class UserBehavior(Base):
    __tablename__ = 'user_behaviors'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')  # 单用户场景使用默认用户ID
    action_type = Column(String, nullable=False)  # 'appointment', 'consultation', 'inquiry'
    action_data = Column(JSON, nullable=True)  # 存储行为相关的详细数据
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    session_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    technician = relationship("Technician")

class UserPreference(Base):
    __tablename__ = 'user_preferences'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')
    preference_type = Column(String, nullable=False)  # 'technician', 'time', 'service', 'duration'
    preference_value = Column(String, nullable=False)
    confidence_score = Column(Integer, default=1)  # 偏好的置信度（出现次数）
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserRecommendation(Base):
    __tablename__ = 'user_recommendations'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, default='default_user')
    recommendation_type = Column(String, nullable=False)  # 'technician_available', 'return_reminder', 'service_suggestion'
    content = Column(Text, nullable=False)
    technician_id = Column(Integer, ForeignKey('technicians.id'), nullable=True)
    is_sent = Column(Integer, default=0)  # 是否已发送
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    technician = relationship("Technician")


class TaskEvaluation(Base):
    """任务评估表 - 记录每次任务执行的评估结果"""
    __tablename__ = 'task_evaluations'
    id = Column(Integer, primary_key=True)
    session_id = Column(String, nullable=False)  # 会话ID
    task_type = Column(String, nullable=False)  # 'appointment', 'consultation', 'classification'
    success = Column(Integer, default=0)  # 是否成功: 0=失败, 1=成功, 2=部分成功
    success_rate = Column(Float, default=0.0)  # 成功率评分 0-1
    completion_time = Column(Float, nullable=True)  # 完成耗时(秒)
    turns_count = Column(Integer, default=0)  # 对话轮数
    error_type = Column(String, nullable=True)  # 错误类型
    error_message = Column(Text, nullable=True)  # 错误信息
    action_data = Column(JSON, nullable=True)  # 任务相关数据
    reflection_triggered = Column(Integer, default=0)  # 是否触发反思
    created_at = Column(DateTime, default=datetime.utcnow)


class ReflectionLog(Base):
    """反思日志表 - 记录反思过程和结论"""
    __tablename__ = 'reflection_logs'
    id = Column(Integer, primary_key=True)
    session_id = Column(String, nullable=False)  # 会话ID
    evaluation_id = Column(Integer, ForeignKey('task_evaluations.id'), nullable=True)  # 关联评估
    reflection_type = Column(String, nullable=False)  # 'post_task', 'periodic', 'threshold_triggered'
    findings = Column(JSON, nullable=False)  # 反思发现
    recommendations = Column(JSON, nullable=True)  # 改进建议
    patterns_discovered = Column(JSON, nullable=True)  # 发现的模式
    bad_cases = Column(JSON, nullable=True)  # 坏case记录
    improvement_actions = Column(JSON, nullable=True)  # 已采取的改进措施
    created_at = Column(DateTime, default=datetime.utcnow)


class UserFeedback(Base):
    """用户反馈表 - 记录用户的显式反馈"""
    __tablename__ = 'user_feedbacks'
    id = Column(Integer, primary_key=True)
    session_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False, default='default_user')
    feedback_type = Column(String, nullable=False)  # 'rating', 'correction', 'complaint', 'praise'
    rating = Column(Integer, nullable=True)  # 评分 1-5
    content = Column(Text, nullable=True)  # 反馈内容
    source = Column(String, default='explicit')  # 'explicit'=显式, 'implicit'=隐式
    action_data = Column(JSON, nullable=True)  # 相关行为数据
    created_at = Column(DateTime, default=datetime.utcnow)
