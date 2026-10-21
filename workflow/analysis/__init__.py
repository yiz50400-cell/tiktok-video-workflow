"""Local, evidence-backed video analysis. No model or network calls."""

from .core import analyze_video, save_observations

__all__ = ["analyze_video", "save_observations"]
