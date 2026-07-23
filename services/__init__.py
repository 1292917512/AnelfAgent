"""顶层 Service 层 -- 封装 AnelfAgent 核心业务逻辑，供 Web/CLI 等前端共享。"""

from services._runtime import get_agent_app, get_runtime, is_ready, require_runtime
from services.adapter import AdapterService
from services.chat import ChatService
from services.database import DatabaseService
from services.entity import EntityService
from services.mcp import MCPService
from services.memory import MemoryService
from services.model import ModelService
from services.persona import PersonaService
from services.responses import ResponsesService
from services.status import AgentStatusService
from services.tag import TagService
from services.tool import ToolService

__all__ = [
    "is_ready",
    "get_runtime",
    "get_agent_app",
    "require_runtime",
    "AdapterService",
    "ChatService",
    "DatabaseService",
    "EntityService",
    "MCPService",
    "MemoryService",
    "ModelService",
    "PersonaService",
    "ResponsesService",
    "AgentStatusService",
    "TagService",
    "ToolService",
]
