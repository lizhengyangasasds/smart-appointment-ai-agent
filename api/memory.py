"""
记忆管理 API — 提供记忆系统的调试和管理接口
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.chat_handler import (
    get_session_status,
    reset_session,
    list_active_sessions,
    get_memory_context,
    get_recommendation_context,
)

router = APIRouter(prefix="/api/memory", tags=["记忆管理"])


class ResetRequest(BaseModel):
    session_id: str


class SessionStatusRequest(BaseModel):
    session_id: str


class ContextRequest(BaseModel):
    session_id: str
    include_profile: bool = True


@router.post("/reset", summary="重置会话记忆")
async def reset_memory(req: ResetRequest):
    """重置指定 session 的所有记忆数据"""
    result = reset_session(req.session_id)
    return {"success": True, "session_id": req.session_id, "result": result}


@router.get("/status/{session_id}", summary="获取会话状态")
async def get_status(session_id: str):
    """获取指定 session 的记忆状态"""
    status = get_session_status(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return status


@router.get("/context/{session_id}", summary="获取记忆上下文")
async def get_context(session_id: str, include_profile: bool = True):
    """获取指定 session 的完整记忆上下文"""
    context = get_memory_context(session_id, include_profile)
    return {"session_id": session_id, "context": context}


@router.get("/recommendation/{session_id}", summary="获取推荐上下文")
async def get_rec_context(session_id: str):
    """获取指定 session 的推荐相关上下文"""
    context = get_recommendation_context(session_id)
    return {"session_id": session_id, "recommendation_context": context}


@router.get("/active-sessions", summary="列出活跃会话")
async def list_sessions():
    """列出当前活跃的所有 session IDs"""
    sessions = list_active_sessions()
    return {"active_sessions": sessions, "count": len(sessions)}
