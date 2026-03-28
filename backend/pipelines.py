"""Pipeline definitions and routing logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from workers.multimodal import ROLE_REGISTRY


@dataclass
class Stage:
    """A single pipeline stage.

    ``role`` refers to an entry in :data:`ROLE_REGISTRY`.
    ``needs`` lists the artifacts this stage expects to find in the
    data dictionary passed to :meth:`Pipeline.run`.
    """

    role: str
    needs: Set[str] = field(default_factory=set)


@dataclass
class Pipeline:
    """A sequence of stages to execute in order."""

    name: str
    stages: Sequence[Stage]

    @property
    def needs(self) -> Set[str]:
        """Artifacts required to start this pipeline.

        The value is computed by walking the stages and determining which
        inputs must be supplied externally (i.e. are not produced by any
        previous stage).
        """

        provided: Set[str] = set()
        required: Set[str] = set()
        for stage in self.stages:
            missing = stage.needs - provided
            required.update(missing)
            provided.update(stage.needs)
            provided.update(ROLE_REGISTRY[stage.role].provides)
        return required

    @property
    def capabilities(self) -> Set[str]:
        """All artifacts consumed or produced by this pipeline."""

        caps: Set[str] = set()
        for stage in self.stages:
            caps.update(stage.needs)
            caps.update(ROLE_REGISTRY[stage.role].provides)
        return caps

    def run(self, data: Dict[str, object]) -> Dict[str, object]:
        """Execute the pipeline sequentially.

        ``data`` is mutated with artifacts produced by each stage and
        returned once all stages have run.
        """

        for stage in self.stages:
            if not stage.needs.issubset(data.keys()):
                missing = stage.needs - data.keys()
                raise ValueError(f"{stage.role} missing inputs: {missing}")
            worker = ROLE_REGISTRY[stage.role]
            inputs = {k: data[k] for k in stage.needs}
            outputs = worker.run(inputs)
            data.update(outputs)
        return data


PIPELINES: List[Pipeline] = [
    Pipeline(
        "image_plus_text",
        [
            Stage("VisionCaptioner", {"image"}),
            Stage("LLM", {"text", "image_caption"}),
        ],
    ),
    Pipeline("text_only", [Stage("LLM", {"text"})]),
    Pipeline(
        "audio_streaming",
        [
            Stage("VAD", {"audio"}),
            Stage("ASR", {"speech_turn"}),
            Stage("LLM", {"asr"}),
        ],
    ),
    Pipeline(
        "video_streaming",
        [
            Stage("KeyframeDetector", {"video"}),
            Stage("VisionCaptioner", {"keyframe"}),
            Stage("VAD", {"audio"}),
            Stage("ASR", {"speech_turn"}),
            Stage("LLM", {"asr", "image_caption"}),
        ],
    ),
    Pipeline(
        "voice_chat_full_duplex",
        [
            Stage("VAD", {"audio"}),
            Stage("ASR", {"speech_turn"}),
            Stage("LLM", {"asr"}),
            Stage("TTS", {"text"}),
        ],
    ),
    Pipeline("multimodal_all_in_one", [Stage("LLM", set())]),
    Pipeline(
        "image_caption",
        [
            Stage("VisionCaptioner", {"image"}),
        ],
    ),
]


class PipelineRouter:
    """Select pipelines based on advertised needs."""

    def __init__(self, pipelines: Sequence[Pipeline] = PIPELINES) -> None:
        self.pipelines = list(pipelines)
        self._availability: Dict[str, bool] = {pipe.name: True for pipe in self.pipelines}

    def set_available(self, name: str, available: bool) -> None:
        """Mark a pipeline as available or unavailable for routing."""

        if name not in self._availability:
            raise KeyError(f"Unknown pipeline '{name}'")
        self._availability[name] = available

    def route(
        self,
        needs: Set[str],
        *,
        outputs: Optional[Set[str]] = None,
    ) -> Optional[Pipeline]:
        """Return a pipeline able to satisfy ``needs`` and optional ``outputs``.

        When ``outputs`` are provided the router prefers pipelines that can
        produce the requested artifacts with the fewest extra capabilities.
        When ``outputs`` are omitted, the first matching pipeline is returned
        to retain the original deterministic behaviour.
        """

        requested_outputs = set(outputs or ())
        needs = set(needs)
        candidates: List[Pipeline] = []
        fallback: Optional[Pipeline] = None

        for pipeline in self.pipelines:
            if not self._availability.get(pipeline.name, True):
                continue

            if not pipeline.needs.issubset(needs):
                continue
            if not needs.issubset(pipeline.capabilities):
                continue

            if not requested_outputs:
                return pipeline

            if requested_outputs.issubset(pipeline.capabilities):
                candidates.append(pipeline)
            elif fallback is None:
                fallback = pipeline

        if requested_outputs:
            if candidates:
                candidates.sort(
                    key=lambda pipe: (
                        len(pipe.capabilities - requested_outputs - needs),
                        len(pipe.stages),
                    )
                )
                return candidates[0]
            return fallback

        return None
