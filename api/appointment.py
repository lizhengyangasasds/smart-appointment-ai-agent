"""
简化的预约API

只保留第一版核心功能
"""
from fastapi import APIRouter, HTTPException
from .core.response_models import (
    AppointmentRequest,
    AppointmentResponse,
    DataResponse
)

router = APIRouter(prefix="/api/appointment", tags=["预约管理"])


@router.post("/create", response_model=DataResponse)
async def create_appointment(request: AppointmentRequest):
    """创建预约"""
    try:
        # 简化实现 - 直接导入需要的服务
        from agents.appointment_agent import AppointmentAgent
        agent = AppointmentAgent()
        result = await agent.process_appointment_request(request.dict())
        
        return DataResponse(
            message="预约创建成功",
            data=result
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
