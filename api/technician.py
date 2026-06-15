"""
技师API

提供技师信息和排班查询接口
"""

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from datetime import datetime
from pydantic import BaseModel

router = APIRouter(prefix="/api/technicians", tags=["技师管理"])


class TechnicianResponse(BaseModel):
    """技师信息响应"""
    id: int
    name: str
    gender: str
    strength: str


class ScheduleResponse(BaseModel):
    """排班信息响应"""
    id: int
    technician_id: int
    start_time: str
    end_time: str
    status: str
    appointment_id: int | None = None


@router.get("/", response_model=List[TechnicianResponse], summary="获取所有技师")
async def get_all_technicians():
    """获取所有技师信息"""
    try:
        from services.technician_service import TechnicianService
        technician_service = TechnicianService()
        technician_service.initialize_default_technicians()
        technicians = technician_service.get_all_technicians()
        
        return [
            TechnicianResponse(
                id=tech["id"],
                name=tech["name"],
                gender=tech.get("gender", ""),
                strength=tech.get("strength", "")
            )
            for tech in technicians
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取技师信息失败: {str(e)}")


@router.get("/{technician_id}/schedule", response_model=List[ScheduleResponse], summary="获取技师排班")
async def get_technician_schedule(technician_id: int):
    """获取指定技师今天的排班信息"""
    try:
        from services.technician_service import TechnicianService
        from config.time_config import time_config
        
        technician_service = TechnicianService()
        technician_service.initialize_default_technicians()
        
        # 获取技师信息确认存在
        tech = technician_service.get_technician_by_id(technician_id)
        if not tech:
            raise HTTPException(status_code=404, detail="技师不存在")
        
        # 获取今天的排班
        today = time_config.today()
        schedules = technician_service.get_technician_schedules(technician_id, today)
        
        return [
            ScheduleResponse(
                id=sched["id"],
                technician_id=sched["technician_id"],
                start_time=sched["start_time"].strftime("%H:%M"),
                end_time=sched["end_time"].strftime("%H:%M"),
                status=sched["status"],
                appointment_id=sched.get("appointment_id")
            )
            for sched in schedules
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取排班信息失败: {str(e)}")


@router.get("/{technician_id}", response_model=TechnicianResponse, summary="获取单个技师信息")
async def get_technician(technician_id: int):
    """获取指定技师的详细信息"""
    try:
        from services.technician_service import TechnicianService
        
        technician_service = TechnicianService()
        technician_service.initialize_default_technicians()
        tech = technician_service.get_technician_by_id(technician_id)
        
        if not tech:
            raise HTTPException(status_code=404, detail="技师不存在")
        
        return TechnicianResponse(
            id=tech["id"],
            name=tech["name"],
            gender=tech.get("gender", ""),
            strength=tech.get("strength", "")
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取技师信息失败: {str(e)}")


@router.get("/schedules/today", summary="获取所有技师今日排班")
async def get_all_technicians_schedule_today():
    """获取所有技师今天的排班信息"""
    try:
        from services.technician_service import TechnicianService
        from config.time_config import time_config
        
        technician_service = TechnicianService()
        technician_service.initialize_default_technicians()
        
        # 获取所有技师
        all_technicians = technician_service.get_all_technicians()
        today = time_config.today()
        
        schedules_data = []
        for tech in all_technicians:
            tech_id = tech["id"]
            tech_name = tech["name"]
            
            # 获取该技师今天的排班
            tech_schedules = technician_service.get_technician_schedules(tech_id, today)
            
            busy_periods = []
            for sched in tech_schedules:
                if sched.get("status") == "busy":
                    busy_periods.append({
                        "start": sched["start_time"].strftime("%H:%M") if hasattr(sched["start_time"], 'strftime') else str(sched["start_time"]),
                        "end": sched["end_time"].strftime("%H:%M") if hasattr(sched["end_time"], 'strftime') else str(sched["end_time"]),
                        "appointment_id": sched.get("appointment_id")
                    })
            
            schedules_data.append({
                "technician_id": tech_id,
                "technician_name": tech_name,
                "busy_periods": busy_periods
            })
        
        return schedules_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取排班信息失败: {str(e)}")
