#!/usr/bin/env python3
"""Generate a time period in review summary of git activity.

This tool summarizes an author's git activity across multiple repositories
for a given time period. Supports single-period summaries (text/markdown)
and multi-period ranges (CSV for charting).
"""

import argparse
import csv
import datetime as _dt
import os
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Optional

from code_recap.arguments import (
    add_author_arg,
    add_exclude_args,
    add_fetch_arg,
    add_filter_arg,
    add_output_dir_arg,
    add_root_arg,
    resolve_author,
)
from code_recap.git_utils import (
    discover_all_submodules,
    discover_top_level_repos,
    fetch_repos_with_progress,
    run_git,
)
from code_recap.paths import (
    get_config_path,
    get_output_dir,
)

# Extension to language name mapping
EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    # Python
    ".py": "Python",
    ".pyw": "Python",
    ".pyi": "Python",
    # JavaScript / TypeScript
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    # Apple platforms
    ".swift": "Swift",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".storyboard": "Storyboard",
    ".xib": "XIB",
    ".pbxproj": "Xcode Project",
    ".xcscheme": "Xcode Config",
    ".xcconfig": "Xcode Config",
    ".plist": "Property List",
    ".entitlements": "Property List",
    ".resolved": "Swift Package",
    # C family
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    # Other compiled languages
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".fs": "F#",
    ".fsx": "F#",
    ".vb": "Visual Basic",
    ".scala": "Scala",
    ".clj": "Clojure",
    ".cljs": "ClojureScript",
    ".erl": "Erlang",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".hs": "Haskell",
    ".lua": "Lua",
    ".pl": "Perl",
    ".pm": "Perl",
    ".r": "R",
    ".R": "R",
    ".jl": "Julia",
    ".dart": "Dart",
    ".zig": "Zig",
    ".nim": "Nim",
    ".v": "V",
    ".ml": "OCaml",
    ".mli": "OCaml",
    ".gleam": "Gleam",
    ".odin": "Odin",
    # Shell / scripting
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    ".ps1": "PowerShell",
    ".psm1": "PowerShell",
    # Build systems
    ".gradle": "Gradle",
    ".cmake": "CMake",
    ".makefile": "Makefile",
    ".mk": "Makefile",
    ".ld": "Linker Script",
    # Patches / diffs
    ".patch": "Patch",
    ".diff": "Patch",
    # Web
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".vue": "Vue",
    ".svelte": "Svelte",
    # Data / config
    ".sql": "SQL",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".xml": "XML",
    ".toml": "TOML",
    ".ini": "INI",
    ".cfg": "Config",
    ".conf": "Config",
    ".properties": "Properties",
    ".env": "Env",
    # Documentation
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".rst": "reStructuredText",
    ".txt": "Text",
    ".tex": "LaTeX",
    # Infrastructure / config
    ".dockerfile": "Dockerfile",
    ".proto": "Protocol Buffers",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
    ".tf": "Terraform",
    ".hcl": "HCL",
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Languages that should not be considered "primary" for a project even if they have
# the most lines (e.g., project files are verbose but don't represent the actual code)
NON_PRIMARY_LANGUAGES: set[str] = {
    # IDE/build config files
    "Xcode Project",
    "Xcode Config",
    "Property List",
    "Swift Package",
    "Storyboard",
    "XIB",
    "Gradle",
    "CMake",
    "Makefile",
    "Linker Script",
    # Header files (supporting files, not standalone code)
    "C/C++ Header",
    "C++ Header",
    # Config/data formats
    "Config",
    "Properties",
    "Env",
    "JSON",
    "YAML",
    "XML",
    "TOML",
    "INI",
    # Documentation
    "Markdown",
    "Text",
    "reStructuredText",
    # Other
    "Patch",
    "Other",
}

# Default patterns to exclude from line counts (build artifacts, generated files)
DEFAULT_EXCLUDE_PATTERNS: list[str] = [
    # Compiled/build artifacts
    "*.hex",
    "*.bin",
    "*.elf",
    "*.o",
    "*.a",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.map",
    "*.list",
    "*.dSYM/*",
    # Firmware/binary blobs
    "*.mbn",
    "*.qsr4",
    "*.fw",
    "*.img",
    # Build output directories
    "*/archive/*",
    "*/release/*",
    "*/build/*",
    "*/dist/*",
    "*/out/*",
    "*/cmake-build-*/*",
    # Generated documentation (Doxygen, etc.)
    "*/docs/_build/*",
    "*/doc/html/*",
    "*/site/*",
    # Package manager artifacts
    "*/node_modules/*",
    "*/vendor/*",
    "*/.venv/*",
    "*/venv/*",
    "*/__pycache__/*",
    "*.pyc",
    # IDE/editor
    "*/.idea/*",
    "*/.vscode/*",
    # Lock files (can be large)
    "package-lock.json",
    "yarn.lock",
    "Podfile.lock",
    "Gemfile.lock",
    "poetry.lock",
    "Cargo.lock",
    # Minified files
    "*.min.js",
    "*.min.css",
    # Source maps
    "*.map",
    "*.js.map",
    "*.css.map",
]

# Legacy excludes file path (for backwards compatibility)
DEFAULT_EXCLUDES_FILE = "config/excludes.yaml"


@dataclass
class ExcludeConfig:
    """Configuration for file exclusions.

    Attributes:
        global_patterns: Patterns applied to all projects.
        project_patterns: Patterns keyed by project name (case-insensitive).
    """

    global_patterns: list[str] = field(default_factory=list)
    project_patterns: dict[str, list[str]] = field(default_factory=dict)

    def get_patterns_for_project(self, project_name: str) -> list[str]:
        """Gets all applicable patterns for a project.

        Args:
            project_name: Name of the project.

        Returns:
            Combined list of global and project-specific patterns.
        """
        patterns = list(self.global_patterns)
        # Check for project-specific patterns (case-insensitive)
        project_lower = project_name.lower()
        for name, proj_patterns in self.project_patterns.items():
            if name.lower() == project_lower:
                patterns.extend(proj_patterns)
        return patterns


def load_excludes_file(filepath: str) -> ExcludeConfig:
    """Loads exclusion patterns from a YAML file.

    YAML format:
        global:
          - "*.hex"
          - "*/build/*"
        projects:
          ROUTES:
            - "xm125/*"

    Args:
        filepath: Path to the excludes YAML file.

    Returns:
        ExcludeConfig with parsed patterns.
    """
    config = ExcludeConfig()

    if not os.path.isfile(filepath):
        return config

    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        print(
            "Warning: PyYAML not installed. Install with: pip install pyyaml",
            file=sys.stderr,
        )
        return config

    try:
        with open(filepath) as f:
            data = yaml.safe_load(f)

        if not data:
            return config

        # Load global patterns
        if "global" in data and data["global"]:
            config.global_patterns = list(data["global"])

        # Load project-specific patterns
        if "projects" in data and data["projects"]:
            for project_name, patterns in data["projects"].items():
                if patterns:
                    config.project_patterns[project_name] = list(patterns)

    except Exception as e:
        print(f"Warning: Failed to load excludes config: {e}", file=sys.stderr)

    return config


def load_excludes_from_config(filepath: str) -> ExcludeConfig:
    """Loads exclusion patterns from a unified config.yaml file.

    Args:
        filepath: Path to the config.yaml file.

    Returns:
        ExcludeConfig with parsed patterns from the 'excludes' section.
    """
    config = ExcludeConfig()

    if not os.path.isfile(filepath):
        return config

    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        return config

    try:
        with open(filepath) as f:
            data = yaml.safe_load(f)

        if not data or "excludes" not in data:
            return config

        excludes_data = data["excludes"]

        # Load global patterns
        if "global" in excludes_data and excludes_data["global"]:
            config.global_patterns = list(excludes_data["global"])

        # Load project-specific patterns
        if "projects" in excludes_data and excludes_data["projects"]:
            for project_name, patterns in excludes_data["projects"].items():
                if patterns:
                    config.project_patterns[project_name] = list(patterns)

    except Exception as e:
        print(f"Warning: Failed to load excludes from config: {e}", file=sys.stderr)

    return config


def _matches_any_pattern(filepath: str, patterns: list[str]) -> bool:
    """Checks if a filepath matches any of the given glob patterns.

    Args:
        filepath: File path to check.
        patterns: List of glob patterns (supports * and ** wildcards).

    Returns:
        True if the filepath matches any pattern, False otherwise.
    """
    import fnmatch

    filepath_lower = filepath.lower()
    for pattern in patterns:
        pattern_lower = pattern.lower()
        # Handle ** for recursive matching
        if "**" in pattern_lower:
            # Convert ** to regex-like matching
            regex_pattern = pattern_lower.replace("**", "*")
            if fnmatch.fnmatch(filepath_lower, regex_pattern):
                return True
        elif fnmatch.fnmatch(filepath_lower, pattern_lower):
            return True
        # Also check if pattern matches anywhere in path
        if fnmatch.fnmatch(filepath_lower, "*/" + pattern_lower):
            return True
        if fnmatch.fnmatch(filepath_lower, pattern_lower + "/*"):
            return True
    return False


@dataclass
class LanguageStats:
    """Statistics for a single programming language.

    Attributes:
        name: Human-readable language name.
        files_changed: Number of unique files modified.
        lines_added: Total lines added.
        lines_removed: Total lines removed.
    """

    name: str
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class TimePattern:
    """Activity distribution across days and hours.

    Attributes:
        day_of_week_counts: Commit count per day of week (Monday=0).
        hour_counts: Commit count per hour (0-23).
    """

    day_of_week_counts: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    hour_counts: dict[int, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class ProjectSummary:
    """Summary statistics for a single project repository.

    Attributes:
        project_name: Display name of the project.
        project_path: Absolute path to the project repository.
        commit_count: Total number of commits in the period.
        lines_added: Total lines added across all files.
        lines_removed: Total lines removed across all files.
        files_changed: Set of unique file paths modified.
        languages: Per-language statistics.
        first_commit_date: Date of the earliest commit.
        last_commit_date: Date of the latest commit.
        commit_dates: Set of dates with commits (for streak calculation).
    """

    project_name: str
    project_path: str
    commit_count: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    files_changed: set[str] = field(default_factory=set)
    languages: dict[str, LanguageStats] = field(default_factory=dict)
    first_commit_date: Optional[_dt.date] = None
    last_commit_date: Optional[_dt.date] = None
    commit_dates: set[_dt.date] = field(default_factory=set)


@dataclass
class PeriodStats:
    """Statistics for a single time period.

    Attributes:
        period_label: Human-readable period label (e.g., "2024", "2024-Q1").
        start_date: First day of the period.
        end_date: Last day of the period.
        commits: Total commit count.
        lines_added: Total lines added.
        lines_removed: Total lines removed.
        files_changed: Number of unique files modified.
        active_days: Number of days with at least one commit.
        longest_streak: Longest consecutive days with commits.
        top_language: Language with most lines added.
        top_language_lines: Lines added in the top language.
        projects_active: Number of projects with commits.
        languages: Per-language statistics.
        time_patterns: Activity distribution (for detailed output).
        project_summaries: Per-project breakdown (for detailed output).
    """

    period_label: str
    start_date: _dt.date
    end_date: _dt.date
    commits: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    files_changed: int = 0
    active_days: int = 0
    longest_streak: int = 0
    top_language: str = ""
    top_language_lines: int = 0
    projects_active: int = 0
    languages: dict[str, LanguageStats] = field(default_factory=dict)
    time_patterns: TimePattern = field(default_factory=TimePattern)
    project_summaries: list[ProjectSummary] = field(default_factory=list)


def parse_period(period_str: str) -> tuple[str, _dt.date, _dt.date]:
    """Parses a period string into label and date boundaries.

    Args:
        period_str: Period specifier in one of these formats:
            - YYYY (year)
            - YYYY-QN (quarter, N=1-4)
            - YYYY-MM (month)
            - YYYY-WNN (ISO week)
            - YYYY-MM-DD:YYYY-MM-DD (custom date range)

    Returns:
        A tuple of (label, start_date, end_date).

    Raises:
        SystemExit: If the period string is invalid.
    """
    period_str = period_str.strip()

    # Custom date range: YYYY-MM-DD:YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$", period_str):
        start_str, end_str = period_str.split(":")
        try:
            start = _dt.datetime.strptime(start_str, "%Y-%m-%d").date()
            end = _dt.datetime.strptime(end_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise SystemExit(f"Invalid date range: {period_str}") from exc
        label = f"{start_str} to {end_str}"
        return label, start, end

    # Year: YYYY
    if re.match(r"^\d{4}$", period_str):
        year = int(period_str)
        start = _dt.date(year, 1, 1)
        end = _dt.date(year, 12, 31)
        return period_str, start, end

    # Quarter: YYYY-QN
    match = re.match(r"^(\d{4})-Q([1-4])$", period_str)
    if match:
        year = int(match.group(1))
        quarter = int(match.group(2))
        quarter_starts = [1, 4, 7, 10]
        quarter_ends = [3, 6, 9, 12]
        start_month = quarter_starts[quarter - 1]
        end_month = quarter_ends[quarter - 1]
        start = _dt.date(year, start_month, 1)
        # Last day of end month
        if end_month == 12:
            end = _dt.date(year, 12, 31)
        else:
            end = _dt.date(year, end_month + 1, 1) - _dt.timedelta(days=1)
        return period_str, start, end

    # Month: YYYY-MM
    match = re.match(r"^(\d{4})-(\d{2})$", period_str)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        if not 1 <= month <= 12:
            raise SystemExit(f"Invalid month: {period_str}")
        start = _dt.date(year, month, 1)
        # Last day of month
        if month == 12:
            end = _dt.date(year, 12, 31)
        else:
            end = _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)
        return period_str, start, end

    # Week: YYYY-WNN (ISO week)
    match = re.match(r"^(\d{4})-W(\d{2})$", period_str)
    if match:
        year = int(match.group(1))
        week = int(match.group(2))
        if not 1 <= week <= 53:
            raise SystemExit(f"Invalid week number: {period_str}")
        # ISO week: Monday of the given week
        start = _dt.date.fromisocalendar(year, week, 1)
        end = start + _dt.timedelta(days=6)
        return period_str, start, end

    raise SystemExit(
        f"Invalid period format: '{period_str}'. "
        "Use YYYY, YYYY-QN, YYYY-MM, YYYY-WNN, or YYYY-MM-DD:YYYY-MM-DD"
    )


def parse_range(range_str: str, granularity: str) -> list[tuple[str, _dt.date, _dt.date]]:
    """Parses a range string and expands it into individual periods.

    Args:
        range_str: Range in the format START:END (e.g., "2020:2025", "2024-01:2024-06").
        granularity: One of "year", "quarter", "month", "week".

    Returns:
        List of (label, start_date, end_date) tuples for each period in the range.

    Raises:
        SystemExit: If the range or granularity is invalid.
    """
    if ":" not in range_str:
        raise SystemExit(f"Range must contain ':' separator: {range_str}")

    parts = range_str.split(":")
    if len(parts) != 2:
        raise SystemExit(f"Range must have exactly two parts: {range_str}")

    start_str, end_str = parts

    # Parse start and end as periods to get date boundaries
    _, range_start, _ = parse_period(start_str)
    _, _, range_end = parse_period(end_str)

    periods: list[tuple[str, _dt.date, _dt.date]] = []

    if granularity == "year":
        year = range_start.year
        while year <= range_end.year:
            start = _dt.date(year, 1, 1)
            end = _dt.date(year, 12, 31)
            if end > range_end:
                end = range_end
            if start < range_start:
                start = range_start
            periods.append((str(year), start, end))
            year += 1

    elif granularity == "quarter":
        current = _dt.date(range_start.year, ((range_start.month - 1) // 3) * 3 + 1, 1)
        while current <= range_end:
            year = current.year
            quarter = (current.month - 1) // 3 + 1
            label = f"{year}-Q{quarter}"
            start = current
            if quarter == 4:
                end = _dt.date(year, 12, 31)
            else:
                end = _dt.date(year, quarter * 3 + 1, 1) - _dt.timedelta(days=1)
            if end > range_end:
                end = range_end
            if start < range_start:
                start = range_start
            periods.append((label, start, end))
            # Move to next quarter
            if quarter == 4:
                current = _dt.date(year + 1, 1, 1)
            else:
                current = _dt.date(year, quarter * 3 + 1, 1)

    elif granularity == "month":
        current = _dt.date(range_start.year, range_start.month, 1)
        while current <= range_end:
            year = current.year
            month = current.month
            label = f"{year}-{month:02d}"
            start = current
            if month == 12:
                end = _dt.date(year, 12, 31)
                next_month = _dt.date(year + 1, 1, 1)
            else:
                end = _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)
                next_month = _dt.date(year, month + 1, 1)
            if end > range_end:
                end = range_end
            if start < range_start:
                start = range_start
            periods.append((label, start, end))
            current = next_month

    elif granularity == "week":
        # Start from the ISO week containing range_start
        iso_year, iso_week, _ = range_start.isocalendar()
        current = _dt.date.fromisocalendar(iso_year, iso_week, 1)
        while current <= range_end:
            iso_year, iso_week, _ = current.isocalendar()
            label = f"{iso_year}-W{iso_week:02d}"
            start = current
            end = current + _dt.timedelta(days=6)
            if end > range_end:
                end = range_end
            if start < range_start:
                start = range_start
            periods.append((label, start, end))
            current = current + _dt.timedelta(days=7)

    else:
        raise SystemExit(f"Invalid granularity: {granularity}")

    return periods


def get_extension(filepath: str) -> str:
    """Extracts the file extension from a path.

    Args:
        filepath: File path to extract extension from.

    Returns:
        Lowercase extension including the dot (e.g., ".py"), or empty string.
    """
    # Handle special filenames like "Dockerfile", "Makefile"
    basename = os.path.basename(filepath).lower()
    if basename == "dockerfile":
        return ".dockerfile"
    if basename == "makefile":
        return ".makefile"

    _, ext = os.path.splitext(filepath)
    return ext.lower()


def get_language(filepath: str) -> str:
    """Determines the programming language for a file.

    Args:
        filepath: File path to analyze.

    Returns:
        Language name, or "Other" if unrecognized.
    """
    ext = get_extension(filepath)
    return EXTENSION_LANGUAGE_MAP.get(ext, "Other")


def get_primary_language(languages: dict[str, "LanguageStats"]) -> str:
    """Determines the primary language from language stats.

    Prefers actual code languages over config/project files. Falls back to the
    language with the most lines added if no code languages are present.

    Args:
        languages: Dictionary of language name to LanguageStats.

    Returns:
        Name of the primary language, or empty string if no languages.
    """
    if not languages:
        return ""

    # First, try to find the top "code" language (not in NON_PRIMARY_LANGUAGES)
    code_langs = {
        name: stats for name, stats in languages.items() if name not in NON_PRIMARY_LANGUAGES
    }

    if code_langs:
        top = max(code_langs.values(), key=lambda x: x.lines_added)
        return top.name

    # Fall back to any language with the most lines
    top = max(languages.values(), key=lambda x: x.lines_added)
    return top.name


def date_range_to_git_args(start: _dt.date, end: _dt.date) -> tuple[str, str]:
    """Converts date boundaries to git --since/--until arguments.

    Args:
        start: First day of the period.
        end: Last day of the period.

    Returns:
        A tuple of (since_str, until_str) for git commands.
    """
    since = _dt.datetime.combine(start, _dt.time.min).strftime("%Y-%m-%d %H:%M:%S")
    until = _dt.datetime.combine(end, _dt.time.max).strftime("%Y-%m-%d %H:%M:%S")
    return since, until


def get_commit_stats(
    repo_path: str,
    since_str: str,
    until_str: str,
    author: Optional[str] = None,
    exclude_patterns: Optional[list[str]] = None,
) -> tuple[int, int, int, set[str], dict[str, LanguageStats], TimePattern, set[_dt.date]]:
    """Retrieves commit statistics from a repository.

    Args:
        repo_path: Absolute path to the repository.
        since_str: Start of the window for git --since.
        until_str: End of the window for git --until.
        author: Optional author filter for git --author.
        exclude_patterns: Optional list of glob patterns for files to exclude
            from line counts.

    Returns:
        A tuple of (commit_count, lines_added, lines_removed, files_changed,
        language_stats, time_patterns, commit_dates).
    """
    if exclude_patterns is None:
        exclude_patterns = []

    # Get commits with numstat for line counts
    # Use null byte as record separator for reliable parsing
    args = [
        "log",
        f"--since={since_str}",
        f"--until={until_str}",
        "--all",
        "--no-merges",
        "--numstat",
        "--date=iso-local",
        "--pretty=format:%x00%H%x1f%ad",
    ]
    if author:
        args.append(f"--author={author}")

    code, out, _ = run_git(repo_path, args)
    if code != 0:
        return 0, 0, 0, set(), {}, TimePattern(), set()

    commit_count = 0
    total_added = 0
    total_removed = 0
    files: set[str] = set()
    lang_stats: dict[str, LanguageStats] = {}
    time_patterns = TimePattern()
    commit_dates: set[_dt.date] = set()

    # Track files per commit to count unique files per language
    lang_files: dict[str, set[str]] = defaultdict(set)

    # Split by null byte - each record starts with commit info, followed by numstat
    # Format: \x00HASH\x1fDATE\nnumstat_line\nnumstat_line\n...
    records = out.split("\x00")
    for rec in records:
        rec = rec.strip()
        if not rec:
            continue

        lines = rec.split("\n")
        if not lines:
            continue

        # First line is commit info: HASH\x1fDATE
        header = lines[0].strip()
        if "\x1f" not in header:
            continue

        parts = header.split("\x1f")
        if len(parts) < 2:
            continue

        commit_count += 1
        date_str = parts[1].strip()

        # Parse ISO date: "2024-01-15 10:30:45 -0700"
        try:
            dt = _dt.datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
            commit_dates.add(dt.date())
            time_patterns.day_of_week_counts[dt.weekday()] += 1
            time_patterns.hour_counts[dt.hour] += 1
        except ValueError:
            pass

        # Remaining lines are numstat output for THIS commit
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            stat_parts = line.split("\t")
            if len(stat_parts) != 3:
                continue
            added_str, removed_str, filepath = stat_parts

            # Skip excluded files
            if exclude_patterns and _matches_any_pattern(filepath, exclude_patterns):
                continue

            # Binary files show "-" for added/removed
            if added_str != "-" and removed_str != "-":
                try:
                    added = int(added_str)
                    removed = int(removed_str)
                    total_added += added
                    total_removed += removed

                    lang = get_language(filepath)
                    if lang not in lang_stats:
                        lang_stats[lang] = LanguageStats(name=lang)
                    lang_stats[lang].lines_added += added
                    lang_stats[lang].lines_removed += removed
                    lang_files[lang].add(filepath)
                except ValueError:
                    pass

            files.add(filepath)

    # Update file counts per language
    for lang, file_set in lang_files.items():
        if lang in lang_stats:
            lang_stats[lang].files_changed = len(file_set)

    return commit_count, total_added, total_removed, files, lang_stats, time_patterns, commit_dates


def get_repo_remote_url(repo_path: str) -> Optional[str]:
    """Gets the primary remote URL for a repository.

    Args:
        repo_path: Absolute path to the repository.

    Returns:
        The origin remote URL, or None if not available.
    """
    code, out, _ = run_git(repo_path, ["remote", "get-url", "origin"])
    if code == 0 and out.strip():
        return out.strip()
    return None


def calculate_longest_streak(commit_dates: set[_dt.date]) -> int:
    """Calculates the longest consecutive days with commits.

    Args:
        commit_dates: Set of dates that had at least one commit.

    Returns:
        Length of the longest streak in days.
    """
    if not commit_dates:
        return 0

    sorted_dates = sorted(commit_dates)
    longest = 1
    current = 1

    for i in range(1, len(sorted_dates)):
        if sorted_dates[i] - sorted_dates[i - 1] == _dt.timedelta(days=1):
            current += 1
            longest = max(longest, current)
        else:
            current = 1

    return longest


def _process_single_repo(
    repo_path: str,
    project_name: str,
    since_str: str,
    until_str: str,
    author: str,
    exclude_patterns: Optional[list[str]],
) -> Optional[ProjectSummary]:
    """Processes a single repository and returns its summary.

    Args:
        repo_path: Absolute path to the repository.
        project_name: Display name for this repository.
        since_str: Start of the window for git --since.
        until_str: End of the window for git --until.
        author: Author filter for git commands.
        exclude_patterns: Optional list of glob patterns for files to exclude.

    Returns:
        ProjectSummary if the repo has commits, None otherwise.
    """
    summary = ProjectSummary(project_name=project_name, project_path=repo_path)

    (commits, added, removed, files, langs, patterns, dates) = get_commit_stats(
        repo_path, since_str, until_str, author, exclude_patterns
    )

    if commits == 0:
        return None

    summary.commit_count = commits
    summary.lines_added = added
    summary.lines_removed = removed
    summary.files_changed = files
    summary.commit_dates = dates
    summary.languages = langs

    if summary.commit_dates:
        summary.first_commit_date = min(summary.commit_dates)
        summary.last_commit_date = max(summary.commit_dates)

    return summary


def process_repos_for_period(
    repos: list[str],
    start_date: _dt.date,
    end_date: _dt.date,
    author: str,
    exclude_config: Optional[ExcludeConfig] = None,
) -> list[ProjectSummary]:
    """Processes all repositories and their submodules for a given time period.

    Each repository and submodule is processed as a separate project. Submodules
    are deduplicated by remote URL to avoid double-counting shared dependencies.

    Args:
        repos: List of repository paths to process.
        start_date: First day of the period.
        end_date: Last day of the period.
        author: Author filter for git commands.
        exclude_config: Optional ExcludeConfig with global and per-project patterns.

    Returns:
        List of ProjectSummary objects for repositories with activity.
    """
    if exclude_config is None:
        exclude_config = ExcludeConfig()
    since_str, until_str = date_range_to_git_args(start_date, end_date)
    summaries: list[ProjectSummary] = []

    # Track processed repos by remote URL to avoid duplicates
    processed_urls: set[str] = set()
    # Also track by realpath for repos without remotes
    processed_paths: set[str] = set()

    def is_already_processed(path: str) -> bool:
        """Checks if a repo has already been processed."""
        real_path = os.path.realpath(path)
        if real_path in processed_paths:
            return True
        url = get_repo_remote_url(path)
        return bool(url and url in processed_urls)

    def mark_as_processed(path: str) -> None:
        """Marks a repo as processed."""
        processed_paths.add(os.path.realpath(path))
        url = get_repo_remote_url(path)
        if url:
            processed_urls.add(url)

    # Build queue of repos to process (top-level + all submodules)
    repo_queue: list[tuple[str, str]] = []  # (path, display_name)

    for repo_path in repos:
        project_name = os.path.basename(repo_path)
        repo_queue.append((repo_path, project_name))

        # Discover all submodules recursively
        for sub_path in discover_all_submodules(repo_path):
            sub_name = os.path.basename(sub_path)
            repo_queue.append((sub_path, sub_name))

    # Process each repo
    for repo_path, display_name in repo_queue:
        if is_already_processed(repo_path):
            continue

        mark_as_processed(repo_path)

        # Get patterns for this project (global + project-specific)
        project_patterns = exclude_config.get_patterns_for_project(display_name)

        summary = _process_single_repo(
            repo_path, display_name, since_str, until_str, author, project_patterns
        )
        if summary:
            summaries.append(summary)

    return summaries


def aggregate_period_stats(
    period_label: str,
    start_date: _dt.date,
    end_date: _dt.date,
    project_summaries: list[ProjectSummary],
) -> PeriodStats:
    """Aggregates project summaries into period-level statistics.

    Args:
        period_label: Label for this period.
        start_date: First day of the period.
        end_date: Last day of the period.
        project_summaries: Per-project statistics to aggregate.

    Returns:
        PeriodStats with aggregated totals.
    """
    stats = PeriodStats(
        period_label=period_label,
        start_date=start_date,
        end_date=end_date,
        project_summaries=project_summaries,
        projects_active=len(project_summaries),
    )

    all_files: set[str] = set()
    all_dates: set[_dt.date] = set()
    lang_totals: dict[str, LanguageStats] = {}

    for proj in project_summaries:
        stats.commits += proj.commit_count
        stats.lines_added += proj.lines_added
        stats.lines_removed += proj.lines_removed
        all_files.update(proj.files_changed)
        all_dates.update(proj.commit_dates)

        # Aggregate language stats
        for lang, lang_stats in proj.languages.items():
            if lang not in lang_totals:
                lang_totals[lang] = LanguageStats(name=lang)
            lang_totals[lang].lines_added += lang_stats.lines_added
            lang_totals[lang].lines_removed += lang_stats.lines_removed
            lang_totals[lang].files_changed += lang_stats.files_changed

    stats.files_changed = len(all_files)
    stats.active_days = len(all_dates)
    stats.longest_streak = calculate_longest_streak(all_dates)
    stats.languages = lang_totals

    # Compute time patterns from all commit dates
    # Re-fetch from repos is expensive, so we use commit_dates from summaries
    # For detailed time patterns, we'd need to store hour data in project summaries

    # Find top language (prefer code languages over config/project files)
    if lang_totals:
        top_lang_name = get_primary_language(lang_totals)
        if top_lang_name and top_lang_name in lang_totals:
            stats.top_language = top_lang_name
            stats.top_language_lines = lang_totals[top_lang_name].lines_added

    return stats


def format_number(n: int) -> str:
    """Formats a number with thousand separators.

    Args:
        n: Number to format.

    Returns:
        Formatted string with commas.
    """
    return f"{n:,}"


def format_text_output(stats: PeriodStats, author: str) -> str:
    """Formats period statistics as plain text.

    Args:
        stats: Statistics to format.
        author: Author name for the header.

    Returns:
        Formatted text string.
    """
    lines: list[str] = []

    # Header
    lines.append(f"{stats.period_label} in Review - {author}")
    lines.append("=" * len(lines[0]))
    lines.append("")

    # Period dates
    start_str = stats.start_date.strftime("%B %d, %Y")
    end_str = stats.end_date.strftime("%B %d, %Y")
    lines.append(f"Period: {start_str} - {end_str}")
    lines.append("")

    # Summary
    lines.append("Summary")
    lines.append("-" * 7)
    days_in_period = (stats.end_date - stats.start_date).days + 1
    weeks_in_period = days_in_period / 7
    commits_per_week = stats.commits / weeks_in_period if weeks_in_period > 0 else 0

    lines.append(f"  Total Commits:   {format_number(stats.commits)}")
    lines.append(f"  Lines Added:     {format_number(stats.lines_added)}")
    lines.append(f"  Lines Removed:   {format_number(stats.lines_removed)}")
    net = stats.lines_added - stats.lines_removed
    sign = "+" if net >= 0 else ""
    lines.append(f"  Net Lines:       {sign}{format_number(net)}")
    lines.append(f"  Files Changed:   {format_number(stats.files_changed)}")
    lines.append(f"  Active Days:     {stats.active_days}")
    lines.append(f"  Commits/Week:    {commits_per_week:.1f}")
    lines.append(f"  Projects Active: {stats.projects_active}")
    lines.append("")

    # Languages
    if stats.languages:
        lines.append("Languages")
        lines.append("-" * 9)
        sorted_langs = sorted(stats.languages.values(), key=lambda x: x.lines_added, reverse=True)
        for lang in sorted_langs[:10]:
            lines.append(
                f"  {lang.name:20} | {lang.files_changed:4} files | "
                f"+{format_number(lang.lines_added):>8} / -{format_number(lang.lines_removed):>8}"
            )
        lines.append("")

    # Streaks
    lines.append("Streaks")
    lines.append("-" * 7)
    lines.append(f"  Longest Streak: {stats.longest_streak} consecutive days")
    lines.append("")

    # Projects
    if stats.project_summaries:
        lines.append("Projects")
        lines.append("-" * 8)
        sorted_projects = sorted(
            stats.project_summaries, key=lambda x: x.commit_count, reverse=True
        )
        for proj in sorted_projects:
            top_lang = get_primary_language(proj.languages)
            lines.append(
                f"  {proj.project_name:30} | {proj.commit_count:4} commits | "
                f"+{format_number(proj.lines_added):>7}/-{format_number(proj.lines_removed):>7} | {top_lang}"
            )

    return "\n".join(lines)


def format_markdown_output(stats: PeriodStats, author: str) -> str:
    """Formats period statistics as Markdown.

    Args:
        stats: Statistics to format.
        author: Author name for the header.

    Returns:
        Formatted Markdown string.
    """
    lines: list[str] = []

    # Header
    lines.append(f"# {stats.period_label} in Review - {author}")
    lines.append("")

    # Period dates
    start_str = stats.start_date.strftime("%B %d, %Y")
    end_str = stats.end_date.strftime("%B %d, %Y")
    lines.append(f"**Period:** {start_str} - {end_str}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")

    days_in_period = (stats.end_date - stats.start_date).days + 1
    weeks_in_period = days_in_period / 7
    commits_per_week = stats.commits / weeks_in_period if weeks_in_period > 0 else 0
    net = stats.lines_added - stats.lines_removed
    sign = "+" if net >= 0 else ""

    lines.append(f"| Total Commits | {format_number(stats.commits)} |")
    lines.append(f"| Lines Added | {format_number(stats.lines_added)} |")
    lines.append(f"| Lines Removed | {format_number(stats.lines_removed)} |")
    lines.append(f"| Net Lines | {sign}{format_number(net)} |")
    lines.append(f"| Files Changed | {format_number(stats.files_changed)} |")
    lines.append(f"| Active Days | {stats.active_days} |")
    lines.append(f"| Commits/Week | {commits_per_week:.1f} |")
    lines.append(f"| Projects Active | {stats.projects_active} |")
    lines.append("")

    # Languages table
    if stats.languages:
        lines.append("## Languages")
        lines.append("")
        lines.append("| Language | Files | Lines Added | Lines Removed |")
        lines.append("|----------|-------|-------------|---------------|")
        sorted_langs = sorted(stats.languages.values(), key=lambda x: x.lines_added, reverse=True)
        for lang in sorted_langs[:10]:
            lines.append(
                f"| {lang.name} | {lang.files_changed} | "
                f"{format_number(lang.lines_added)} | {format_number(lang.lines_removed)} |"
            )
        lines.append("")

    # Streaks
    lines.append("## Streaks")
    lines.append("")
    lines.append(f"- **Longest Streak:** {stats.longest_streak} consecutive days")
    lines.append("")

    # Projects table
    if stats.project_summaries:
        lines.append("## Projects")
        lines.append("")
        lines.append("| Project | Commits | Lines +/- | Top Language |")
        lines.append("|---------|---------|-----------|--------------|")
        sorted_projects = sorted(
            stats.project_summaries, key=lambda x: x.commit_count, reverse=True
        )
        for proj in sorted_projects:
            top_lang = get_primary_language(proj.languages)
            lines.append(
                f"| {proj.project_name} | {proj.commit_count} | "
                f"+{format_number(proj.lines_added)}/-{format_number(proj.lines_removed)} | {top_lang} |"
            )

    return "\n".join(lines)


def format_csv_output(all_stats: list[PeriodStats]) -> str:
    """Formats multiple period statistics as CSV.

    Includes per-language columns for lines added, allowing charting of
    language trends over time.

    Args:
        all_stats: List of period statistics.

    Returns:
        CSV formatted string.
    """
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    # Collect all unique languages across all periods (sorted by total lines)
    all_languages: dict[str, int] = {}
    for stats in all_stats:
        for lang_name, lang_stats in stats.languages.items():
            if lang_name not in all_languages:
                all_languages[lang_name] = 0
            all_languages[lang_name] += lang_stats.lines_added

    # Sort languages by total lines added (descending) and take top languages
    sorted_languages = sorted(all_languages.keys(), key=lambda x: all_languages[x], reverse=True)

    # Base header columns
    base_columns = [
        "period",
        "start_date",
        "end_date",
        "commits",
        "lines_added",
        "lines_removed",
        "net_lines",
        "files_changed",
        "active_days",
        "commits_per_week",
        "longest_streak",
        "top_language",
        "projects_active",
    ]

    # Add language columns (lines added for each language)
    language_columns = [
        f"lang_{lang.lower().replace(' ', '_').replace('/', '_').replace('+', 'plus')}_added"
        for lang in sorted_languages
    ]

    writer.writerow(base_columns + language_columns)

    for stats in all_stats:
        days_in_period = (stats.end_date - stats.start_date).days + 1
        weeks_in_period = days_in_period / 7
        commits_per_week = stats.commits / weeks_in_period if weeks_in_period > 0 else 0

        # Base row data
        row = [
            stats.period_label,
            stats.start_date.isoformat(),
            stats.end_date.isoformat(),
            stats.commits,
            stats.lines_added,
            stats.lines_removed,
            stats.lines_added - stats.lines_removed,
            stats.files_changed,
            stats.active_days,
            f"{commits_per_week:.2f}",
            stats.longest_streak,
            stats.top_language,
            stats.projects_active,
        ]

        # Add language data (0 if language not present in this period)
        for lang in sorted_languages:
            if lang in stats.languages:
                row.append(stats.languages[lang].lines_added)
            else:
                row.append(0)

        writer.writerow(row)

    return output.getvalue()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the CLI tool.

    Args:
        argv: Optional sequence of command-line arguments. If None, uses
            sys.argv.

    Returns:
        Process exit code: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Generate a time period in review summary of git activity. "
            "Supports single periods (text/markdown) and ranges (CSV)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 2024                                # Full year (uses git config user.name)
  %(prog)s 2024 --author "@example.com"        # Match by email domain
  %(prog)s 2024-Q3                             # Quarter
  %(prog)s 2024-06                             # Month
  %(prog)s 2024-W25                            # Week
  %(prog)s 2024-06-01:2024-06-15               # Custom range

  # Range mode (CSV output for charting)
  %(prog)s 2020:2025 --granularity year
  %(prog)s 2024-01:2024-12 --granularity month --format csv
        """,
    )
    parser.add_argument(
        "period",
        help=(
            "Time period to analyze. Formats: YYYY (year), YYYY-QN (quarter), "
            "YYYY-MM (month), YYYY-WNN (week), or START:END for ranges."
        ),
    )
    add_author_arg(parser)
    add_root_arg(parser)
    add_filter_arg(parser)
    parser.add_argument(
        "--format",
        choices=["text", "markdown", "csv"],
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--granularity",
        choices=["year", "quarter", "month", "week"],
        default=None,
        help="Granularity for range mode (required when using ranges).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file path. Default: output/[client/]activity-<period>.<ext>",
    )
    parser.add_argument(
        "--client",
        metavar="NAME",
        help="Client name for organizing output files (creates output/<client>/ subdirectory).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write output to stdout instead of files.",
    )
    add_output_dir_arg(parser)
    add_exclude_args(parser, detailed_help=True)
    add_fetch_arg(parser, detailed_help=True)
    parser.add_argument(
        "--error-on-fetch-failure",
        action="store_true",
        help=(
            "Abort if any repository fetch fails. By default, fetch failures "
            "are logged as warnings and processing continues."
        ),
    )

    args = parser.parse_args(argv)

    resolve_author(args, parser)

    root = os.path.abspath(args.root)

    # Build exclude configuration
    exclude_config = ExcludeConfig()

    # Add default excludes
    if not args.no_default_excludes:
        exclude_config.global_patterns.extend(DEFAULT_EXCLUDE_PATTERNS)

    # Add command-line excludes
    exclude_config.global_patterns.extend(args.exclude)

    # Load excludes file
    if not args.no_excludes_file:
        if args.excludes_file:
            # Explicit excludes file specified
            excludes_file = args.excludes_file
            if os.path.isfile(excludes_file):
                file_config = load_excludes_file(excludes_file)
                exclude_config.global_patterns.extend(file_config.global_patterns)
                for project, patterns in file_config.project_patterns.items():
                    if project not in exclude_config.project_patterns:
                        exclude_config.project_patterns[project] = []
                    exclude_config.project_patterns[project].extend(patterns)
                print(f"Loaded excludes from: {excludes_file}", file=sys.stderr)
        else:
            # Try unified config first, fall back to legacy excludes file
            config_file = get_config_path()
            if config_file.exists():
                file_config = load_excludes_from_config(str(config_file))
                if file_config.global_patterns or file_config.project_patterns:
                    exclude_config.global_patterns.extend(file_config.global_patterns)
                    for project, patterns in file_config.project_patterns.items():
                        if project not in exclude_config.project_patterns:
                            exclude_config.project_patterns[project] = []
                        exclude_config.project_patterns[project].extend(patterns)
                    print(f"Loaded excludes from: {config_file}", file=sys.stderr)
            else:
                # Fall back to legacy excludes file
                legacy_excludes = config_file.parent / "excludes.yaml"
                if legacy_excludes.exists():
                    file_config = load_excludes_file(str(legacy_excludes))
                    exclude_config.global_patterns.extend(file_config.global_patterns)
                    for project, patterns in file_config.project_patterns.items():
                        if project not in exclude_config.project_patterns:
                            exclude_config.project_patterns[project] = []
                        exclude_config.project_patterns[project].extend(patterns)
                    print(f"Loaded excludes from: {legacy_excludes}", file=sys.stderr)

    # Discover repositories
    repos = discover_top_level_repos(root)
    if not repos:
        print(f"No git repositories found under: {root}", file=sys.stderr)
        return 1

    # Filter repositories by name if requested
    if args.filter:
        import fnmatch

        filtered_repos = []
        for repo in repos:
            repo_name = os.path.basename(repo)
            for pattern in args.filter:
                pattern_lower = pattern.lower()
                repo_name_lower = repo_name.lower()
                # Try glob match first, then substring match
                if (
                    fnmatch.fnmatch(repo_name_lower, pattern_lower)
                    or pattern_lower in repo_name_lower
                ):
                    filtered_repos.append(repo)
                    break
        repos = filtered_repos
        if not repos:
            print(f"No repositories matched filter(s): {', '.join(args.filter)}", file=sys.stderr)
            return 1
        print(
            f"Filtered to {len(repos)} repositories matching: {', '.join(args.filter)}",
            file=sys.stderr,
        )

    # Fetch all repos and submodules once at the start (if --fetch)
    if args.fetch:
        _, fetch_success = fetch_repos_with_progress(
            repos,
            include_submodules=True,
            max_workers=8,
            error_on_failure=args.error_on_fetch_failure,
            output=sys.stderr,
        )
        if not fetch_success:
            return 1

    # Determine if this is a range or single period
    is_range = ":" in args.period and not re.match(
        r"^\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$", args.period
    )

    if is_range:
        # Range mode
        if not args.granularity:
            print("Error: --granularity is required when using range mode.", file=sys.stderr)
            return 1

        periods = parse_range(args.period, args.granularity)
        all_stats: list[PeriodStats] = []

        for label, start, end in periods:
            summaries = process_repos_for_period(repos, start, end, args.author, exclude_config)
            stats = aggregate_period_stats(label, start, end, summaries)
            all_stats.append(stats)

        output = format_csv_output(all_stats)

    else:
        # Single period mode
        label, start, end = parse_period(args.period)
        summaries = process_repos_for_period(repos, start, end, args.author, exclude_config)
        stats = aggregate_period_stats(label, start, end, summaries)

        if args.format == "markdown":
            output = format_markdown_output(stats, args.author)
        elif args.format == "csv":
            output = format_csv_output([stats])
        else:
            output = format_text_output(stats, args.author)

    # Determine output path
    if args.output:
        output_path = args.output
    elif args.stdout:
        output_path = None
    else:
        # Default: save to output directory with sensible name
        output_dir_path = get_output_dir(
            output_dir=args.output_dir,
            period=args.period.split(":")[0] if ":" in args.period else args.period,
            client=args.client,
        )
        output_dir = str(output_dir_path)

        os.makedirs(output_dir, exist_ok=True)

        # Determine extension
        if args.format == "csv" or is_range:
            ext = "csv"
        elif args.format == "markdown":
            ext = "md"
        else:
            ext = "txt"

        output_path = os.path.join(output_dir, f"activity-{args.period.replace(':', '-to-')}.{ext}")

    # Write output
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            f.write(output)
        print(f"Output written to: {output_path}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
