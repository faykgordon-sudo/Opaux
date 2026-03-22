"""
logger.py -- Centralised logging for Opaux.

Usage in any module:
    from src.logger import get_logger
    log = get_logger(__name__)
    log.info("Scoring job %s", job_id)
"""

import logging
from pathlib import Path

from rich.logging import RichHandler

_LOG_FILE = Path("data/opaux.log")
_configured = False


def configure_logging(verbose: bool = False, quiet: bool = False) -> None:
    """
    Call once at CLI startup to set up root logger.

    verbose=True  -> DEBUG to console
    quiet=True    -> ERROR to console only
    default       -> INFO to console (progress + results)
    Always writes DEBUG+ to data/opaux.log regardless of console level.
    """
    global _configured
    if _configured:
        return
    _configured = True

    # Console level
    if verbose:
        console_level = logging.DEBUG
    elif quiet:
        console_level = logging.ERROR
    else:
        console_level = logging.INFO

    # Ensure log directory exists
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # let handlers filter

    # Rich console handler
    rich_handler = RichHandler(
        level=console_level,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    rich_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    root.addHandler(rich_handler)

    # File handler (always DEBUG)
    file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "playwright"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)
