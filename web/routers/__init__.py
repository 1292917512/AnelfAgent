"""Web API 路由集合。"""

from fastapi import APIRouter

from web.routers.auth import router as auth_router
from web.routers.adapters import router as adapters_router
from web.routers.approvals import router as approvals_router
from web.routers.chat import router as chat_router
from web.routers.config import router as config_router
from web.routers.config_meta import router as config_meta_router
from web.routers.entities import router as entities_router
from web.routers.mcp import router as mcp_router
from web.routers.memory import router as memory_router
from web.routers.models import router as models_router
from web.routers.nonebot import router as nonebot_router
from web.routers.personas import router as personas_router
from web.routers.skills import router as skills_router
from web.routers.status import router as status_router
from web.routers.system import router as system_router
from web.routers.tags import router as tags_router
from web.routers.thinking import router as thinking_router
from web.routers.tools import router as tools_router

api_router = APIRouter(prefix="/api")

api_router.include_router(auth_router)
api_router.include_router(config_router)
api_router.include_router(config_meta_router)
api_router.include_router(chat_router)
api_router.include_router(status_router)
api_router.include_router(models_router)
api_router.include_router(tools_router)
api_router.include_router(tags_router)
api_router.include_router(personas_router)
api_router.include_router(skills_router)
api_router.include_router(memory_router)
api_router.include_router(mcp_router)
api_router.include_router(adapters_router)
api_router.include_router(approvals_router)
api_router.include_router(nonebot_router)
api_router.include_router(system_router)
api_router.include_router(entities_router)
api_router.include_router(thinking_router)
