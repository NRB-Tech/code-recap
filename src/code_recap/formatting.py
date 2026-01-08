"""Output formatting utilities for code-recap.

Provides functions for consistent terminal output formatting across scripts.
"""

import sys
from typing import TextIO

DEFAULT_WIDTH = 60


def print_separator(width: int = DEFAULT_WIDTH, file: TextIO = sys.stderr) -> None:
    """Prints a separator line.

    Args:
        width: Width of the separator line in characters.
        file: Output stream (default: sys.stderr).
    """
    print("=" * width, file=file)


def print_heading(title: str, width: int = DEFAULT_WIDTH, file: TextIO = sys.stderr) -> None:
    """Prints a section heading with separator lines.

    Args:
        title: The heading text to display.
        width: Width of the separator lines in characters.
        file: Output stream (default: sys.stderr).
    """
    print_separator(width, file)
    print(title, file=file)
    print_separator(width, file)
