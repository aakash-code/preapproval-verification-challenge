"""Typed pipeline error.

Every pipeline stage wraps unexpected failures in a `PipelineError`. The
original exception is chained (so the traceback survives in the logs) and a
plain-language `user_message` is carried for the UI — reviewers never see a
stack trace or Python jargon.
"""

from __future__ import annotations


class PipelineError(Exception):
    """An error in a review-pipeline stage, with a plain-language message.

    Raise as ``raise PipelineError("Could not read the form...") from exc`` so
    the original exception is chained and stays in the log.
    """

    def __init__(self, user_message: str, *, original: BaseException | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.original = original
