#!/usr/bin/env python3
"""Generate blog posts from git activity using a two-stage LLM pipeline.

This script researches git activity related to a blog post idea, then generates
a polished blog post. The two-stage approach allows for human review between
research and writing.

Stage 1 (research): Gather commits, identify relevant changes, output research summary
Stage 2 (write): Transform research summary into a blog post with full diff context

Requires the litellm package and appropriate API keys set as environment
variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY).
"""

import argparse
import datetime as _dt
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional, Union

from code_recap.arguments import (
    add_author_arg,
    add_config_arg,
    add_fetch_arg,
    add_filter_arg,
    add_model_args,
    add_root_arg,
    resolve_author,
)
from code_recap.formatting import print_heading
from code_recap.git_activity_review import (
    date_range_to_git_args,
    parse_period,
)
from code_recap.git_utils import (
    CommitInfo,
    discover_all_submodules,
    discover_top_level_repos,
    fetch_repos_with_progress,
    get_commits_with_diffs,
    run_git,
)
from code_recap.paths import get_config_path, load_api_keys_from_config
from code_recap.summarize_activity import (
    RECOMMENDED_MODELS,
    CostTracker,
    call_llm,
    load_config,
)

DEFAULT_MODEL = "gpt-4o-mini"

# Research stage system prompt
RESEARCH_SYSTEM_PROMPT = """You are an expert software developer analyzing git commits to identify material for a blog post.

Your task is to review the provided git activity and identify commits that are DIRECTLY relevant to the blog post topic. The writing stage will receive both the commit diffs AND the final state of key files, so your job is to:

1. Identify which commits are relevant
2. Briefly explain WHY each is relevant to the topic
3. Identify the KEY FILES that represent the final implementation (these will be retrieved in full)
4. Suggest how the content might be organized into a narrative

IMPORTANT: Only include commits that are directly relevant to the topic. Do NOT include tangentially related changes just to have something to report. Quality over quantity.

Structure your response as follows:

## Summary
A 2-3 sentence overview of the relevant work found and how it relates to the blog topic.

## Key Files
List the most important files that represent the implementation. These files will be retrieved to show the complete implementation (not just diffs).

Format: `path/to/file.ext` (repo-name) - brief description of what this file contains

You can optionally specify which version of the file to retrieve:
- `path/to/file.ext` (repo-name) - the final version (last commit in the period that touched it)
- `path/to/file.ext` (repo-name) @ before - the version BEFORE any changes in this period
- `path/to/file.ext` (repo-name) @ abc123de - a specific commit

Use "@ before" when showing how something worked previously (e.g., "this is how we used to do it").
Use specific commit SHAs when a particular intermediate state is important.

Include 3-8 files maximum. Focus on files that:
- Contain the core implementation logic
- Would be most useful for readers to see in full
- Represent the "finished product" rather than intermediate states

## Relevant Commits

Group related commits together. For each group:

### [Descriptive Title for this feature/change]
**Commits**: [list commit SHAs, e.g., `abc123de`, `def456gh`]
**Repository**: [repo name]
**Relevance**: [1-2 sentences explaining why these commits are relevant to the blog topic]

Do NOT include code snippets - the full diffs and final file states will be provided to the writing stage.

If NO relevant changes are found for the topic, you MUST respond with EXACTLY this format:

## No Relevant Changes Found

[Explanation of what was searched and why nothing matched the topic]

Do not try to find tangentially related content. If the commits don't contain work directly related to the topic, use the "No Relevant Changes Found" format above.

IMPORTANT: Always include commit SHAs so they can be retrieved later. Use the format `abc123de` (8 characters)."""

# Write stage system prompt
WRITE_SYSTEM_PROMPT = """You are an expert technical writer creating a blog post.

You have been provided with:
1. A research summary identifying relevant commits and key files
2. The FINAL STATE of key implementation files - use these as your PRIMARY source for code examples
3. Commit diffs showing how the implementation evolved - use these for context on design decisions

IMPORTANT GUIDANCE:
- Write mainly about the final implementation, rather than the changes that led to it, unless they are particularly interesting or relevant to the topic.
- Code examples should come from the final file states, showing working code readers can learn from
- The diffs are for YOUR context to understand design decisions - don't focus on "we changed X to Y"
- Focus on generally useful patterns (applicable to anyone) over app-specific details
- When showing implementation-specific code, note what parts are specific to this app vs generally applicable

The blog post should:
- Have a compelling introduction explaining the problem being solved
- Present the solution as a coherent implementation, not a series of changes
- Include code examples from the final file states (not fabricated)
- Explain the "why" behind design decisions
- Note where implementation details are app-specific vs generally applicable
- Have a conclusion with key takeaways

Format the output as clean markdown suitable for publishing. Include:
- A title (# heading)
- Section headings (## or ###)
- Code blocks with language tags
- Any relevant callouts or tips

Do NOT include meta-commentary about writing the blog post - just output the blog post itself."""

# Review stage system prompt
REVIEW_SYSTEM_PROMPT = """You are an expert technical editor reviewing and refining a blog post.

You have been provided with:
1. The original topic and writing instructions
2. A draft blog post
3. User feedback to incorporate

Your task is to produce a refined version of the blog post that:
- Addresses the user's feedback directly
- Maintains the strengths of the original draft
- Stays true to the original topic and instructions
- Improves clarity, accuracy, and engagement

Guidelines:
- Make substantive improvements based on the feedback, not just cosmetic changes
- If feedback conflicts with the original instructions, prioritize the feedback (it's more recent)
- Preserve accurate technical content and code examples from the original
- If feedback asks to change factual content you're unsure about, note this in a comment

Output ONLY the refined blog post in clean markdown format. Do NOT include:
- Meta-commentary about your changes
- A summary of what you changed
- The original draft for comparison

Just output the improved blog post."""


@dataclass
class ResearchMetadata:
    """Metadata embedded in research markdown for stage 2.

    Attributes:
        topic: The original blog post idea/topic.
        description: Additional context about the blog post.
        instructions: Writing instructions (audience, tone, style).
        period: Time period that was analyzed.
        client: Client filter used (if any).
        author: Git author filter.
        root: Repository root path.
        commits: List of (sha, repo_name) tuples for referenced commits.
    """

    topic: str
    description: str
    instructions: str
    period: str
    client: str
    author: str
    root: str
    commits: list[tuple[str, str]]


def parse_research_metadata(content: str) -> Optional[ResearchMetadata]:
    """Extracts metadata from research markdown.

    Looks for YAML embedded in an HTML comment at the start of the file:
    <!-- blog-research-meta
    topic: ...
    commits:
      - sha: abc123
        repo: MyRepo
    -->

    Args:
        content: The research markdown content.

    Returns:
        ResearchMetadata if found and valid, None otherwise.
    """
    # Look for the metadata comment
    pattern = r"<!--\s*blog-research-meta\s*\n(.*?)\n-->"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return None

    try:
        import yaml
    except ImportError:
        print("Warning: PyYAML required for metadata parsing", file=sys.stderr)
        return None

    try:
        data = yaml.safe_load(match.group(1))
        if not data:
            return None

        commits = []
        if "commits" in data and data["commits"]:
            for c in data["commits"]:
                if isinstance(c, dict) and "sha" in c and "repo" in c:
                    commits.append((c["sha"], c["repo"]))

        return ResearchMetadata(
            topic=data.get("topic", ""),
            description=data.get("description", ""),
            instructions=data.get("instructions", ""),
            period=data.get("period", ""),
            client=data.get("client", ""),
            author=data.get("author", ""),
            root=data.get("root", ""),
            commits=commits,
        )
    except Exception as e:
        print(f"Warning: Failed to parse research metadata: {e}", file=sys.stderr)
        return None


def extract_commit_shas_from_research(content: str) -> list[tuple[str, str]]:
    """Extracts commit SHA references from research markdown content.

    Handles two formats:
    1. Structured sections with **Commits**: and **Repository**: lines
    2. Inline format: `abc123de` (RepoName) or just `abc123de`

    Args:
        content: The research markdown content.

    Returns:
        List of (sha_prefix, repo_name) tuples found in the content.
    """
    results: list[tuple[str, str]] = []
    seen_shas: set[str] = set()

    # First, extract from structured sections (### Title ... **Commits**: ... **Repository**: ...)
    # Split by section headers to process each section
    sections = re.split(r"(?=^###\s)", content, flags=re.MULTILINE)

    for section in sections:
        # Look for **Commits**: line with backtick-wrapped SHAs
        commits_match = re.search(r"\*\*Commits?\*\*:\s*(.+?)(?:\n|$)", section, re.IGNORECASE)
        # Look for **Repository**: line
        repo_match = re.search(r"\*\*Repository\*\*:\s*(.+?)(?:\n|$)", section, re.IGNORECASE)

        if commits_match:
            commits_line = commits_match.group(1)
            repo_name = repo_match.group(1).strip() if repo_match else ""
            # Strip backticks from repo name (LLM sometimes wraps in backticks)
            repo_name = repo_name.strip("`")

            # Extract all SHAs from the commits line
            sha_pattern = r"`([a-f0-9]{7,8})`"
            shas = re.findall(sha_pattern, commits_line)

            for sha in shas:
                if sha not in seen_shas:
                    seen_shas.add(sha)
                    results.append((sha, repo_name))

    # Also check for inline format: `abc123de` (RepoName) or `abc123de` (Repo-Name)
    inline_pattern = r"`([a-f0-9]{7,8})`\s*\(([^)]+)\)"
    inline_matches = re.findall(inline_pattern, content)

    for sha, repo in inline_matches:
        if sha not in seen_shas:
            seen_shas.add(sha)
            results.append((sha, repo.strip()))

    # Finally, extract any remaining standalone SHAs not yet captured
    all_sha_pattern = r"`([a-f0-9]{7,8})`"
    all_shas = re.findall(all_sha_pattern, content)

    for sha in all_shas:
        if sha not in seen_shas:
            seen_shas.add(sha)
            results.append((sha, ""))

    return results


def extract_key_files_from_research(content: str) -> list[tuple[str, str, str]]:
    """Extracts key file references from research markdown content.

    Looks for the "## Key Files" section and extracts file paths with repo names
    and optional commit specifiers.

    Expected formats:
    - `path/to/file.ext` (repo-name) - description
    - `path/to/file.ext` (repo-name) @ before - description
    - `path/to/file.ext` (repo-name) @ abc123 - description

    Commit specifiers:
    - No specifier or "after": last commit in period that touched the file
    - "before": state before the first commit in period
    - "<sha>": specific commit hash

    Args:
        content: The research markdown content.

    Returns:
        List of (file_path, repo_name, commit_specifier) tuples.
        commit_specifier is "after" (default), "before", or a specific SHA.
    """
    results: list[tuple[str, str, str]] = []

    # Find the Key Files section
    key_files_match = re.search(
        r"##\s*Key Files\s*\n(.*?)(?=\n##|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if not key_files_match:
        return results

    key_files_section = key_files_match.group(1)

    # Pattern: `path/to/file.ext` (repo-name) [@ specifier]
    # Matches: file path, repo name, optional @ specifier
    pattern = r"`([^`]+\.[a-zA-Z0-9]+)`\s*\(([^)]+)\)(?:\s*@\s*(\S+))?"
    matches = re.findall(pattern, key_files_section)

    for file_path, repo_name, specifier in matches:
        repo_name = repo_name.strip()
        specifier = specifier.strip().lower() if specifier else "after"
        if specifier == "":
            specifier = "after"
        results.append((file_path.strip(), repo_name, specifier))

    return results


def get_file_contents(repo_path: str, file_path: str, max_lines: int = 500) -> Optional[str]:
    """Retrieves the current contents of a file from a repository.

    Args:
        repo_path: Path to the repository.
        file_path: Path to the file within the repository.
        max_lines: Maximum lines to include (0 for unlimited).

    Returns:
        File contents if found, None otherwise.
    """
    full_path = os.path.join(repo_path, file_path)
    if not os.path.isfile(full_path):
        return None

    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            if max_lines > 0:
                lines = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(f"\n... (truncated, file continues beyond {max_lines} lines)")
                        break
                    lines.append(line)
                return "".join(lines)
            else:
                return f.read()
    except Exception:
        return None


def retrieve_key_files(
    root: str,
    key_files: list[tuple[str, str, str]],
    period: str = "",
    max_lines: int = 500,
) -> list[tuple[str, str, str, str]]:
    """Retrieves the contents of key files from repositories at specific commits.

    Args:
        root: Root directory containing repositories.
        key_files: List of (file_path, repo_name, commit_specifier) tuples.
            commit_specifier can be "after", "before", or a specific SHA.
        period: The analysis period (e.g., "2025-08" or "2025-08-01:2025-08-31").
            Used to determine date range for "before" and "after" specifiers.
        max_lines: Maximum lines per file.

    Returns:
        List of (file_path, repo_name, contents, commit_info) tuples.
        commit_info describes which version was retrieved.
    """
    results: list[tuple[str, str, str, str]] = []

    # Parse period into since/until dates
    since, until = "", ""
    if period:
        if ":" in period:
            since, until = period.split(":", 1)
        elif len(period) == 7:  # YYYY-MM format
            year, month = period.split("-")
            since = f"{year}-{month}-01"
            # Calculate last day of month
            import calendar

            _, last_day = calendar.monthrange(int(year), int(month))
            until = f"{year}-{month}-{last_day:02d}"
        else:
            since = period
            until = period

    for file_path, repo_name, specifier in key_files:
        repo_path = find_repo_by_name(root, repo_name)
        if not repo_path:
            continue

        contents = None
        commit_info = ""

        if specifier == "after" or specifier == "":
            # Get file at the last commit that touched it in the period
            if since and until:
                sha = find_last_commit_for_file(repo_path, file_path, since, until)
                if sha:
                    contents = get_file_at_commit(repo_path, file_path, sha)
                    commit_info = f"at {sha[:8]} (last commit in period)"
            # Fall back to HEAD if no commit found in period
            if not contents:
                contents = get_file_contents(repo_path, file_path, max_lines)
                commit_info = "at HEAD"

        elif specifier == "before":
            # Get file at state before the first commit in the period
            if since and until:
                first_sha = find_first_commit_for_file(repo_path, file_path, since, until)
                if first_sha:
                    parent_sha = get_parent_commit(repo_path, first_sha)
                    if parent_sha:
                        contents = get_file_at_commit(repo_path, file_path, parent_sha)
                        commit_info = f"at {parent_sha[:8]} (before first change)"
            if not contents:
                commit_info = "(file did not exist before period)"

        else:
            # Treat as specific SHA
            contents = get_file_at_commit(repo_path, file_path, specifier)
            commit_info = f"at {specifier}"

        if contents:
            # Truncate if needed
            lines = contents.split("\n")
            if max_lines > 0 and len(lines) > max_lines:
                contents = "\n".join(lines[:max_lines])
                contents += f"\n... (truncated, {len(lines) - max_lines} more lines)"

            results.append((file_path, repo_name, contents, commit_info))

    return results


def format_key_files_for_prompt(
    key_files: Union[list[tuple[str, str, str]], list[tuple[str, str, str, str]]],
) -> str:
    """Formats key file contents for inclusion in an LLM prompt.

    Args:
        key_files: List of (file_path, repo_name, contents) or
            (file_path, repo_name, contents, commit_info) tuples.

    Returns:
        Formatted string with all file contents.
    """
    if not key_files:
        return "(No key files could be retrieved)"

    lines = [f"# Key Implementation Files ({len(key_files)} files)", ""]
    lines.append("These are the FINAL versions of the key files - use these for code examples.\n")

    for item in key_files:
        # Handle both 3-tuple and 4-tuple formats
        if len(item) == 4:
            file_path, repo_name, contents, commit_info = item
        else:
            file_path, repo_name, contents = item
            commit_info = ""

        # Determine language from file extension
        ext = os.path.splitext(file_path)[1].lower()
        lang_map = {
            ".swift": "swift",
            ".kt": "kotlin",
            ".java": "java",
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".c": "c",
            ".h": "c",
            ".cpp": "cpp",
            ".m": "objc",
            ".rs": "rust",
            ".go": "go",
        }
        lang = lang_map.get(ext, "")

        header = f"## {file_path} ({repo_name})"
        if commit_info:
            header += f" {commit_info}"
        lines.append(header)
        lines.append(f"```{lang}")
        lines.append(contents.rstrip())
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def format_research_metadata(
    topic: str,
    description: str,
    instructions: str,
    period: str,
    client: str,
    author: str,
    root: str,
    commits: list[tuple[str, str]],
) -> str:
    """Formats metadata as YAML in an HTML comment.

    Args:
        topic: The blog post topic.
        description: Additional context about the blog post.
        instructions: Writing instructions (audience, tone, style).
        period: Time period analyzed.
        client: Client filter (may be empty).
        author: Git author filter.
        root: Repository root path.
        commits: List of (sha, repo_name) tuples.

    Returns:
        HTML comment containing YAML metadata.
    """
    lines = [
        "<!-- blog-research-meta",
        f"topic: {topic}",
    ]

    if description:
        # Use YAML literal block scalar for multi-line text
        if "\n" in description:
            lines.append("description: |")
            for line in description.split("\n"):
                lines.append(f"  {line}")
        else:
            lines.append(f"description: {description}")

    if instructions:
        if "\n" in instructions:
            lines.append("instructions: |")
            for line in instructions.split("\n"):
                lines.append(f"  {line}")
        else:
            lines.append(f"instructions: {instructions}")

    lines.extend(
        [
            f"period: {period}",
            f"client: {client}",
            f"author: {author}",
            f"root: {root}",
        ]
    )

    if commits:
        lines.append("commits:")
        for sha, repo in commits:
            lines.append(f"  - sha: {sha}")
            lines.append(f"    repo: {repo}")

    lines.append("-->")
    return "\n".join(lines)


def get_commit_by_sha(repo_path: str, sha: str, max_diff_lines: int = 500) -> Optional[CommitInfo]:
    """Retrieves a specific commit by SHA with its diff.

    Args:
        repo_path: Path to the repository.
        sha: Commit SHA (full or prefix).
        max_diff_lines: Maximum diff lines to include.

    Returns:
        CommitInfo if found, None otherwise.
    """
    # Get commit info
    args = [
        "show",
        "--no-color",
        "--format=%H%x1f%ad%x1f%an%x1f%s%x1f%b%x00",
        "--date=iso-local",
        sha,
    ]
    code, out, _ = run_git(repo_path, args)
    if code != 0:
        return None

    # Parse the output
    parts = out.split("\x00")[0].split("\x1f")
    if len(parts) < 4:
        return None

    full_sha = parts[0]
    author_date = parts[1]
    author_name = parts[2]
    subject = parts[3]
    body = parts[4].strip() if len(parts) > 4 else ""

    # Get the diff
    diff_args = ["show", "--no-color", "--format=", "--stat", "--patch", sha]
    diff_code, diff_out, _ = run_git(repo_path, diff_args)

    diff_content = ""
    if diff_code == 0:
        diff_lines = diff_out.split("\n")
        if len(diff_lines) > max_diff_lines:
            diff_content = "\n".join(diff_lines[:max_diff_lines])
            diff_content += f"\n... (truncated, {len(diff_lines) - max_diff_lines} more lines)"
        else:
            diff_content = diff_out

    return CommitInfo(
        sha=full_sha,
        author_date=author_date,
        author_name=author_name,
        subject=subject,
        body=body,
        diff=diff_content,
    )


def find_repo_by_name(root: str, repo_name: str) -> Optional[str]:
    """Finds a repository path by name.

    Args:
        root: Root directory containing repositories.
        repo_name: Name of the repository to find.

    Returns:
        Full path to the repository, or None if not found.
    """
    # Check direct child
    direct_path = os.path.join(root, repo_name)
    if os.path.isdir(direct_path):
        return direct_path

    # Search all repos and submodules
    for repo_path in discover_top_level_repos(root):
        if os.path.basename(repo_path).lower() == repo_name.lower():
            return repo_path
        # Check submodules
        for sub_path in discover_all_submodules(repo_path):
            if os.path.basename(sub_path).lower() == repo_name.lower():
                return sub_path

    return None


def get_file_at_commit(repo_path: str, file_path: str, sha: str) -> Optional[str]:
    """Retrieves file contents at a specific commit.

    Args:
        repo_path: Path to the repository.
        file_path: Path to the file relative to repo root.
        sha: Commit SHA to retrieve file from.

    Returns:
        File contents as string, or None if not found.
    """
    code, out, _ = run_git(repo_path, ["show", f"{sha}:{file_path}"])
    return out if code == 0 else None


def find_last_commit_for_file(
    repo_path: str,
    file_path: str,
    since: str,
    until: str,
) -> Optional[str]:
    """Find the last commit that touched a file in a date range.

    Args:
        repo_path: Path to the repository.
        file_path: Path to the file relative to repo root.
        since: Start date (ISO format or git-parseable).
        until: End date (ISO format or git-parseable).

    Returns:
        Commit SHA if found, None otherwise.
    """
    args = [
        "log",
        "--format=%H",
        "-1",
        f"--since={since}",
        f"--until={until}",
        "--",
        file_path,
    ]
    code, out, _ = run_git(repo_path, args)
    if code == 0 and out.strip():
        return out.strip()
    return None


def find_first_commit_for_file(
    repo_path: str,
    file_path: str,
    since: str,
    until: str,
) -> Optional[str]:
    """Find the first commit that touched a file in a date range.

    Args:
        repo_path: Path to the repository.
        file_path: Path to the file relative to repo root.
        since: Start date (ISO format or git-parseable).
        until: End date (ISO format or git-parseable).

    Returns:
        Commit SHA if found, None otherwise.
    """
    args = [
        "log",
        "--format=%H",
        "--reverse",
        "-1",
        f"--since={since}",
        f"--until={until}",
        "--",
        file_path,
    ]
    code, out, _ = run_git(repo_path, args)
    if code == 0 and out.strip():
        return out.strip()
    return None


def get_parent_commit(repo_path: str, sha: str) -> Optional[str]:
    """Get the parent commit of a given commit.

    Args:
        repo_path: Path to the repository.
        sha: Commit SHA.

    Returns:
        Parent commit SHA, or None if no parent (root commit).
    """
    code, out, _ = run_git(repo_path, ["rev-parse", f"{sha}^"])
    if code == 0 and out.strip():
        return out.strip()
    return None


def retrieve_referenced_commits(
    metadata: ResearchMetadata,
    additional_refs: list[tuple[str, str]],
    max_diff_lines: int = 500,
) -> list[tuple[str, CommitInfo]]:
    """Retrieves full commit info for referenced commits.

    Args:
        metadata: Research metadata containing root and commit list.
        additional_refs: Additional (sha, repo) references from content.
        max_diff_lines: Maximum diff lines per commit.

    Returns:
        List of (repo_name, CommitInfo) tuples for found commits.
    """
    results: list[tuple[str, CommitInfo]] = []
    seen_shas: set[str] = set()

    # Combine metadata commits and extracted refs
    all_refs = list(metadata.commits) + additional_refs

    root = metadata.root or os.path.dirname(os.getcwd())

    for sha_prefix, repo_name in all_refs:
        if sha_prefix in seen_shas:
            continue
        seen_shas.add(sha_prefix)

        # Find the repo
        if repo_name:
            repo_path = find_repo_by_name(root, repo_name)
            if repo_path:
                commit = get_commit_by_sha(repo_path, sha_prefix, max_diff_lines)
                if commit:
                    results.append((repo_name, commit))
                    continue

        # If no repo specified or not found, search all repos
        for repo_path in discover_top_level_repos(root):
            commit = get_commit_by_sha(repo_path, sha_prefix, max_diff_lines)
            if commit:
                results.append((os.path.basename(repo_path), commit))
                break
            # Check submodules
            for sub_path in discover_all_submodules(repo_path):
                commit = get_commit_by_sha(sub_path, sha_prefix, max_diff_lines)
                if commit:
                    results.append((os.path.basename(sub_path), commit))
                    break

    return results


def gather_commits_for_period(
    repos: list[str],
    start_date: _dt.date,
    end_date: _dt.date,
    author: str,
    max_diff_lines: int = 500,
) -> list[tuple[str, CommitInfo]]:
    """Gathers all commits with diffs for a time period.

    Args:
        repos: List of repository paths.
        start_date: Start of the period.
        end_date: End of the period.
        author: Author filter.
        max_diff_lines: Maximum diff lines per commit.

    Returns:
        List of (repo_name, CommitInfo) tuples.
    """
    since_str, until_str = date_range_to_git_args(start_date, end_date)
    all_commits: list[tuple[str, CommitInfo]] = []
    seen_shas: set[str] = set()

    for repo_path in repos:
        repo_name = os.path.basename(repo_path)

        # Get commits from main repo
        commits = get_commits_with_diffs(repo_path, since_str, until_str, author, max_diff_lines)
        for c in commits:
            if c.sha not in seen_shas:
                seen_shas.add(c.sha)
                all_commits.append((repo_name, c))

        # Get commits from submodules
        for sub_path in discover_all_submodules(repo_path):
            sub_name = os.path.basename(sub_path)
            sub_commits = get_commits_with_diffs(
                sub_path, since_str, until_str, author, max_diff_lines
            )
            for c in sub_commits:
                if c.sha not in seen_shas:
                    seen_shas.add(c.sha)
                    all_commits.append((sub_name, c))

    return all_commits


def format_commits_for_prompt(commits: list[tuple[str, CommitInfo]]) -> str:
    """Formats commits for inclusion in an LLM prompt.

    Args:
        commits: List of (repo_name, CommitInfo) tuples.

    Returns:
        Formatted string with all commits.
    """
    if not commits:
        return "No commits found in the specified period."

    lines = [f"# Git Commits ({len(commits)} total)", ""]

    # Sort by date
    sorted_commits = sorted(commits, key=lambda x: x[1].author_date)

    for repo_name, commit in sorted_commits:
        lines.append(f"## Commit: {commit.sha[:8]} ({repo_name})")
        lines.append(f"**Date**: {commit.author_date}")
        lines.append(f"**Author**: {commit.author_name}")
        lines.append(f"**Subject**: {commit.subject}")
        if commit.body:
            lines.append(f"**Body**:\n{commit.body}")
        lines.append("")
        if commit.diff:
            lines.append("**Diff**:")
            lines.append("```diff")
            lines.append(commit.diff)
            lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def run_research_stage(
    topic: str,
    repos: list[str],
    start_date: _dt.date,
    end_date: _dt.date,
    author: str,
    period_str: str,
    client: str,
    root: str,
    model: str,
    temperature: float,
    max_cost: float,
    max_diff_lines: int,
    description: str = "",
    instructions: str = "",
    global_context: str = "",
    client_context: str = "",
    dry_run: bool = False,
) -> tuple[str, CostTracker, bool]:
    """Runs the research stage of blog post generation.

    Args:
        topic: The blog post topic/idea.
        repos: List of repository paths to search.
        start_date: Start of the time period.
        end_date: End of the time period.
        author: Git author filter.
        period_str: Period string for metadata.
        client: Client name for metadata.
        root: Root directory for metadata.
        model: LLM model to use.
        temperature: LLM temperature.
        max_cost: Maximum allowed cost.
        max_diff_lines: Maximum diff lines per commit.
        description: Additional context about the blog post.
        instructions: Writing instructions for the blog post (audience, tone, style).
        global_context: Company context for prompt.
        client_context: Client-specific context for prompt.
        dry_run: If True, don't call LLM.

    Returns:
        Tuple of (research_markdown, cost_tracker, no_relevant_changes).
        no_relevant_changes is True if LLM found no relevant content for the topic.
    """
    cost_tracker = CostTracker()

    # Gather commits
    print(f"Gathering commits from {len(repos)} repositories...", file=sys.stderr)
    commits = gather_commits_for_period(repos, start_date, end_date, author, max_diff_lines)
    print(f"Found {len(commits)} commits", file=sys.stderr)

    if not commits:
        return (
            "# Research: No Commits Found\n\nNo commits were found in the specified period.",
            cost_tracker,
        )

    # Build the prompt
    commits_text = format_commits_for_prompt(commits)

    description_section = ""
    if description:
        description_section = f"\n# Description\n{description}\n"

    user_prompt = f"""# Blog Post Topic
{topic}
{description_section}
# Time Period
{start_date} to {end_date}

{commits_text}

Please analyze these commits and identify changes relevant to the blog post topic."""

    # Build system prompt with context
    system_prompt = RESEARCH_SYSTEM_PROMPT
    if global_context or client_context:
        system_prompt += "\n\n---\n\nContext:\n"
        if global_context:
            system_prompt += f"\nCompany Background:\n{global_context}\n"
        if client_context:
            system_prompt += f"\nClient Context:\n{client_context}\n"

    if dry_run:
        print(f"[DRY RUN] Would send {len(user_prompt)} chars to LLM", file=sys.stderr)
        research_content = f"*(Dry run - {len(commits)} commits would be analyzed)*"
        no_relevant_changes = False
    else:
        print("Calling LLM for research analysis...", file=sys.stderr)
        research_content = call_llm(
            model, system_prompt, user_prompt, temperature, cost_tracker, max_cost
        )
        print(f"Research complete (cost: ${cost_tracker.total_cost:.4f})", file=sys.stderr)

        # Check if LLM found no relevant changes
        no_relevant_changes = bool(
            re.search(
                r"^##\s*No Relevant Changes Found", research_content, re.MULTILINE | re.IGNORECASE
            )
        )
        if no_relevant_changes:
            print("LLM found no relevant changes for the topic.", file=sys.stderr)

    # Extract commit references from the LLM output
    extracted_refs = extract_commit_shas_from_research(research_content)

    # Build metadata
    metadata_str = format_research_metadata(
        topic=topic,
        description=description,
        instructions=instructions,
        period=period_str,
        client=client,
        author=author,
        root=root,
        commits=extracted_refs,
    )

    # Combine into final output
    output = f"# Research: {topic}\n\n{metadata_str}\n\n{research_content}"

    return output, cost_tracker, no_relevant_changes


def run_write_stage(
    research_path: str,
    model: str,
    temperature: float,
    max_cost: float,
    max_diff_lines: int,
    global_context: str = "",
    client_context: str = "",
    dry_run: bool = False,
) -> tuple[str, CostTracker]:
    """Runs the write stage of blog post generation.

    Args:
        research_path: Path to the research markdown file.
        model: LLM model to use.
        temperature: LLM temperature.
        max_cost: Maximum allowed cost.
        max_diff_lines: Maximum diff lines per commit.
        global_context: Company context for prompt.
        client_context: Client-specific context for prompt.
        dry_run: If True, don't call LLM.

    Returns:
        Tuple of (blog_post_markdown, cost_tracker).
    """
    cost_tracker = CostTracker()

    # Read research file
    print(f"Reading research from: {research_path}", file=sys.stderr)
    with open(research_path) as f:
        research_content = f.read()

    # Parse metadata
    metadata = parse_research_metadata(research_content)
    if not metadata:
        print("Warning: No metadata found in research file, using defaults", file=sys.stderr)
        metadata = ResearchMetadata(
            topic="",
            description="",
            instructions="",
            period="",
            client="",
            author="",
            root=os.path.dirname(os.getcwd()),
            commits=[],
        )

    # Extract additional commit references from content
    additional_refs = extract_commit_shas_from_research(research_content)

    # Retrieve referenced commits
    print("Retrieving referenced commits...", file=sys.stderr)
    referenced_commits = retrieve_referenced_commits(metadata, additional_refs, max_diff_lines)
    print(f"Retrieved {len(referenced_commits)} commits", file=sys.stderr)

    # Format commits for prompt
    if referenced_commits:
        commits_text = format_commits_for_prompt(referenced_commits)
    else:
        commits_text = "(No referenced commits could be retrieved)"

    # Extract and retrieve key files
    key_file_refs = extract_key_files_from_research(research_content)
    key_files_text = ""
    if key_file_refs:
        print(f"Retrieving {len(key_file_refs)} key files...", file=sys.stderr)
        root = metadata.root or os.path.dirname(os.getcwd())
        period = metadata.period if metadata else ""
        key_files = retrieve_key_files(root, key_file_refs, period=period, max_lines=500)
        print(f"Retrieved {len(key_files)} key files", file=sys.stderr)
        if key_files:
            key_files_text = format_key_files_for_prompt(key_files)

    # Build instructions section
    instructions_section = ""
    if metadata.instructions:
        instructions_section = f"""
# Writing Instructions

{metadata.instructions}
"""

    # Build key files section
    key_files_section = ""
    if key_files_text:
        key_files_section = f"""
{key_files_text}
"""

    # Build user prompt
    user_prompt = f"""# Topic

{metadata.topic}
{instructions_section}
# Research Summary

{research_content}
{key_files_section}
# Commit Diffs (for context on how the implementation evolved)

{commits_text}

Write a blog post based on this material. Use the KEY FILES above for code examples (they show the final implementation). Use the commit diffs for context on design decisions, but focus on the end result, not the changes."""

    # Build system prompt with context
    system_prompt = WRITE_SYSTEM_PROMPT
    if global_context or client_context:
        system_prompt += "\n\n---\n\nContext:\n"
        if global_context:
            system_prompt += f"\nCompany Background:\n{global_context}\n"
        if client_context:
            system_prompt += f"\nClient Context:\n{client_context}\n"

    if dry_run:
        print(f"[DRY RUN] Would send {len(user_prompt)} chars to LLM", file=sys.stderr)
        blog_content = "*(Dry run - blog post would be generated from research)*"
    else:
        print("Calling LLM to write blog post...", file=sys.stderr)
        blog_content = call_llm(
            model, system_prompt, user_prompt, temperature, cost_tracker, max_cost
        )
        print(f"Writing complete (cost: ${cost_tracker.total_cost:.4f})", file=sys.stderr)

    return blog_content, cost_tracker


def increment_version_filename(filepath: str) -> str:
    """Increments the version number in a filename.

    Transforms:
    - post.md -> post-v2.md
    - post-v2.md -> post-v3.md
    - post-v10.md -> post-v11.md

    Args:
        filepath: Path to the file.

    Returns:
        New filepath with incremented version number.
    """
    dir_name = os.path.dirname(filepath)
    base_name = os.path.basename(filepath)
    name, ext = os.path.splitext(base_name)

    # Check for existing version pattern
    version_match = re.match(r"(.+)-v(\d+)$", name)
    if version_match:
        base = version_match.group(1)
        version = int(version_match.group(2)) + 1
    else:
        base = name
        version = 2

    new_name = f"{base}-v{version}{ext}"
    return os.path.join(dir_name, new_name) if dir_name else new_name


def find_research_file(draft_path: str) -> Optional[str]:
    """Attempts to find the research file for a draft blog post.

    Looks for research.md in the same directory as the draft.

    Args:
        draft_path: Path to the draft blog post.

    Returns:
        Path to research file if found, None otherwise.
    """
    dir_name = os.path.dirname(draft_path) or "."
    research_path = os.path.join(dir_name, "research.md")
    if os.path.isfile(research_path):
        return research_path
    return None


def run_review_stage(
    draft_path: str,
    feedback: str,
    model: str,
    temperature: float,
    max_cost: float,
    research_path: Optional[str] = None,
    global_context: str = "",
    client_context: str = "",
    dry_run: bool = False,
) -> tuple[str, CostTracker]:
    """Runs the review stage to refine a blog post with feedback.

    Args:
        draft_path: Path to the draft blog post.
        feedback: User feedback to incorporate.
        model: LLM model to use.
        temperature: LLM temperature.
        max_cost: Maximum allowed cost.
        research_path: Optional path to research file for context.
        global_context: Company context for prompt.
        client_context: Client-specific context for prompt.
        dry_run: If True, don't call LLM.

    Returns:
        Tuple of (refined_blog_post, cost_tracker).
    """
    cost_tracker = CostTracker()

    # Read draft
    print(f"Reading draft from: {draft_path}", file=sys.stderr)
    with open(draft_path) as f:
        draft_content = f.read()

    # Try to find and read research file for context
    topic = ""
    instructions = ""
    if not research_path:
        research_path = find_research_file(draft_path)

    if research_path and os.path.isfile(research_path):
        print(f"Reading research context from: {research_path}", file=sys.stderr)
        with open(research_path) as f:
            research_content = f.read()
        metadata = parse_research_metadata(research_content)
        if metadata:
            topic = metadata.topic
            instructions = metadata.instructions

    # Build context section
    context_section = ""
    if topic:
        context_section += f"# Original Topic\n\n{topic}\n\n"
    if instructions:
        context_section += f"# Original Writing Instructions\n\n{instructions}\n\n"

    # Build user prompt
    user_prompt = f"""{context_section}# Draft Blog Post

{draft_content}

# Feedback to Incorporate

{feedback}

Please produce a refined version of the blog post that addresses this feedback."""

    # Build system prompt with context
    system_prompt = REVIEW_SYSTEM_PROMPT
    if global_context or client_context:
        system_prompt += "\n\n---\n\nContext:\n"
        if global_context:
            system_prompt += f"\nCompany Background:\n{global_context}\n"
        if client_context:
            system_prompt += f"\nClient Context:\n{client_context}\n"

    if dry_run:
        print(f"[DRY RUN] Would send {len(user_prompt)} chars to LLM", file=sys.stderr)
        refined_content = "*(Dry run - blog post would be refined with feedback)*"
    else:
        print("Calling LLM to review and refine blog post...", file=sys.stderr)
        refined_content = call_llm(
            model, system_prompt, user_prompt, temperature, cost_tracker, max_cost
        )
        print(f"Review complete (cost: ${cost_tracker.total_cost:.4f})", file=sys.stderr)

    return refined_content, cost_tracker


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Adds common arguments to a subparser.

    Args:
        parser: The argument parser to add arguments to.
    """
    add_model_args(parser, DEFAULT_MODEL)
    # Override temperature default for blog posts
    parser.set_defaults(temperature=0.7)
    parser.add_argument(
        "--max-cost",
        type=float,
        default=1.00,
        help="Maximum allowed cost in USD (default: 1.00).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without calling LLM.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write output to stdout instead of file.",
    )
    add_config_arg(parser)


def add_research_args(parser: argparse.ArgumentParser) -> None:
    """Adds research-specific arguments to a subparser.

    Args:
        parser: The argument parser to add arguments to.
    """
    parser.add_argument(
        "topic",
        help="Blog post topic/idea. Use '-' to read from stdin.",
    )
    parser.add_argument(
        "-d",
        "--description",
        metavar="TEXT",
        help="Additional context or description for the blog post topic.",
    )
    parser.add_argument(
        "-i",
        "--instructions",
        metavar="TEXT",
        help="Writing instructions (e.g., 'write for a non-technical audience').",
    )
    parser.add_argument(
        "--period",
        required=True,
        help="Time period to analyze (YYYY, YYYY-QN, YYYY-MM, YYYY-WNN).",
    )
    add_author_arg(parser)
    parser.add_argument(
        "--client",
        metavar="NAME",
        help="Filter repositories by client name.",
    )
    add_root_arg(parser)
    add_filter_arg(parser)
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=500,
        help="Maximum diff lines per commit (default: 500).",
    )
    add_fetch_arg(parser)


def cmd_research(args: argparse.Namespace) -> int:
    """Handles the 'research' subcommand.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    resolve_author(args)

    # Read topic from stdin if '-'
    if args.topic == "-":
        topic = sys.stdin.read().strip()
        if not topic:
            print("Error: No topic provided on stdin", file=sys.stderr)
            return 1
    else:
        topic = args.topic

    root = os.path.abspath(args.root)

    # Load config and API keys
    config_file = get_config_path(args.config)
    load_api_keys_from_config(config_file)
    client_config, _, _, _ = load_config(str(config_file))

    global_context = ""
    client_context = ""
    if client_config:
        global_context = client_config.global_context
        if args.client:
            client_context = client_config.get_client_context(args.client)

    # Discover repositories
    all_repos = discover_top_level_repos(root)
    if not all_repos:
        print(f"No git repositories found under: {root}", file=sys.stderr)
        return 1

    # Filter by client if specified
    repos = all_repos
    if args.client and client_config:
        categorized = client_config.categorize_repos(all_repos)
        repos = categorized.get(args.client, [])
        if not repos:
            print(f"No repositories found for client: {args.client}", file=sys.stderr)
            return 1
        print(f"Filtered to {len(repos)} repositories for client: {args.client}", file=sys.stderr)

    # Additional filter patterns
    if args.filter:
        import fnmatch

        filtered = []
        for repo in repos:
            repo_name = os.path.basename(repo)
            for pattern in args.filter:
                if (
                    fnmatch.fnmatch(repo_name.lower(), pattern.lower())
                    or pattern.lower() in repo_name.lower()
                ):
                    filtered.append(repo)
                    break
        repos = filtered
        if not repos:
            print("No repositories matched filter(s)", file=sys.stderr)
            return 1

    # Parse period
    label, start_date, end_date = parse_period(args.period)

    # Fetch repos if requested
    if args.fetch:
        fetch_repos_with_progress(repos, include_submodules=True, output=sys.stderr)

    # Run research stage
    output, cost_tracker, no_relevant_changes = run_research_stage(
        topic=topic,
        repos=repos,
        start_date=start_date,
        end_date=end_date,
        author=args.author,
        period_str=args.period,
        client=args.client or "",
        root=root,
        model=args.model,
        temperature=args.temperature,
        max_cost=args.max_cost,
        max_diff_lines=args.max_diff_lines,
        description=args.description or "",
        instructions=args.instructions or "",
        global_context=global_context,
        client_context=client_context,
        dry_run=args.dry_run,
    )

    print(f"Cost: {cost_tracker.summary()}", file=sys.stderr)

    # Fail if no relevant changes found
    if no_relevant_changes:
        print(f"Error: No relevant changes found for topic: {topic}", file=sys.stderr)
        return 1

    # Write output
    if args.stdout:
        print(output)
    elif args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Research written to: {args.output}", file=sys.stderr)
    else:
        # Default output path
        from code_recap.paths import get_output_dir

        slug = re.sub(r"[^\w\-]", "-", topic.lower())[:50]
        output_dir_path = get_output_dir(subdir=f"blog/{slug}")
        os.makedirs(output_dir_path, exist_ok=True)
        output_path = os.path.join(str(output_dir_path), "research.md")
        with open(output_path, "w") as f:
            f.write(output)
        print(f"Research written to: {output_path}", file=sys.stderr)

    return 0


def cmd_write(args: argparse.Namespace) -> int:
    """Handles the 'write' subcommand.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    research_path = args.research_file

    if not os.path.isfile(research_path):
        print(f"Error: Research file not found: {research_path}", file=sys.stderr)
        return 1

    # Load config and API keys
    config_file = get_config_path(args.config)
    load_api_keys_from_config(config_file)
    client_config, _, _, _ = load_config(str(config_file))

    # Try to get context from metadata
    with open(research_path) as f:
        content = f.read()
    metadata = parse_research_metadata(content)

    global_context = ""
    client_context = ""
    if client_config:
        global_context = client_config.global_context
        if metadata and metadata.client:
            client_context = client_config.get_client_context(metadata.client)

    # Run write stage
    output, cost_tracker = run_write_stage(
        research_path=research_path,
        model=args.model,
        temperature=args.temperature,
        max_cost=args.max_cost,
        max_diff_lines=args.max_diff_lines,
        global_context=global_context,
        client_context=client_context,
        dry_run=args.dry_run,
    )

    # Write output
    if args.stdout:
        print(output)
    elif args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Blog post written to: {args.output}", file=sys.stderr)
    else:
        # Default: same directory as research file
        research_dir = os.path.dirname(research_path) or "."
        output_path = os.path.join(research_dir, "post.md")
        with open(output_path, "w") as f:
            f.write(output)
        print(f"Blog post written to: {output_path}", file=sys.stderr)

    print(f"Cost: {cost_tracker.summary()}", file=sys.stderr)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Handles the 'review' subcommand.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    draft_path = args.draft_file

    if not os.path.isfile(draft_path):
        print(f"Error: Draft file not found: {draft_path}", file=sys.stderr)
        return 1

    # Get feedback from flag, file, or stdin
    feedback = ""
    if args.feedback:
        feedback = args.feedback
    elif args.feedback_file:
        if not os.path.isfile(args.feedback_file):
            print(f"Error: Feedback file not found: {args.feedback_file}", file=sys.stderr)
            return 1
        with open(args.feedback_file) as f:
            feedback = f.read().strip()
    else:
        # Check if stdin has data (non-blocking check)
        import select

        if select.select([sys.stdin], [], [], 0.0)[0]:
            feedback = sys.stdin.read().strip()

    if not feedback:
        print(
            "Error: No feedback provided. Use --feedback, --feedback-file, or pipe to stdin.",
            file=sys.stderr,
        )
        return 1

    # Load config and API keys
    config_file = get_config_path(args.config)
    load_api_keys_from_config(config_file)
    client_config, _, _, _ = load_config(str(config_file))

    global_context = ""
    client_context = ""
    if client_config:
        global_context = client_config.global_context

    # Determine research file path
    research_path = args.research if hasattr(args, "research") and args.research else None

    # Run review stage
    output, cost_tracker = run_review_stage(
        draft_path=draft_path,
        feedback=feedback,
        model=args.model,
        temperature=args.temperature,
        max_cost=args.max_cost,
        research_path=research_path,
        global_context=global_context,
        client_context=client_context,
        dry_run=args.dry_run,
    )

    # Determine output path
    if args.stdout:
        print(output)
    elif args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Refined post written to: {args.output}", file=sys.stderr)
    else:
        # Auto-increment version
        output_path = increment_version_filename(draft_path)
        with open(output_path, "w") as f:
            f.write(output)
        print(f"Refined post written to: {output_path}", file=sys.stderr)

    print(f"Cost: {cost_tracker.summary()}", file=sys.stderr)
    return 0


def cmd_full(args: argparse.Namespace) -> int:
    """Handles the 'full' subcommand (research + write).

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    resolve_author(args)

    # Read topic from stdin if '-'
    if args.topic == "-":
        topic = sys.stdin.read().strip()
        if not topic:
            print("Error: No topic provided on stdin", file=sys.stderr)
            return 1
    else:
        topic = args.topic

    root = os.path.abspath(args.root)

    # Load config and API keys
    config_file = get_config_path(args.config)
    load_api_keys_from_config(config_file)
    client_config, _, _, _ = load_config(str(config_file))

    global_context = ""
    client_context = ""
    if client_config:
        global_context = client_config.global_context
        if args.client:
            client_context = client_config.get_client_context(args.client)

    # Discover repositories
    all_repos = discover_top_level_repos(root)
    if not all_repos:
        print(f"No git repositories found under: {root}", file=sys.stderr)
        return 1

    # Filter by client if specified
    repos = all_repos
    if args.client and client_config:
        categorized = client_config.categorize_repos(all_repos)
        repos = categorized.get(args.client, [])
        if not repos:
            print(f"No repositories found for client: {args.client}", file=sys.stderr)
            return 1

    # Additional filter patterns
    if args.filter:
        import fnmatch

        filtered = []
        for repo in repos:
            repo_name = os.path.basename(repo)
            for pattern in args.filter:
                if (
                    fnmatch.fnmatch(repo_name.lower(), pattern.lower())
                    or pattern.lower() in repo_name.lower()
                ):
                    filtered.append(repo)
                    break
        repos = filtered
        if not repos:
            print("No repositories matched filter(s)", file=sys.stderr)
            return 1

    # Parse period
    label, start_date, end_date = parse_period(args.period)

    # Fetch repos if requested
    if args.fetch:
        fetch_repos_with_progress(repos, include_submodules=True, output=sys.stderr)

    # Determine output paths
    if args.output:
        if os.path.isdir(args.output) or args.output.endswith("/"):
            output_dir = args.output
        else:
            output_dir = os.path.dirname(args.output) or "."
    else:
        from code_recap.paths import get_output_dir

        slug = re.sub(r"[^\w\-]", "-", topic.lower())[:50]
        output_dir = str(get_output_dir(subdir=f"blog/{slug}"))

    os.makedirs(output_dir, exist_ok=True)
    research_path = os.path.join(output_dir, "research.md")
    post_path = os.path.join(output_dir, "post.md")

    total_cost = CostTracker()

    # Stage 1: Research
    print_heading("Stage 1: Research")

    research_output, research_cost, no_relevant_changes = run_research_stage(
        topic=topic,
        repos=repos,
        start_date=start_date,
        end_date=end_date,
        author=args.author,
        period_str=args.period,
        client=args.client or "",
        root=root,
        model=args.model,
        temperature=args.temperature,
        max_cost=args.max_cost,
        max_diff_lines=args.max_diff_lines,
        description=args.description or "",
        instructions=args.instructions or "",
        global_context=global_context,
        client_context=client_context,
        dry_run=args.dry_run,
    )

    total_cost.total_input_tokens += research_cost.total_input_tokens
    total_cost.total_output_tokens += research_cost.total_output_tokens
    total_cost.total_cost += research_cost.total_cost
    total_cost.calls += research_cost.calls

    # Fail if no relevant changes found
    if no_relevant_changes:
        print(f"\nTotal cost: {total_cost.summary()}", file=sys.stderr)
        print(f"Error: No relevant changes found for topic: {topic}", file=sys.stderr)
        return 1

    # Save research
    with open(research_path, "w") as f:
        f.write(research_output)
    print(f"Research written to: {research_path}", file=sys.stderr)

    # Stage 2: Write
    print("", file=sys.stderr)
    print_heading("Stage 2: Write")

    blog_output, write_cost = run_write_stage(
        research_path=research_path,
        model=args.model,
        temperature=args.temperature,
        max_cost=args.max_cost - total_cost.total_cost,  # Remaining budget
        max_diff_lines=args.max_diff_lines,
        global_context=global_context,
        client_context=client_context,
        dry_run=args.dry_run,
    )

    total_cost.total_input_tokens += write_cost.total_input_tokens
    total_cost.total_output_tokens += write_cost.total_output_tokens
    total_cost.total_cost += write_cost.total_cost
    total_cost.calls += write_cost.calls

    # Save blog post
    if args.stdout:
        print(blog_output)
    else:
        with open(post_path, "w") as f:
            f.write(blog_output)
        print(f"Blog post written to: {post_path}", file=sys.stderr)

    print("", file=sys.stderr)
    print(f"Total cost: {total_cost.summary()}", file=sys.stderr)
    return 0


def cmd_list_models(args: argparse.Namespace) -> int:
    """Handles the 'list-models' subcommand.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    try:
        import litellm
    except ImportError:
        print("litellm not installed. Install with: pip install litellm", file=sys.stderr)
        return 1

    provider = args.provider.lower() if args.provider else None

    if provider and provider not in RECOMMENDED_MODELS:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        print(f"Available: {', '.join(RECOMMENDED_MODELS.keys())}", file=sys.stderr)
        return 1

    providers_to_show = [provider] if provider else list(RECOMMENDED_MODELS.keys())

    print("Recommended models:\n")
    for prov in providers_to_show:
        print(f"=== {prov.upper()} ===")
        for model, description in RECOMMENDED_MODELS[prov]:
            cost_info = ""
            try:
                if model in litellm.model_cost:
                    cost = litellm.model_cost[model]
                    input_cost = cost.get("input_cost_per_token", 0) * 1_000_000
                    output_cost = cost.get("output_cost_per_token", 0) * 1_000_000
                    cost_info = f" [${input_cost:.2f}/${output_cost:.2f} per 1M]"
            except Exception:
                pass
            print(f"  {model:<42} {description}{cost_info}")
        print()

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the CLI tool.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Generate blog posts from git activity using a two-stage LLM pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Stage 1: Research (uses git config user.name by default)
  %(prog)s research "Building a Real-Time LED Controller" --period 2025-09

  # With description for research context
  %(prog)s research "AccessorySetupKit Integration" --period 2025-08 \\
    -d "Focus on how we implemented Apple's AccessorySetupKit for seamless BLE pairing"

  # With writing instructions
  %(prog)s research "AccessorySetupKit Integration" --period 2025-08 \\
    -i "Write for a non-technical audience, focus on user benefits"

  # Stage 2: Write (after reviewing/editing research)
  %(prog)s write output/blog/building-a-real-time-led-controller/research.md

  # Combined: Run both stages
  %(prog)s full "Building a Real-Time LED Controller" --period 2025-09 \\
    -i "Technical deep-dive for iOS developers"
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Research subcommand
    research_parser = subparsers.add_parser(
        "research",
        help="Stage 1: Analyze git activity and create research summary",
        description="Gather commits for a period and identify changes relevant to the topic.",
    )
    add_research_args(research_parser)
    add_common_args(research_parser)

    # Write subcommand
    write_parser = subparsers.add_parser(
        "write",
        help="Stage 2: Generate blog post from research summary",
        description="Transform research summary into a polished blog post.",
    )
    write_parser.add_argument(
        "research_file",
        help="Path to research markdown file from stage 1.",
    )
    write_parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=500,
        help="Maximum diff lines per commit (default: 500).",
    )
    add_common_args(write_parser)

    # Full subcommand
    full_parser = subparsers.add_parser(
        "full",
        help="Run both stages sequentially",
        description="Research and write in one command.",
    )
    add_research_args(full_parser)
    add_common_args(full_parser)

    # Review subcommand
    review_parser = subparsers.add_parser(
        "review",
        help="Refine a blog post draft with feedback",
        description="Critique and refine a draft blog post based on user feedback.",
    )
    review_parser.add_argument(
        "draft_file",
        help="Path to the draft blog post to refine.",
    )
    review_parser.add_argument(
        "--feedback",
        "-f",
        help="Feedback to incorporate into the refined post.",
    )
    review_parser.add_argument(
        "--feedback-file",
        help="Path to a file containing feedback.",
    )
    review_parser.add_argument(
        "--research",
        help="Path to research file for context (auto-detected if not specified).",
    )
    add_common_args(review_parser)

    # List models subcommand
    models_parser = subparsers.add_parser(
        "list-models",
        help="List available LLM models",
    )
    models_parser.add_argument(
        "provider",
        nargs="?",
        help="Filter by provider (openai, anthropic, gemini).",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "research":
        return cmd_research(args)
    elif args.command == "write":
        return cmd_write(args)
    elif args.command == "full":
        return cmd_full(args)
    elif args.command == "review":
        return cmd_review(args)
    elif args.command == "list-models":
        return cmd_list_models(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
