from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ToolSchema(BaseModel):
    """
    Defines the structure of tool configurations for the agent.
    """

    tool_id: str = Field(
        description=(
            "The unique identifier for the tool. " + "This is how you call it."
        ),
    )
    description: str = Field(
        description="A description of what the tool does.",
    )
    input_schema: Dict[str, Any] = Field(
        description="The expected input data structure."
    )
    permissions: Optional[List[str]] = Field(
        default=[],
        description=(
            "A list of permissions required to execute tool. "
            "User validation will halt the flow if these are not available."
        ),
    )


class Message(BaseModel):
    """
    Defines the structure of a message in the chat.
    """

    sender: str = Field(description="who sent this message.")
    content: str = Field(description="text of the message.")
    timestamp: float = Field(description="Time the message was sent.")
    metadata: Optional[Dict[str, str]] = Field(
        default={}, description="any extra info."
    )


class CalendarNote(BaseModel):
    """A note attached to a calendar event."""

    id: str = Field(description="Unique note identifier.")
    content: str = Field(description="Content of the note.")
    timestamp: float = Field(
        description="Unix timestamp when the note was created.",
    )


class CalendarEvent(BaseModel):
    """Represents a calendar event with optional notes."""

    id: str = Field(description="Unique event identifier.")
    title: str = Field(description="Short title for the event.")
    description: Optional[str] = Field(
        default=None,
        description="Longer freeform description/notes for the event.",
    )
    location: Optional[str] = Field(
        default=None,
        description="Optional location for the event.",
    )
    start_time: float = Field(
        description="Event start time as Unix timestamp.",
    )
    end_time: Optional[float] = Field(
        default=None, description="Event end time as Unix timestamp."
    )
    grounded_at: Optional[float] = Field(
        default=None,
        description="Reference Unix timestamp used to resolve relative natural-language times.",
    )
    rrule: Optional[str] = Field(
        default=None,
        description="Recurrence rule in RRULE format.",
    )
    timezone: str = Field(
        default="UTC",
        description="IANA time zone identifier for the event times.",
    )
    notes: List[CalendarNote] = Field(
        default_factory=list, description="Notes associated with this event."
    )
    actions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Optional structured actions (e.g. scheduled tools) attached to the event.",
    )
    status: str = Field(
        default="pending",
        description="Event status (pending, scheduled, prompted, acknowledged, skipped).",
    )


class ObservationalTokenSchema(BaseModel):
    token: str
    description: Optional[str]
    context: Optional[Dict[str, Any]]


class EmbeddingRequest(BaseModel):
    text: str
    model: Optional[str] = "default"


class AugmentedResponse(BaseModel):
    response: str
    metadata: Optional[Dict[str, Any]]


class MemoryUpdateRequest(BaseModel):
    key: str
    value: Optional[Dict[str, str]]  # Adjust type based on expected structure
