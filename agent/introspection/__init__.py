"""内省系统：模块化编排器，统一管理反思单元与任务单元。"""

from .config import IntrospectionConfig, get_introspection_config
from .introspection_unit import (
    IntrospectionContext,
    IntrospectionResult,
    IntrospectionUnit,
    UnitMode,
    UnitScope,
)
from .orchestrator import Introspection
from .units import (
    EntityAnalysisUnit,
    MemoryHealthUnit,
    PromptBasedUnit,
    SelfReflectionUnit,
)

__all__ = [
    "Introspection",
    "IntrospectionConfig",
    "IntrospectionContext",
    "IntrospectionResult",
    "IntrospectionUnit",
    "UnitMode",
    "UnitScope",
    "PromptBasedUnit",
    "SelfReflectionUnit",
    "EntityAnalysisUnit",
    "MemoryHealthUnit",
    "get_introspection_config",
]
