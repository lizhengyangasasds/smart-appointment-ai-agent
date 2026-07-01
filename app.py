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
from services.reflection_service import get_reflection_service
from typing import List, Optional
import logging
import asyncio
import threading
import time

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

        # 预热反思服务（后台线程初始化，避免首次调用延迟）
        logger.info("🧠 启动反思服务预热...")
        _warm_up_reflection_service()

        logger.info("✅ 系统初始化完成！")

    except Exception as e:
        logger.error(f"❌ 系统初始化失败: {e}")
        raise


def _warm_up_reflection_service():
    """在独立线程中预热反思服务，避免阻塞启动流程"""
    def _bg_init():
        try:
            svc = get_reflection_service()
            if svc.is_available:
                logger.info("✅ 反思服务已就绪")
            else:
                logger.warning("⚠️ 反思服务未就绪，将在实际使用时重试")
        except Exception as e:
            logger.warning(f"⚠️ 反思服务预热失败: {e}")

    t = threading.Thread(target=_bg_init, daemon=True)
    t.start()


def _start_periodic_closed_loop():
    """
    启动周期性闭环任务（后台线程）

    每 6 小时运行一次完整的闭环周期：
    1. 获取反思洞察
    2. 生成策略更新
    3. 评估策略效果
    4. 自动回滚效果下降的策略

    日志写入 logger，不影响主服务响应。
    """
    def _run_cycle():
        logger.info("[闭环任务] 启动周期性闭环验证...")
        try:
            svc = get_reflection_service()
            if not svc.is_available:
                logger.warning("[闭环任务] 反思服务不可用，跳过本轮")
                return

            result = svc.run_closed_loop_cycle()
            strategies = result.get('strategies_updated', 0)
            evals = result.get('evaluation_results', [])

            logger.info(
                f"[闭环任务] 完成: 生成 {strategies} 个策略更新，"
                f"评估了 {len(evals)} 个策略"
            )

            for eval_item in evals:
                eval_type = eval_item.get('evaluation', 'unknown')
                rec = eval_item.get('recommendation', '')
                logger.info(f"  - {eval_item.get('strategy_type')}: {eval_type} — {rec}")

        except Exception as e:
            logger.error(f"[闭环任务] 执行失败: {e}")

    def _scheduler():
        # 首次执行：启动后等待 10 分钟，让系统先稳定运行
        time.sleep(600)
        while True:
            try:
                _run_cycle()
            except Exception as e:
                logger.error(f"[闭环任务] 异常: {e}")
            # 每 6 小时执行一次
            time.sleep(6 * 3600)

    t = threading.Thread(target=_scheduler, daemon=True)
    t.start()
    logger.info("✅ 周期性闭环任务已启动（每6小时执行一次）")

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
        allow_origins=["http://127.0.0.1:8000", "http://127.0.0.1:8001", "http://localhost:8000", "http://localhost:8001"],
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
        _start_periodic_closed_loop()

    return app

# 创建应用实例
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
