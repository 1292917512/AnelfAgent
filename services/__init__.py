"""顶层 Service 层 -- 封装 AnelfAgent 核心业务逻辑，供 Web/CLI 等前端共享。"""

from services._runtime import get_agent_app, get_runtime, is_ready, require_runtime
from services.adapter import AdapterService
from services.approval import ApprovalService
from services.chat import ChatService
from services.config import ConfigService
from services.context import ContextService
from services.database import DatabaseService
from services.entity import EntityService
from services.graph import GraphService
from services.heartbeat import HeartbeatService
from services.mcp import MCPService
from services.memory import MemoryService
from services.model import ModelService
from services.persona import PersonaService
from services.responses import ResponsesService
from services.status import AgentStatusService
from services.sticker import StickerService
from services.system import SystemService
from services.tag import TagService
from services.task import TaskService
from services.tool import ToolService
from services.ui import UiService

__all__ = [
    "is_ready",
    "get_runtime",
    "get_agent_app",
    "require_runtime",
    "AdapterService",
    "ApprovalService",
    "ChatService",
    "ConfigService",
    "ContextService",
    "DatabaseService",
    "EntityService",
    "GraphService",
    "HeartbeatService",
    "MCPService",
    "MemoryService",
    "ModelService",
    "PersonaService",
    "ResponsesService",
    "AgentStatusService",
    "StickerService",
    "SystemService",
    "TagService",
    "TaskService",
    "ToolService",
    "UiService",
]
