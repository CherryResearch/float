import io
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from app.utils.blob_store import put_asset

try:  # pragma: no cover - optional dependency
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore


class KeyframeDetector:
    """Detects informative frames in a video stream.

    The detector periodically samples frames, scores them for novelty
    against the previous frame and saves those that exceed a capture
    threshold. High scoring frames can be escalated via a callback
    (e.g. to inject into an LLM context).
    """

    def __init__(
        self,
        save_dir: Path,
        sample_interval: float = 1.0,
        capture_threshold: float = 0.5,
        max_per_response: int = 5,
        escalate_callback: Optional[Callable[[Path, float], None]] = None,
    ) -> None:
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.sample_interval = sample_interval
        self.capture_threshold = capture_threshold
        self.max_per_response = max_per_response
        self.escalate_callback = escalate_callback
        self._last_frame: Optional[np.ndarray] = None
        self._captured = 0
        self._counter = 0
        self._last_sample_time = 0.0

    # ------------------------------------------------------------------
    # Frame scoring
    # ------------------------------------------------------------------
    def score(self, frame: np.ndarray) -> float:
        """Return a novelty score for *frame* in range [0, 1]."""
        if self._last_frame is None:
            self._last_frame = frame
            return 1.0
        current = frame.astype("float32")
        previous = self._last_frame.astype("float32")
        diff = np.mean(np.abs(current - previous))
        diff /= 255.0
        self._last_frame = frame
        return float(diff)

    # ------------------------------------------------------------------
    # Frame processing helpers
    # ------------------------------------------------------------------
    def _save_frame(self, frame: np.ndarray) -> Path:
        self._counter += 1
        buffer = io.BytesIO()
        np.save(buffer, frame)
        asset = put_asset(
            buffer.getvalue(),
            filename=f"frame_{self._counter}.npy",
            origin="screenshot",
        )
        return Path(asset["path"])

    def process_array(self, frame: np.ndarray) -> Optional[float]:
        """Process a single numpy array frame."""
        score = self.score(frame)
        over_threshold = score >= self.capture_threshold
        under_limit = self._captured < self.max_per_response
        if over_threshold and under_limit:
            self._captured += 1
            path = self._save_frame(frame)
            if self.escalate_callback:
                self.escalate_callback(path, score)
            return score
        return None

    def reset_rate_limit(self) -> None:
        """Reset per-response capture counter."""
        self._captured = 0

    # ------------------------------------------------------------------
    # Stream processing
    # ------------------------------------------------------------------
    def process_stream(
        self, source: int | str
    ) -> None:  # pragma: no cover - requires cv2
        """Capture frames from *source* and run detection."""
        if cv2 is None:
            raise RuntimeError("OpenCV is required for stream processing")

        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video source: {source}")

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            now = time.time()
            if now - self._last_sample_time < self.sample_interval:
                continue
            self._last_sample_time = now
            self.process_array(frame)

        cap.release()


def make_llm_escalation_callback(llm_service, session_id: str = "default"):
    """Return a callback that adds frames to an LLM context."""

    def _callback(path: Path, score: float) -> None:
        llm_service.add_image_to_context(
            str(path),
            score,
            session_id=session_id,
        )

    return _callback
