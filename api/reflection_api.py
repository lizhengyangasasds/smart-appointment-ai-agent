"""
反思 API 接口示例

FastAPI 风格的反思功能接口
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

router = APIRouter(prefix="/api/reflection", tags=["反思"])


# ============== 请求模型 ==============

class AppointmentReflectionRequest(BaseModel):
    session_id: str
    appointment_history: dict
    turns_count: int
    completion_time: Optional[float] = None
    error: Optional[str] = None


class ConsultationReflectionRequest(BaseModel):
    session_id: str
    consultation_data: dict
    turns_count: int
    completion_time: Optional[float] = None
    error: Optional[str] = None


class FeedbackRequest(BaseModel):
    session_id: str
    feedback_type: str  # rating/correction/complaint/praise
    rating: Optional[int] = None
    content: Optional[str] = None


# ============== API 接口 ==============

@router.post("/appointment")
async def reflect_appointment(request: AppointmentReflectionRequest):
    """
    反思预约任务
    
    在预约完成后调用此接口进行反思分析
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    
    error = None
    if request.error:
        error = Exception(request.error)
    
    result = await reflection.reflect_on_appointment(
        session_id=request.session_id,
        appointment_history=request.appointment_history,
        turns_count=request.turns_count,
        completion_time=request.completion_time,
        error=error
    )
    
    return {
        "success": True,
        "data": {
            "evaluation": result['evaluation'],
            "should_improve": result['evaluation']['should_reflect'],
            "reflection_summary": result['report'].get('reflection_content', '')
        }
    }


@router.post("/consultation")
async def reflect_consultation(request: ConsultationReflectionRequest):
    """
    反思咨询任务
    
    在咨询完成后调用此接口进行反思分析
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    
    error = None
    if request.error:
        error = Exception(request.error)
    
    result = await reflection.reflect_on_consultation(
        session_id=request.session_id,
        consultation_data=request.consultation_data,
        turns_count=request.turns_count,
        completion_time=request.completion_time,
        error=error
    )
    
    return {
        "success": True,
        "data": {
            "evaluation": result['evaluation'],
            "should_improve": result['evaluation']['should_reflect']
        }
    }


@router.post("/feedback")
async def submit_feedback(request: FeedbackRequest):
    """
    提交用户反馈
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    
    feedback_id = reflection.record_explicit_feedback(
        session_id=request.session_id,
        feedback_type=request.feedback_type,
        rating=request.rating,
        content=request.content
    )
    
    return {
        "success": True,
        "data": {
            "feedback_id": feedback_id
        }
    }


@router.get("/insights")
async def get_insights(days: int = 7):
    """
    获取反思洞察
    
    返回最近的反思洞察、可执行建议和坏case
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    insights = reflection.get_insights(days=days)
    
    return {
        "success": True,
        "data": insights
    }


@router.get("/report/weekly")
async def get_weekly_report():
    """
    获取周报
    
    返回过去7天的反思报告
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    report = reflection.get_weekly_report()
    
    return {
        "success": True,
        "data": report
    }


@router.get("/report/monthly")
async def get_monthly_report():
    """
    获取月报
    
    返回过去30天的反思报告
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    report = reflection.get_monthly_report()
    
    return {
        "success": True,
        "data": report
    }


@router.get("/statistics")
async def get_statistics(days: int = 30):
    """
    获取评估统计
    
    返回各任务类型的成功率统计
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    stats = reflection.get_statistics(days=days)
    
    return {
        "success": True,
        "data": stats
    }


@router.get("/dashboard")
async def get_dashboard():
    """
    获取仪表盘数据
    
    返回用于展示的仪表盘摘要数据
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    dashboard = reflection.get_dashboard()
    
    return {
        "success": True,
        "data": dashboard
    }


@router.get("/diagnose/{session_id}")
async def diagnose_session(session_id: str):
    """
    诊断特定会话
    
    分析某个会话的问题并给出建议
    """
    from agents.reflection_agent import ReflectionAgent
    
    reflection = ReflectionAgent()
    diagnosis = await reflection.diagnose_issue(session_id)
    
    if "error" in diagnosis:
        raise HTTPException(status_code=404, detail=diagnosis["error"])
    
    return {
        "success": True,
        "data": diagnosis
    }


# ============== 使用示例 ==============

"""
curl 使用示例:

# 1. 反思预约任务
curl -X POST http://localhost:8000/api/reflection/appointment \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess_123",
    "appointment_history": {
      "gender": "male",
      "start_time": "2024-01-15T14:00:00",
      "duration": 60,
      "project": "全身按摩"
    },
    "turns_count": 4,
    "completion_time": 45.5
  }'

# 2. 提交用户反馈
curl -X POST http://localhost:8000/api/reflection/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess_123",
    "feedback_type": "rating",
    "rating": 5,
    "content": "服务很好"
  }'

# 3. 获取周报
curl http://localhost:8000/api/reflection/report/weekly

# 4. 获取洞察
curl http://localhost:8000/api/reflection/insights?days=7

# 5. 获取仪表盘
curl http://localhost:8000/api/reflection/dashboard
"""
