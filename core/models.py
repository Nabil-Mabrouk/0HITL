from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timezone
from enum import Enum

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class ToolCall(BaseModel):
    id: str
    function: str
    arguments: str # JSON string coming from LLM

class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    id: str = Field(default_factory=lambda: f"msg_{datetime.now().timestamp()}")
    parent_id: Optional[str] = None
    role: Role
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None  # Refers to ToolCall ID if role == tool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AgentSession(BaseModel):
    session_id: str
    history: List[Message] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
