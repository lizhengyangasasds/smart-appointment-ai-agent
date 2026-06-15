"""
简化的API响应模型

只保留第一版真正需要的核心功能
"""
from pydantic import BaseModel
from typing import Any, Dict, Optional
from datetime import datetime
from config.time_config import time_config


class BaseResponse(BaseModel):
    """基础响应模型"""
    message: str
    timestamp: datetime = time_config.now()


class DataResponse(BaseResponse):
    """数据响应模型"""
    data: Any


# 预约相关模型
class AppointmentRequest(BaseModel):
    user_id: str
    service_type: str
    preferred_time: str
    notes: Optional[str] = None


class AppointmentResponse(BaseModel):
    appointment_id: str
    user_id: str
    service_type: str
    scheduled_time: str
    status: str
    notes: Optional[str] = None


# 咨询相关模型
class ConsultationRequest(BaseModel):
    user_id: str
    question: str
    category: Optional[str] = None


class ConsultationResponse(BaseModel):
    consultation_id: str
    question: str
    answer: str
    category: Optional[str] = None


# 用户行为相关模型
class UserBehaviorRequest(BaseModel):
    user_id: str
    action: str
    context: Optional[Dict[str, Any]] = None


class UserBehaviorResponse(BaseModel):
    user_id: str
    action: str
    timestamp: datetime
    context: Optional[Dict[str, Any]] = None


# 任务分类相关模型
class TaskClassificationRequest(BaseModel):
    text: str
    context: Optional[Dict[str, Any]] = None


class TaskClassificationResponse(BaseModel):
    text: str
    category: str
    confidence: float
    reasoning: Optional[str] = None
