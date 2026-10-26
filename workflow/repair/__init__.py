"""Local, source-preserving video repair.

Pipeline imports OpenCV. Attribute access loads it; ``workflow.repair.intervals``
does not, so temporal-ROI checks can run without cv2.
"""

__all__ = ["load_spec", "run_repair", "task_status"]


def __getattr__(name):
    if name in __all__:
        from .pipeline import load_spec, run_repair, task_status
        exports = {"load_spec": load_spec, "run_repair": run_repair, "task_status": task_status}
        globals().update(exports)
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
