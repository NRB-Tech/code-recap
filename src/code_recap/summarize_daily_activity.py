#!/usr/bin/env python3
"""Summarize git activity for a specific date using LLM.

Generates concise summaries of git changes for each project on a given date,
designed to assist with time logging and billing documentation. Outputs to
stdout with each project formatted separately.

Requires the litellm package and appropriate API keys set as environment
variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY).
"""

import argparse
import datetime as _dt
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Optional

from code_recap.arguments import (
    add_author_arg,
    add_filter_arg,
    add_model_args,
    add_root_arg,
    resolve_author,
)
from code_recap.git_utils import (
    CommitInfo,
    discover_all_submodules,
    discover_top_level_repos,
    get_commits_with_diffs,
    run_git,
)
from code_recap.paths import load_api_keys_from_config

# Default model
DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class ProjectActivity:
    """Activity for a single project on a given date.

    Attributes:
        project_name: Display name of the project.
        project_path: Absolute path to the project repository.
        commits: List of commits with details.
    """

    project_name: str
    project_path: str
    commits: list[CommitInfo] = field(default_factory=list)


@dataclass
class CostTracker:
    """Tracks LLM API usage and costs.

    Attributes:
        total_input_tokens: Cumulative input tokens used.
        total_output_tokens: Cumulative output tokens used.
        total_cost: Cumulative cost in USD.
        call_count: Number of LLM calls made.
    """

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    call_count: int = 0

    def add(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        """Records usage from a single LLM call.

        Args:
            input_tokens: Number of input tokens used.
            output_tokens: Number of output tokens generated.
            cost: Cost in USD for this call.
        """
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost
        self.call_count += 1


def get_repo_remote_url_cached(repo_path: str, cache: dict[str, Optional[str]]) -> Optional[str]:
    """Gets the primary remote URL for a repository with caching.

    Args:
        repo_path: Absolute path to the repository.
        cache: Dictionary cache for remote URLs.

    Returns:
        The origin remote URL, or None if not available.
    """
    if repo_path not in cache:
        code, out, _ = run_git(repo_path, ["remote", "get-url", "origin"])
        cache[repo_path] = out.strip() if code == 0 and out.strip() else None
    return cache[repo_path]


def date_to_git_range(target_date: _dt.date) -> tuple[str, str]:
    """Converts a date to git --since/--until arguments for that full day.

    Args:
        target_date: The date to query.

    Returns:
        A tuple of (since_str, until_str) covering the entire day.
    """
    since = _dt.datetime.combine(target_date, _dt.time.min).strftime("%Y-%m-%d %H:%M:%S")
    until = _dt.datetime.combine(target_date, _dt.time.max).strftime("%Y-%m-%d %H:%M:%S")
    return since, until


def gather_daily_activity(
    repos: list[str],
    target_date: _dt.date,
    author: str,
    max_diff_lines: int = 300,
) -> list[ProjectActivity]:
    """Gathers git activity for a specific date across all repositories.

    Args:
        repos: List of repository paths to check.
        target_date: The date to gather activity for.
        author: Author filter for git commands.
        max_diff_lines: Maximum lines of diff to include per commit.

    Returns:
        List of ProjectActivity objects for repos with commits on that date.
    """
    since_str, until_str = date_to_git_range(target_date)
    activities: list[ProjectActivity] = []

    # Track processed repos by remote URL to avoid duplicates
    processed_urls: set[str] = set()
    processed_paths: set[str] = set()
    url_cache: dict[str, Optional[str]] = {}

    def is_already_processed(path: str) -> bool:
        """Checks if a repo has already been processed."""
        real_path = os.path.realpath(path)
        if real_path in processed_paths:
            return True
        url = get_repo_remote_url_cached(path, url_cache)
        return bool(url and url in processed_urls)

    def mark_as_processed(path: str) -> None:
        """Marks a repo as processed."""
        processed_paths.add(os.path.realpath(path))
        url = get_repo_remote_url_cached(path, url_cache)
        if url:
            processed_urls.add(url)

    # Build queue of repos to process (top-level + submodules)
    repo_queue: list[tuple[str, str]] = []

    for repo_path in repos:
        project_name = os.path.basename(repo_path)
        repo_queue.append((repo_path, project_name))

        for sub_path in discover_all_submodules(repo_path):
            sub_name = os.path.basename(sub_path)
            repo_queue.append((sub_path, sub_name))

    # Process each repo
    for repo_path, display_name in repo_queue:
        if is_already_processed(repo_path):
            continue

        mark_as_processed(repo_path)

        commits = get_commits_with_diffs(repo_path, since_str, until_str, author, max_diff_lines)

        if commits:
            activities.append(
                ProjectActivity(
                    project_name=display_name,
                    project_path=repo_path,
                    commits=commits,
                )
            )

    return activities


DAILY_SUMMARY_SYSTEM_PROMPT = """You are an expert at summarizing software development work for time tracking and billing purposes.

Given git commits and diffs from a single day, provide a concise summary of the work done. The summary should be:

1. **Brief but specific**: Capture what was accomplished in 2-4 bullet points
2. **Action-oriented**: Use past tense verbs (e.g., "Implemented", "Fixed", "Refactored", "Added")
3. **Technical but accessible**: Include relevant technical details without being overly verbose
4. **Billing-friendly**: Focus on deliverables and concrete accomplishments

Format your response as a simple bulleted list (using - for bullets). Do NOT include:
- The project name (it will be added separately)
- Time estimates
- Commit hashes or dates
- Section headers

Just provide the bullet points summarizing what was done."""


def call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cost_tracker: CostTracker,
    temperature: float = 0.3,
) -> str:
    """Calls the LLM via LiteLLM and tracks costs.

    Args:
        model: LiteLLM model string (e.g., 'gpt-4o-mini').
        system_prompt: System prompt to set context.
        user_prompt: User prompt with the actual request.
        cost_tracker: CostTracker instance to update.
        temperature: LLM temperature (default: 0.3).

    Returns:
        The LLM's response text.

    Raises:
        SystemExit: If litellm is not installed.
    """
    try:
        from litellm import completion  # pyright: ignore[reportMissingImports]
    except ImportError as err:
        raise SystemExit("litellm is required. Install with: pip install litellm") from err

    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )

    # Extract cost information from response
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0

    cost = 0.0
    if hasattr(response, "_hidden_params"):
        cost = response._hidden_params.get("response_cost", 0.0) or 0.0

    cost_tracker.add(input_tokens, output_tokens, cost)

    return response.choices[0].message.content


def format_project_prompt(activity: ProjectActivity, include_diffs: bool) -> str:
    """Formats a project's activity as a prompt for the LLM.

    Args:
        activity: ProjectActivity with commits for one project.
        include_diffs: Whether to include diff content in the prompt.

    Returns:
        Formatted prompt string.
    """
    lines: list[str] = []

    lines.append(f"Project: {activity.project_name}")
    lines.append(f"Number of commits: {len(activity.commits)}")
    lines.append("")
    lines.append("Commits:")
    lines.append("-" * 40)

    for commit in activity.commits:
        lines.append(f"Subject: {commit.subject}")
        if commit.body:
            lines.append(f"Body: {commit.body}")

        if include_diffs and commit.diff:
            lines.append("")
            lines.append("Diff:")
            lines.append(commit.diff)

        lines.append("-" * 40)

    return "\n".join(lines)


def format_output_without_llm(activities: list[ProjectActivity], target_date: _dt.date) -> str:
    """Formats activity output without LLM summarization.

    Args:
        activities: List of project activities.
        target_date: The date being summarized.

    Returns:
        Formatted string with commit details per project.
    """
    lines: list[str] = []
    date_str = target_date.strftime("%A, %B %d, %Y")

    lines.append(f"Git Activity for {date_str}")
    lines.append("=" * 50)
    lines.append("")

    for activity in sorted(activities, key=lambda a: a.project_name.lower()):
        lines.append(f"## {activity.project_name}")
        lines.append("")

        for commit in activity.commits:
            lines.append(f"- {commit.subject}")
            if commit.body:
                # Indent body lines
                for body_line in commit.body.split("\n"):
                    if body_line.strip():
                        lines.append(f"  {body_line}")

        lines.append("")

    total_commits = sum(len(a.commits) for a in activities)
    lines.append("-" * 50)
    lines.append(f"Total: {total_commits} commits across {len(activities)} projects")

    return "\n".join(lines)


def format_output_with_llm(
    activities: list[ProjectActivity],
    target_date: _dt.date,
    model: str,
    include_diffs: bool,
    cost_tracker: CostTracker,
    temperature: float = 0.3,
) -> str:
    """Formats activity output with LLM-generated summaries.

    Args:
        activities: List of project activities.
        target_date: The date being summarized.
        model: LLM model to use.
        include_diffs: Whether to include diffs in LLM prompts.
        cost_tracker: CostTracker instance to update.
        temperature: LLM temperature (default: 0.3).

    Returns:
        Formatted string with LLM summaries per project.
    """
    lines: list[str] = []
    date_str = target_date.strftime("%A, %B %d, %Y")

    lines.append(f"Git Activity Summary for {date_str}")
    lines.append("=" * 50)
    lines.append("")

    for activity in sorted(activities, key=lambda a: a.project_name.lower()):
        print(
            f"  Summarizing {activity.project_name} ({len(activity.commits)} commits)...",
            file=sys.stderr,
        )

        prompt = format_project_prompt(activity, include_diffs)
        summary = call_llm(model, DAILY_SUMMARY_SYSTEM_PROMPT, prompt, cost_tracker, temperature)

        lines.append(f"## {activity.project_name}")
        lines.append("")
        lines.append(summary.strip())
        lines.append("")

    total_commits = sum(len(a.commits) for a in activities)
    lines.append("-" * 50)
    lines.append(f"Total: {total_commits} commits across {len(activities)} projects")
    lines.append(f"LLM cost: ${cost_tracker.total_cost:.4f}")

    return "\n".join(lines)


def parse_date(date_str: str) -> _dt.date:
    """Parses a date string in various formats.

    Args:
        date_str: Date string to parse. Supports:
            - "today" or "yesterday"
            - "YYYY-MM-DD"
            - "-N" for N days ago

    Returns:
        Parsed date object.

    Raises:
        SystemExit: If the date string is invalid.
    """
    date_str = date_str.strip().lower()

    if date_str == "today":
        return _dt.date.today()
    if date_str == "yesterday":
        return _dt.date.today() - _dt.timedelta(days=1)

    # Relative days: -1, -2, etc.
    if date_str.startswith("-") and date_str[1:].isdigit():
        days_ago = int(date_str[1:])
        return _dt.date.today() - _dt.timedelta(days=days_ago)

    # ISO format: YYYY-MM-DD
    try:
        return _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        pass

    raise SystemExit(
        f"Invalid date format: '{date_str}'. "
        "Use YYYY-MM-DD, 'today', 'yesterday', or '-N' for N days ago."
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the CLI tool.

    Args:
        argv: Optional sequence of command-line arguments.

    Returns:
        Process exit code: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Summarize git activity for a specific date using LLM. "
            "Outputs concise summaries per project for time logging."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              # Today's activity (uses git config user.name)
  %(prog)s --author "@example.com"      # Match by email domain
  %(prog)s --date yesterday             # Yesterday's activity
  %(prog)s --date 2025-01-03            # Specific date
  %(prog)s --date -2                    # 2 days ago
  %(prog)s --no-llm                     # Just list commits, no LLM

Models (LiteLLM format):
  gpt-4o-mini                              OpenAI (cheapest, default)
  gemini/gemini-2.0-flash-lite             Google (cheapest Gemini)
  anthropic/claude-3-5-haiku-latest        Anthropic (cheapest Claude)
        """,
    )
    parser.add_argument(
        "--date",
        default="today",
        help=(
            "Date to summarize. Formats: YYYY-MM-DD, 'today', 'yesterday', "
            "or '-N' for N days ago (default: today)."
        ),
    )
    add_author_arg(parser)
    add_root_arg(parser)
    add_filter_arg(parser)
    add_model_args(parser, DEFAULT_MODEL)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM summarization; just list commits per project.",
    )
    parser.add_argument(
        "--no-diffs",
        action="store_true",
        help="Exclude diff content from LLM prompts (faster, less detailed).",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=300,
        metavar="N",
        help="Maximum lines of diff to include per commit (default: 300).",
    )

    args = parser.parse_args(argv)

    # Load API keys from config (if available)
    load_api_keys_from_config()

    resolve_author(args, parser)

    # Parse target date
    target_date = parse_date(args.date)
    date_display = target_date.strftime("%Y-%m-%d (%A)")
    print(f"Gathering activity for {date_display}...", file=sys.stderr)

    # Discover repositories
    root = os.path.abspath(args.root)
    repos = discover_top_level_repos(root)

    if not repos:
        print(f"No git repositories found under: {root}", file=sys.stderr)
        return 1

    # Filter repositories if requested
    if args.filter:
        import fnmatch

        filtered_repos = []
        for repo in repos:
            repo_name = os.path.basename(repo)
            for pattern in args.filter:
                pattern_lower = pattern.lower()
                repo_name_lower = repo_name.lower()
                if (
                    fnmatch.fnmatch(repo_name_lower, pattern_lower)
                    or pattern_lower in repo_name_lower
                ):
                    filtered_repos.append(repo)
                    break
        repos = filtered_repos

        if not repos:
            print(
                f"No repositories matched filter(s): {', '.join(args.filter)}",
                file=sys.stderr,
            )
            return 1

    # Gather activity
    activities = gather_daily_activity(repos, target_date, args.author, args.max_diff_lines)

    if not activities:
        print(f"No commits found for {args.author} on {date_display}.", file=sys.stderr)
        return 0

    print(
        f"Found {sum(len(a.commits) for a in activities)} commits "
        f"across {len(activities)} projects.",
        file=sys.stderr,
    )

    # Generate output
    if args.no_llm:
        output = format_output_without_llm(activities, target_date)
    else:
        print("Generating summaries...", file=sys.stderr)
        cost_tracker = CostTracker()
        include_diffs = not args.no_diffs
        output = format_output_with_llm(
            activities,
            target_date,
            args.model,
            include_diffs,
            cost_tracker,
            args.temperature,
        )
        print(f"Done. Total cost: ${cost_tracker.total_cost:.4f}", file=sys.stderr)

    print("")
    print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
