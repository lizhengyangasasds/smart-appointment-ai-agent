"""
FastAPI应用程序

主应用程序入口，配置中间件、路由和异常处理
自动初始化知识库和技师数据
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from services.knowledge_service import KnowledgeService
from services.technician_service import TechnicianService
from services.recommendation_service import RecommendationService
from typing import List, Optional
import logging
import asyncio

# 导入路由
from api import api_routers
from api.core.exceptions import api_exception_handler, general_exception_handler, BusinessException
from web import router as web_router

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic模型
from pydantic import BaseModel

class KnowledgeRequest(BaseModel):
    content: str
    category: str
    keywords: List[str] = []

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    category: Optional[str] = None

async def initialize_system():
    """系统启动时自动初始化"""
    try:
        logger.info("🚀 正在初始化智能预约系统...")
        
        # 初始化知识库服务
        logger.info("📚 初始化知识库服务...")
        knowledge_service = KnowledgeService()
        await knowledge_service.initialize()
        
        # 初始化技师服务
        logger.info("👨‍⚕️ 初始化技师服务...")
        technician_service = TechnicianService()
        technician_service.initialize_default_technicians()
        
        # 初始化推荐服务
        logger.info("🎯 启动推荐调度服务...")
        recommendation_service = RecommendationService()
        if recommendation_service.start_scheduler():
            logger.info("✅ 推荐调度服务启动成功")
        else:
            logger.warning("⚠️ 推荐调度服务启动失败")
        
        logger.info("✅ 系统初始化完成！")
        
    except Exception as e:
        logger.error(f"❌ 系统初始化失败: {e}")
        raise

def create_app() -> FastAPI:
    """创建FastAPI应用实例"""
    
    app = FastAPI(
        title="智能预约AI代理",
        description="提供预约管理、智能咨询、用户行为分析等功能的API服务",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # 添加CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境中应该设置具体的域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册异常处理器
    app.add_exception_handler(BusinessException, api_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # 注册API路由
    for router in api_routers:
        app.include_router(router)

    # 注册Web界面路由
    app.include_router(web_router)

    # 静态文件
    app.mount("/static", StaticFiles(directory="web/static"), name="static")

    # 添加启动事件
    @app.on_event("startup")
    async def startup_event():
        """应用启动时自动初始化系统"""
        await initialize_system()

    return app

# 创建应用实例
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
