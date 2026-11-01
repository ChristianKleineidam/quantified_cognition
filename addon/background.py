"""File-only work uses Anki's worker pool without blocking collection operations."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import TypeVar

T = TypeVar("T")


def run_file_job(
    task: Callable[[], T], success: Callable[[T], None], failure: Callable[[Exception], None]
) -> Future:
    from aqt import mw

    if mw is None:
        raise RuntimeError("Open Anki before starting a background job.")

    def done(future: Future) -> None:
        # TaskManager dispatches completion callbacks to Qt's main thread.
        try:
            result = future.result()
        except Exception as error:
            failure(error)
        else:
            success(result)

    return mw.taskman.run_in_background(task, done, uses_collection=False)
