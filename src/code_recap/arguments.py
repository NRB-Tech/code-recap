"""Shared argument definitions for CLI scripts.

This module provides functions to add common argument groups to argparse parsers,
reducing duplication across scripts.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from code_recap.git_utils import get_git_config_author
from code_recap.paths import get_default_output_dir_name, get_default_scan_root


def add_author_arg(parser: argparse.ArgumentParser) -> None:
    """Adds the --author argument to a parser.

    Args:
        parser: The argument parser to add the argument to.
    """
    parser.add_argument(
        "--author",
        help=(
            "Author name or email pattern to filter commits. "
            "Defaults to git config user.name. "
            "Supports partial matching: 'John', 'john@example.com', or '@example.com' for domain."
        ),
    )


def add_root_arg(parser: argparse.ArgumentParser) -> None:
    """Adds the --root argument to a parser.

    Args:
        parser: The argument parser to add the argument to.
    """
    parser.add_argument(
        "--root",
        default=str(get_default_scan_root()),
        help="Root directory containing project folders (default: parent of current directory).",
    )


def add_fetch_arg(parser: argparse.ArgumentParser, detailed_help: bool = False) -> None:
    """Adds the --fetch argument to a parser.

    Args:
        parser: The argument parser to add the argument to.
        detailed_help: If True, use detailed help text mentioning submodules.
    """
    if detailed_help:
        help_text = (
            "Fetch repositories before processing (updates from remotes). All "
            "repos (including submodules) are fetched to ensure latest commits."
        )
    else:
        help_text = "Fetch repositories before processing (updates from remotes)."

    parser.add_argument(
        "--fetch",
        action="store_true",
        help=help_text,
    )


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    """Adds the --config argument to a parser.

    Args:
        parser: The argument parser to add the argument to.
    """
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to config.yaml file (default: ./config/config.yaml or ~/.config/code-recap/).",
    )


def add_model_args(parser: argparse.ArgumentParser, default_model: str) -> None:
    """Adds LLM model arguments (--model, --temperature) to a parser.

    Args:
        parser: The argument parser to add the arguments to.
        default_model: Default model string.
    """
    parser.add_argument(
        "--model",
        default=default_model,
        help=f"LiteLLM model string (default: {default_model}).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.3,
        help="LLM temperature (default: 0.3).",
    )


def add_filter_arg(parser: argparse.ArgumentParser) -> None:
    """Adds the --filter argument for repository filtering to a parser.

    Args:
        parser: The argument parser to add the argument to.
    """
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Filter repositories by name pattern (can be repeated). "
            "Supports glob patterns (e.g., 'MyProject*') or substrings. "
            "Case-insensitive."
        ),
    )


def add_output_dir_arg(parser: argparse.ArgumentParser, help_text: Optional[str] = None) -> None:
    """Adds the --output-dir argument to a parser.

    Args:
        parser: The argument parser to add the argument to.
        help_text: Custom help text (default: generic output directory help).
    """
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=help_text or f"Base output directory (default: {get_default_output_dir_name()}).",
    )


def add_input_dir_arg(parser: argparse.ArgumentParser, help_text: Optional[str] = None) -> None:
    """Adds the --input-dir argument to a parser.

    Args:
        parser: The argument parser to add the argument to.
        help_text: Custom help text (default: generic input directory help).
    """
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help=help_text or "Input directory.",
    )


def add_exclude_args(parser: argparse.ArgumentParser, detailed_help: bool = False) -> None:
    """Adds exclusion pattern arguments to a parser.

    Adds --exclude, --excludes-file, --no-excludes-file, and --no-default-excludes.

    Args:
        parser: The argument parser to add the arguments to.
        detailed_help: If True, use more detailed help text.
    """
    if detailed_help:
        exclude_help = (
            "Glob pattern for files to exclude from line counts (can be "
            "repeated). Examples: '*.hex', '*/archive/*', 'package-lock.json'."
        )
        excludes_file_help = (
            "Path to file containing exclusion patterns. Default: looks for "
            "'config.yaml' in the config directory. Format: YAML with 'excludes' section."
        )
    else:
        exclude_help = "Glob pattern for files to exclude from stats (can be repeated)."
        excludes_file_help = "Path to file containing exclusion patterns."

    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help=exclude_help,
    )
    parser.add_argument(
        "--excludes-file",
        metavar="FILE",
        help=excludes_file_help,
    )
    parser.add_argument(
        "--no-excludes-file",
        action="store_true",
        help="Don't load exclusion patterns from config file.",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Disable default exclusions (build artifacts, lock files, etc.).",
    )


def resolve_author(
    args: argparse.Namespace,
    parser: Optional[argparse.ArgumentParser] = None,
    required: bool = True,
    output: object = sys.stderr,
) -> Optional[str]:
    """Resolves the author from args, falling back to git config.

    Args:
        args: Parsed command-line arguments with an 'author' attribute.
        parser: Optional parser for error reporting (uses parser.error if provided).
        required: If True and no author can be resolved, raise an error.
        output: File object for status messages (default: sys.stderr).

    Returns:
        The resolved author string, or None if not required and not found.

    Raises:
        SystemExit: If required=True and no author is found (via parser.error or sys.exit).
    """
    if args.author:
        return args.author

    author = get_git_config_author()
    if author:
        print(f"Using author from git config: {author}", file=output)
        args.author = author
        return author

    if required:
        error_msg = (
            "--author is required (git config user.name not set). "
            "Set it with: git config --global user.name 'Your Name'"
        )
        if parser:
            parser.error(error_msg)
        else:
            print(f"Error: {error_msg}", file=sys.stderr)
            sys.exit(1)

    return None
