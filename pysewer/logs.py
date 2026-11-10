# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only
"""Two small helpers around pysewer's log messages, so nobody has to learn
Python's ``logging`` module to read them.

* ``show_progress()`` prints pysewer's messages as they happen.
* ``capture_log()`` collects them instead, together with any warnings, for
  looking at (or saving) afterwards.

pysewer writes its messages to the ``pysewer`` logger at three levels:
``"debug"`` (every pipe decision), ``"info"`` (progress and summaries) and
``"warning"`` (problems). Without either helper only problems are shown.
"""

import logging
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path

__all__ = ["CapturedLog", "capture_log", "show_progress"]

LOGGER = logging.getLogger("pysewer")
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}

# the handler show_progress() attached, if any
_progress_handler: logging.Handler | None = None


class _Formatter(logging.Formatter):
    """``pysewer · message``; problems also get their level: ``pysewer · WARNING · message``."""

    def format(self, record):
        message = record.getMessage()
        if record.levelno >= logging.WARNING:
            return f"pysewer · {record.levelname} · {message}"
        return f"pysewer · {message}"


class _StdoutHandler(logging.Handler):
    """Writes to whatever ``sys.stdout`` is at the time (Jupyter, a redirect, ...)."""

    def emit(self, record):
        try:
            sys.stdout.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


def _level(level) -> int | None:
    if level is False or level is None or level == "off":
        return None
    if level is True:
        return logging.INFO
    if isinstance(level, int):
        return level
    try:
        return LEVELS[str(level).lower()]
    except KeyError:
        raise ValueError(
            f"level must be one of {list(LEVELS)}, True or False, not {level!r}"
        ) from None


def show_progress(level="info") -> None:
    """Print pysewer's messages while it works.

    Parameters
    ----------
    level : "info", "debug", "warning", True or False
        ``"info"`` (or ``True``) shows progress and summaries, ``"debug"``
        also every pipe decision (thousands of lines on a town),
        ``"warning"`` only problems. ``False`` turns the messages off again,
        back to pysewer's default of showing only problems.

    Examples
    --------
    >>> pysewer.show_progress()         # see what pysewer is doing
    >>> pysewer.show_progress(False)    # quiet again
    """
    global _progress_handler
    if _progress_handler is not None:
        LOGGER.removeHandler(_progress_handler)
        _progress_handler = None
    lvl = _level(level)
    if lvl is None:
        LOGGER.setLevel(logging.NOTSET)
        LOGGER.propagate = True
        return
    _progress_handler = _StdoutHandler(lvl)
    _progress_handler.setFormatter(_Formatter())
    LOGGER.addHandler(_progress_handler)
    LOGGER.setLevel(lvl)
    # printed once here, not again by a handler the user set up on the root logger
    LOGGER.propagate = False


class CapturedLog:
    """The messages and warnings collected by :func:`capture_log`.

    Attributes
    ----------
    lines : list of str
        Every message, in order, like ``"pysewer · Sewer graph: 105 nodes"``.
    warnings : list of str
        Only the problems: pysewer's warning messages and any Python warning
        raised while the block ran.
    records : list of logging.LogRecord
        The raw log records, for anyone who wants levels, times or modules.
    """

    def __init__(self):
        self.lines: list[str] = []
        self.warnings: list[str] = []
        self.records: list[logging.LogRecord] = []

    @property
    def text(self) -> str:
        """All lines as one string."""
        return "\n".join(self.lines)

    def tail(self, n: int = 5) -> str:
        """The last ``n`` lines as one string."""
        return "\n".join(self.lines[-n:])

    def save(self, path) -> Path:
        """Write all lines to a text file and return its path."""
        path = Path(path)
        path.write_text(self.text + "\n" if self.lines else "", encoding="utf-8")
        return path

    def __len__(self) -> int:
        return len(self.lines)

    def __repr__(self) -> str:
        n = len(self.warnings)
        head = f"pysewer log: {len(self.lines)} lines, {n} warning{'s' if n != 1 else ''}"
        return head + ("\n" + self.tail(3) if self.lines else "")


class _CaptureHandler(logging.Handler):
    def __init__(self, log: CapturedLog, level: int):
        super().__init__(level)
        self.log = log
        self.setFormatter(_Formatter())

    def emit(self, record):
        line = self.format(record)
        self.log.records.append(record)
        self.log.lines.append(line)
        if record.levelno >= logging.WARNING:
            self.log.warnings.append(line)


@contextmanager
def capture_log(level="info", report: bool = True):
    """Collect pysewer's messages and warnings instead of printing them.

    Parameters
    ----------
    level : "info", "debug" or "warning"
        The least important messages to keep (see :func:`show_progress`).
    report : bool
        If True and something went wrong, print one line at the end, such as
        ``pysewer: 2 warnings, see log.warnings``, so problems are never
        silently hidden.

    Yields
    ------
    CapturedLog
        Filled while the block runs; use ``log.lines``, ``log.warnings``,
        ``log.tail()`` or ``log.save("run.log")`` afterwards.

    Examples
    --------
    >>> with pysewer.capture_log() as log:
    ...     tree = pysewer.rsph_tree(G, sinks)
    >>> print(log.tail())
    >>> log.warnings
    """
    lvl = _level(level)
    if lvl is None:
        raise ValueError("capture_log needs a level: 'info', 'debug' or 'warning'")
    log = CapturedLog()
    handler = _CaptureHandler(log, lvl)

    def _showwarning(message, category, filename, lineno, file=None, line=None):
        kind = "" if category is UserWarning else f"{category.__name__}: "
        text = f"pysewer · WARNING · {kind}{message}"
        log.lines.append(text)
        log.warnings.append(text)

    saved = (LOGGER.level, LOGGER.propagate, _progress_handler)
    if _progress_handler is not None:
        LOGGER.removeHandler(_progress_handler)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(lvl)
    LOGGER.propagate = False
    try:
        with warnings.catch_warnings():
            # "always": a warning seen in an earlier run is still collected
            warnings.simplefilter("always")
            # pysewer ignores numpy's RuntimeWarnings (see preprocessing); keep it so
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            warnings.showwarning = _showwarning
            yield log
    finally:
        LOGGER.removeHandler(handler)
        level_before, propagate_before, progress = saved
        LOGGER.setLevel(level_before)
        LOGGER.propagate = propagate_before
        if progress is not None:
            LOGGER.addHandler(progress)
        if report and log.warnings:
            n = len(log.warnings)
            sys.stdout.write(
                f"pysewer: {n} warning{'s' if n != 1 else ''}, see log.warnings\n"
            )
