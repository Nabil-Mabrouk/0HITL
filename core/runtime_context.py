from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolRuntimeContext:
    session_id: str
    tool_name: Optional[str] = None


_current_runtime_context: ContextVar[Optional[ToolRuntimeContext]] = ContextVar(
    "current_tool_runtime_context",
    default=None,
)


def get_current_runtime_context() -> Optional[ToolRuntimeContext]:
    return _current_runtime_context.get()


def get_current_session_id(default: Optional[str] = None) -> Optional[str]:
    context = get_current_runtime_context()
    if context is None:
        return default
    return context.session_id


@contextmanager
def tool_runtime_context(session_id: str, tool_name: Optional[str] = None):
    token = _current_runtime_context.set(
        ToolRuntimeContext(session_id=session_id, tool_name=tool_name)
    )
    try:
        yield
    finally:
        _current_runtime_context.reset(token)
