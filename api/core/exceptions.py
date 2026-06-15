"""
简化的API异常处理

只保留第一版真正需要的核心功能
"""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)


class BusinessException(HTTPException):
    """业务逻辑异常"""
    
    def __init__(self, message: str):
        super().__init__(status_code=400, detail=message)


async def api_exception_handler(request: Request, exc: BusinessException):
    """API异常处理器"""
    logger.error(f"业务异常: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )


async def general_exception_handler(request: Request, exc: Exception):
    """通用异常处理器"""
    import traceback
    error_detail = f"未处理异常: {str(exc)}"
    stack_trace = traceback.format_exc()
    logger.error(f"{error_detail}\n{stack_trace}")
    
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误"}
    )
