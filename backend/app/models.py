from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class Tool(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    type: Optional[str] = None
    native: Optional[Dict[str, Any]] = None
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


class ComputerDisplayConfig(BaseModel):
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=240, le=2160)


class ComputerConfig(BaseModel):
    enabled: bool = False
    runtime: Literal["browser", "windows"] = "browser"
    session_id: Optional[str] = None
    start_url: Optional[str] = None
    allowed_domains: List[str] = Field(default_factory=list)
    allowed_apps: List[str] = Field(default_factory=list)
    native_tool_type: Optional[str] = None
    display: ComputerDisplayConfig = Field(default_factory=ComputerDisplayConfig)


class ComputerSession(BaseModel):
    id: str
    runtime: str
    status: str
    width: int
    height: int
    current_url: Optional[str] = None
    active_window: Optional[str] = None
    last_screenshot_path: Optional[str] = None
    created_at: float
    updated_at: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ComputerAction(BaseModel):
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[str] = None
    text: Optional[str] = None
    keys: Optional[Union[str, List[str]]] = None
    delta_x: Optional[int] = None
    delta_y: Optional[int] = None
    ms: Optional[int] = None
    url: Optional[str] = None
    app: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    window_title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ComputerActionBatch(BaseModel):
    session_id: str
    actions: List[ComputerAction] = Field(default_factory=list)
    approval_level: str = "confirm"
    source_model: Optional[str] = None


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
    workflow: Optional[str] = None
    modules: List[str] = Field(default_factory=list)
    context: Optional[ModelContext] = None
    computer: Optional[ComputerConfig] = None


class ChatResponse(BaseModel):
    message: str
    thought: Optional[str] = None
    tools_used: Optional[List[Union[str, Dict[str, Any]]]] = None
    metadata: Optional[Dict[str, Any]] = None
    context: Optional[ModelContext] = None


class ErrorResponse(BaseModel):
    error: str
    details: Optional[Dict[str, Any]]
