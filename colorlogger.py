import logging
import os
from enum import Enum

class TermColor(str, Enum):
    """ Colors are fun. """

    def __str__(self):
        return self.value

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    WHONRED = "\033[97;41m"  # Bright white text on red background
    WHONGRAY = "\033[48;5;236;38;5;250m"
    BKONBL = "\033[30;44m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


class LogColor(str, Enum):
    """ Define log colors in terms of TermColors. """

    def __str__(self):
        return self.value

    NOTSET = TermColor.WHITE
    DEBUG = TermColor.BLUE
    INFO = TermColor.GREEN
    WARNING = TermColor.YELLOW
    ERROR = TermColor.RED
    FATAL = TermColor.WHONRED
    CRITICAL = TermColor.WHONRED


class ColoredFormatter(logging.Formatter):
    """ Make it purdy. """

    def format(self, record):
        """ """
        record_asctime = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        levelname = record.levelname

        # Pycharm warns about the levelname in brackets because (I think) of LogColor's
        #  multiple inheritance from str and Enum; safe to ignore.
        # noinspection PyTypeHints
        levelcolor = LogColor[levelname].value
        file = record.__dict__.get('filename', '<file?>')
        function = record.__dict__.get('funcName', '<function?>')
        line = record.__dict__.get('lineno', '<line?>')

        msg = f"{TermColor.RED}[{levelcolor}{levelname} | {record_asctime}{TermColor.RED}] "
        msg += f"{levelcolor}{file}:{function}:{line}{TermColor.RESET}"
        msg += f"{TermColor.CYAN} - {record.getMessage()}{TermColor.RESET}"
        return msg


class CustomLogger(logging.Logger):
    """Custom logger that extends Python's built-in Logger."""

    def __init__(self, name="my_logger", level=None):
        """ """
        # The first of the input level, the env LOG_LEVEL, or the default of INFO.
        level = level or getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
        super().__init__(name, level)
        self.setLevel(level)

        # Prevent duplicate handlers if the logger is re-imported
        if not self.hasHandlers():
            # Add a local colorful logger.
            handler = logging.StreamHandler()
            handler.setFormatter(ColoredFormatter())
            self.addHandler(handler)

logger = CustomLogger()