"""Tests for video keyframe detection and context injection."""

# isort:skip_file
import numpy as np
import pytest


@pytest.fixture(autouse=True)
def add_backend_to_sys_path():
    import sys
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parents[2]
    backend_dir = str(backend_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)


def test_frame_scoring(tmp_path):
    from video.keyframe_detector import KeyframeDetector

    detector = KeyframeDetector(save_dir=tmp_path)
    f1 = np.zeros((10, 10, 3), dtype=np.uint8)
    s1 = detector.score(f1)
    assert s1 == 1.0
    f2 = np.zeros((10, 10, 3), dtype=np.uint8)
    s2 = detector.score(f2)
    assert s2 < 0.01
    f3 = np.ones((10, 10, 3), dtype=np.uint8) * 255
    s3 = detector.score(f3)
    assert s3 > 0.5


def test_context_injection(tmp_path):
    from app.base_services import LLMService
    from video.keyframe_detector import (
        KeyframeDetector,
        make_llm_escalation_callback,
    )

    llm = LLMService()
    paths = []
    cb = make_llm_escalation_callback(llm, session_id="sess")

    def wrapped(path, score):
        paths.append(path)
        cb(path, score)

    detector = KeyframeDetector(
        save_dir=tmp_path,
        capture_threshold=0.1,
        escalate_callback=wrapped,
    )
    f1 = np.zeros((5, 5, 3), dtype=np.uint8)
    f2 = np.ones((5, 5, 3), dtype=np.uint8) * 255
    detector.process_array(f1)
    detector.process_array(f2)
    assert paths, "callback not invoked"
    ctx = llm.get_context("sess")
    images = ctx.metadata.get("images", [])
    assert images and images[0]["path"] == str(paths[0])
