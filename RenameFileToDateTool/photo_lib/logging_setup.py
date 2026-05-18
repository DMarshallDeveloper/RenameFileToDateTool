"""Central logging configuration for the photo library tooling.

Every workhorse script calls ``configure_logging(script_name)`` once from its
``__main__`` block. That sets up one shared logger named ``photo_lib`` with
two handlers:

  - **Console** (stderr) at INFO+ — bare ``%(message)s`` format so the output
    looks the same as the old ``print()`` calls.
  - **File** at DEBUG+ — written to ``logs/{script_name}.log`` with full
    timestamps and levels. This is the "where did it go wrong last week?" log.

Library code (e.g. ``photo_lib.exiftool_runner``) gets the same logger by name
via ``logging.getLogger("photo_lib")`` so its warnings/errors land in the same
file without any plumbing.

Safe to call ``configure_logging`` multiple times in one process — handlers are
only attached on the first call.
"""

import logging
import os
from datetime import datetime

LOGGER_NAME = "photo_lib"

# Logs live in RenameFileToDateTool/logs/ (gitignored). __file__ is
# photo_lib/logging_setup.py, so two parents up is the package root.
LOGS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)


def get_logger() -> logging.Logger:
    """Return the shared logger. Library code uses this instead of importing logger
    objects directly so the same handler chain applies regardless of who configured it.
    """
    return logging.getLogger(LOGGER_NAME)


def configure_logging(script_name: str, console_level: int = logging.INFO) -> logging.Logger:
    """Attach console + file handlers to the shared ``photo_lib`` logger.

    Returns the configured logger so callers can stash it in a module-level
    ``logger`` name and proceed. Idempotent — the second call in the same
    process is a no-op.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # don't double-emit via the root logger

    if getattr(logger, "_photo_lib_configured", False):
        return logger

    os.makedirs(LOGS_DIR, exist_ok=True)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    log_path = os.path.join(LOGS_DIR, f"{script_name}.log")
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(file_handler)

    logger._photo_lib_configured = True  # type: ignore[attr-defined]
    logger.debug("=== %s started at %s ===", script_name, datetime.now().isoformat())
    return logger
