from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class Tool(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


class ModelContext(BaseModel):
    system_prompt: str = ""
    messages: List[Message] = Field(default_factory=list)
    tools: List[Tool] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)




class Attachment(BaseModel):
    name: str
    type: Optional[str] = None
    url: Optional[str] = None
    size: Optional[int] = None
    content_hash: Optional[str] = None
    origin: Optional[str] = None
    relative_path: Optional[str] = None
    capture_source: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None  # Add session tracking
    # Frontend may provide a per-message id for streaming/association; ignore if unused
    message_id: Optional[str] = None  # noqa: used for client correlation only
    model: Optional[str] = None
    mode: Optional[str] = None
    attachments: List[Attachment] = Field(default_factory=list)
    vision_workflow: Optional[str] = "auto"
    use_rag: Optional[bool] = True
    patience: Optional[int] = 1
    thinking: Optional[Union[bool, str]] = None
    context: Optional[ModelContext] = None


class ChatResponse(BaseModel):
    message: str
    thought: Optional[str] = None
    tools_used: Optional[List[Union[str, Dict[str, Any]]]] = None
    metadata: Optional[Dict[str, Any]] = None
    context: Optional[ModelContext] = None


class ErrorResponse(BaseModel):
    error: str
    details: Optional[Dict[str, Any]]
