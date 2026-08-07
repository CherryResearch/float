import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.calendar_recurrence import validate_rrule_text
from dateutil import rrule as dateutil_rrule
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_BACKGROUND_PERMISSION_SCOPE_RE = re.compile(
    r"^[a-z][a-z0-9_-]{0,63}\.(?:read|write|execute)$"
)
_SERVER_OWNED_CALENDAR_ACTION_FIELDS = {
    "authorization",
    "authorization_id",
    "authorization_status",
    "authorization_request_digest",
    "authorization_occurrence_at",
    "approval_id",
    "approval_status",
    "approved_at",
    "work_run_receipt_id",
    "effect_id",
    "effect_ids",
    "effect_status",
    "effect_certainty",
    "state_delta_certainty",
    "reconciliation_outcome",
    "reconciliation_summary",
    "reconcile_required",
    "tool_invoked",
    "cancel_requested",
    "cancel_request_id",
    "cancel_requested_at",
    "cancelled_at",
    "prompt_checkpoint",
    "external_control_revision",
    "run_control_revision",
}


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


class BackgroundJobPatience(BaseModel):
    """Termination limits for one scheduled background-job occurrence."""

    model_config = ConfigDict(extra="allow")

    stop_condition: str = Field(
        default="one_pass",
        description="Stop policy: one_pass, until_useful, or full_budget.",
    )
    max_attempts: int = Field(
        default=1,
        ge=1,
        le=20,
        description="Maximum workflow/reasoning attempts allowed for one occurrence.",
    )
    max_provider_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description=(
            "Maximum transient provider retries, separate from workflow attempts."
        ),
    )
    max_runtime_seconds: int = Field(
        default=900,
        ge=30,
        le=86400,
        description="Wall-clock safety limit for one occurrence.",
    )
    satisfied_threshold: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="Usefulness threshold for runtimes that can self-evaluate.",
    )


class BackgroundJobExecution(BaseModel):
    """Execution preferences independent from schedule and patience."""

    model_config = ConfigDict(extra="allow")

    reasoning_effort: str = Field(
        default="inherit",
        description="Reasoning effort override, or inherit the active default.",
    )
    model: str = Field(
        default="inherit",
        description="Requested model identifier, or inherit the active default.",
    )
    workflow: str = Field(
        default="inherit",
        description="Requested internal workflow profile, or inherit the default.",
    )
    allow_subagents: bool = Field(
        default=True,
        description="Whether this job may delegate bounded work to sub-agents.",
    )
    sandbox_processes: bool = Field(
        default=True,
        description="Prefer isolated execution when the runtime supports it.",
    )
    permissions: List[str] = Field(
        default_factory=list,
        description=(
            "Canonical permission scopes this job is allowed to use. A scheduled "
            "tool cannot run outside this explicit ceiling."
        ),
    )
    permission_semantics: Literal["allowed_scopes"] = Field(
        default="allowed_scopes",
        description="Declares that permissions are an enforced allowlist ceiling.",
    )

    @field_validator("permissions")
    @classmethod
    def validate_permission_scopes(cls, value: List[str]) -> List[str]:
        scopes: set[str] = set()
        for item in value:
            scope = str(item or "").strip().lower()
            if not _BACKGROUND_PERMISSION_SCOPE_RE.fullmatch(scope):
                raise ValueError(
                    "background permission scopes must use category.read, "
                    "category.write, or category.execute"
                )
            scopes.add(scope)
        if len(scopes) > 32:
            raise ValueError("background jobs support at most 32 permission scopes")
        return sorted(scopes)


class BackgroundJobOwnership(BaseModel):
    """Lineage linking a job to its calendar, chat, message, or parent agent."""

    model_config = ConfigDict(extra="allow")

    owner_kind: str = Field(default="calendar_event")
    calendar_event_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    parent_job_id: Optional[str] = None
    parent_agent_id: Optional[str] = None


class BackgroundJobPolicy(BaseModel):
    """Policy attached to actionable calendar events.

    Start time, time zone, and RRULE intentionally stay on ``CalendarEvent``;
    duplicating them here would create two competing schedule sources.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = Field(default=1, ge=1)
    patience: BackgroundJobPatience = Field(default_factory=BackgroundJobPatience)
    execution: BackgroundJobExecution = Field(default_factory=BackgroundJobExecution)
    ownership: BackgroundJobOwnership = Field(default_factory=BackgroundJobOwnership)


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
        description=(
            "Reference Unix timestamp used to resolve relative natural-language times."
        ),
    )
    rrule: Optional[str] = Field(
        default=None,
        description="Recurrence rule in RRULE format.",
    )
    recurrence_exceptions: List[str] = Field(
        default_factory=list,
        description=(
            "Imported EXDATE/RDATE/override metadata not yet applied to expansion."
        ),
    )
    recurrence_import_warning: Optional[str] = Field(
        default=None,
        description=(
            "Visible warning when an import contains unsupported recurrence detail."
        ),
    )

    @field_validator("rrule")
    @classmethod
    def validate_rrule(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not str(value).strip():
            return None
        return validate_rrule_text(value)

    timezone: str = Field(
        default="UTC",
        description="IANA time zone identifier for the event times.",
    )
    notes: List[CalendarNote] = Field(
        default_factory=list, description="Notes associated with this event."
    )
    actions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Optional structured actions (e.g. scheduled tools) attached to the event."
        ),
    )

    @field_validator("actions")
    @classmethod
    def validate_action_ids(cls, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw_action in enumerate(actions):
            action = dict(raw_action)
            for field in _SERVER_OWNED_CALENDAR_ACTION_FIELDS:
                action.pop(field, None)
            request_id = str(action.get("request_id") or "").strip()
            action_id = str(action.get("id") or "").strip()
            if request_id and action_id and request_id != action_id:
                raise ValueError("calendar action id and request_id must match")
            stable_id = request_id or action_id or f"action-{index + 1}"
            if stable_id in seen:
                raise ValueError(f"duplicate calendar action id: {stable_id}")
            seen.add(stable_id)
            action["id"] = stable_id
            normalized.append(action)
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        timezone = str(value or "").strip()
        if not timezone:
            raise ValueError("timezone must be an IANA time zone identifier")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA time zone: {timezone}") from exc
        return timezone

    @model_validator(mode="after")
    def validate_recurrence_semantics(self):
        if not self.rrule:
            return self
        try:
            start = datetime.fromtimestamp(self.start_time, ZoneInfo(self.timezone))
            dateutil_rrule.rrulestr(self.rrule, dtstart=start)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid RRULE: {exc}") from exc
        return self

    background_job: Optional[BackgroundJobPolicy] = Field(
        default=None,
        description=(
            "Optional patience, execution, and ownership policy. Calendar fields "
            "remain the canonical schedule."
        ),
    )
    status: str = Field(
        default="pending",
        description=(
            "Event status (pending, scheduled, prompted, acknowledged, skipped)."
        ),
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
