#!/usr/bin/env python3
"""Shared git utilities for repository discovery and command execution.

This module provides common functionality for working with git repositories,
including running git commands, detecting repositories, discovering
submodules, and fetching updates.
"""

import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

from code_recap.paths import get_default_scan_root


def run_git(repo_path: str, args: Sequence[str]) -> tuple[int, str, str]:
    """Runs a git command in the provided repository path.

    Args:
        repo_path: Absolute path to the git repository directory to use as CWD.
        args: Sequence of git arguments (without the leading "git").

    Returns:
        A tuple of (return_code, stdout_text, stderr_text).
    """
    proc = subprocess.Popen(
        ["git", *args],
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out_bytes, err_bytes = proc.communicate()
    # Decode with 'replace' to handle non-UTF-8 bytes (e.g., in binary diffs)
    out = out_bytes.decode("utf-8", errors="replace")
    err = err_bytes.decode("utf-8", errors="replace")
    return proc.returncode, out, err


def get_git_config_author() -> Optional[str]:
    """Gets the default author name from git config.

    Returns:
        The user.name from git config, or None if not set.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def is_git_repo(path: str) -> bool:
    """Determines whether the directory at `path` is a git repository.

    Args:
        path: Absolute path to the directory to check.

    Returns:
        True if the directory is a git repository, otherwise False.
    """
    if not os.path.isdir(path):
        return False
    # Quick check: .git directory or file exists
    if os.path.exists(os.path.join(path, ".git")):
        return True
    # Fallback to git command
    code, _, _ = run_git(path, ["rev-parse", "--is-inside-work-tree"])
    return code == 0


DEFAULT_ARCHIVE_DIR = "archive"


def discover_top_level_repos(
    root: str,
    include_archived: bool = False,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
) -> list[str]:
    """Finds immediate child directories of `root` that are git repositories.

    Args:
        root: Absolute path to the root directory containing project folders.
        include_archived: If True, include repos in the archive directory.
        archive_dir: Name of the archive directory to skip (default: "archive").

    Returns:
        List of absolute paths to git repositories within `root`.

    Raises:
        SystemExit: If the root directory does not exist.
    """
    repos: list[str] = []
    try:
        for entry in sorted(os.listdir(root)):
            if entry.startswith("."):
                continue
            # Skip archive directory unless explicitly requested
            if not include_archived and entry == archive_dir:
                continue
            full = os.path.join(root, entry)
            if os.path.isdir(full) and is_git_repo(full):
                repos.append(full)
    except FileNotFoundError as exc:
        raise SystemExit(f"Root directory not found: {root}") from exc
    return repos


def discover_submodules(repo_path: str) -> list[str]:
    """Discovers submodule directories inside a git repository if present.

    Args:
        repo_path: Absolute path to the root of the git repository.

    Returns:
        List of absolute paths to submodule directories. If no submodules are
        present, returns an empty list.
    """
    gitmodules_path = os.path.join(repo_path, ".gitmodules")
    if not os.path.isfile(gitmodules_path):
        return []

    # Use git config to parse .gitmodules reliably
    code, out, _ = run_git(
        repo_path,
        ["config", "-f", ".gitmodules", "--get-regexp", "path"],
    )
    if code != 0:
        return []
    submodule_paths: list[str] = []
    for line in out.splitlines():
        # Lines look like: submodule."path/to/sub".path path/to/sub
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        path_rel = parts[1].strip()
        full_path = os.path.join(repo_path, path_rel)
        if os.path.isdir(full_path) and is_git_repo(full_path):
            submodule_paths.append(full_path)
    return submodule_paths


def discover_all_submodules(repo_path: str) -> list[str]:
    """Finds all submodules recursively within a repository.

    Args:
        repo_path: Absolute path to the root of the git repository.

    Returns:
        List of absolute paths to all submodules found recursively.
    """
    seen: set[str] = set()
    all_subs: list[str] = []
    frontier: list[str] = discover_submodules(repo_path)
    while frontier:
        next_frontier: list[str] = []
        for sub_path in frontier:
            if sub_path in seen:
                continue
            seen.add(sub_path)
            all_subs.append(sub_path)
            nested = discover_submodules(sub_path)
            for n in nested:
                if n not in seen:
                    next_frontier.append(n)
        frontier = next_frontier
    return sorted(all_subs)


def fetch_repo(repo_path: str) -> tuple[bool, Optional[str]]:
    """Fetches all remotes for a repository.

    Args:
        repo_path: Absolute path to the repository.

    Returns:
        A tuple of (success, error_message). Success is True if fetch succeeded
        or no remotes exist. If fetch failed, error_message contains the reason.
    """
    # Check if repo has any remotes
    code, out, _ = run_git(repo_path, ["remote"])
    if code != 0 or not out.strip():
        # No remotes - nothing to fetch, that's okay
        return True, None

    code, _, err = run_git(repo_path, ["fetch", "--all", "--quiet"])
    if code != 0:
        return False, err.strip()
    return True, None


@dataclass
class FetchResult:
    """Result of a fetch operation.

    Attributes:
        total: Total number of repositories processed.
        succeeded: Number of successful fetches.
        failed: Number of failed fetches.
        failures: List of (path, error_message) for failed fetches.
    """

    total: int
    succeeded: int
    failed: int
    failures: list[tuple[str, str]]


def fetch_all_repos(
    repos: list[str],
    include_submodules: bool = True,
    max_workers: int = 8,
    error_on_failure: bool = False,
    progress_callback: Optional[Callable[[int, int, str, bool, Optional[str]], None]] = None,
) -> tuple[FetchResult, bool]:
    """Fetches all repositories and optionally their submodules in parallel.

    Args:
        repos: List of repository paths to fetch.
        include_submodules: Whether to also fetch submodules recursively.
        max_workers: Maximum number of parallel fetch operations.
        error_on_failure: If True, abort on first failure and return False.
        progress_callback: Optional callback for progress updates. Called with
            (completed, total, repo_path, success, error_message).

    Returns:
        A tuple of (FetchResult, success). If error_on_failure is True and any
        fetch fails, success will be False.
    """
    # Build list of unique repos to fetch
    repos_to_fetch: list[str] = []
    seen_paths: set[str] = set()

    for repo_path in repos:
        real_path = os.path.realpath(repo_path)
        if real_path not in seen_paths:
            repos_to_fetch.append(repo_path)
            seen_paths.add(real_path)

        if include_submodules:
            for sub_path in discover_all_submodules(repo_path):
                sub_real = os.path.realpath(sub_path)
                if sub_real not in seen_paths:
                    repos_to_fetch.append(sub_path)
                    seen_paths.add(sub_real)

    if not repos_to_fetch:
        return FetchResult(0, 0, 0, []), True

    failures: list[tuple[str, str]] = []
    completed_count = 0
    aborted = False

    def fetch_with_result(path: str) -> tuple[str, bool, Optional[str]]:
        """Fetch a repo and return (path, success, error)."""
        success, err = fetch_repo(path)
        return path, success, err

    # Use thread pool for parallel fetching
    actual_workers = min(max_workers, len(repos_to_fetch))
    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = {executor.submit(fetch_with_result, path): path for path in repos_to_fetch}

        for future in as_completed(futures):
            path, success, err = future.result()
            completed_count += 1

            if not success:
                error_msg = err or "Unknown error"
                failures.append((path, error_msg))

                if progress_callback:
                    progress_callback(completed_count, len(repos_to_fetch), path, False, error_msg)

                if error_on_failure:
                    # Cancel remaining futures
                    for f in futures:
                        f.cancel()
                    aborted = True
                    break
            else:
                if progress_callback:
                    progress_callback(completed_count, len(repos_to_fetch), path, True, None)

    result = FetchResult(
        total=len(repos_to_fetch),
        succeeded=len(repos_to_fetch) - len(failures),
        failed=len(failures),
        failures=failures,
    )

    return result, not aborted or not error_on_failure


def fetch_repos_with_progress(
    repos: list[str],
    include_submodules: bool = True,
    max_workers: int = 8,
    error_on_failure: bool = False,
    output=None,
) -> tuple[FetchResult, bool]:
    """Fetches repositories with progress output to stderr.

    This is a convenience wrapper around fetch_all_repos that prints progress
    to the specified output (default: stderr).

    Args:
        repos: List of repository paths to fetch.
        include_submodules: Whether to also fetch submodules recursively.
        max_workers: Maximum number of parallel fetch operations.
        error_on_failure: If True, abort on first failure and return False.
        output: File-like object for progress output (default: sys.stderr).

    Returns:
        A tuple of (FetchResult, success).
    """
    if output is None:
        output = sys.stderr

    print("Discovering repositories to fetch...", file=output)

    # Count repos first for progress display
    repos_to_fetch: list[str] = []
    seen_paths: set[str] = set()

    for repo_path in repos:
        real_path = os.path.realpath(repo_path)
        if real_path not in seen_paths:
            repos_to_fetch.append(repo_path)
            seen_paths.add(real_path)

        if include_submodules:
            for sub_path in discover_all_submodules(repo_path):
                sub_real = os.path.realpath(sub_path)
                if sub_real not in seen_paths:
                    repos_to_fetch.append(sub_path)
                    seen_paths.add(sub_real)

    print(f"Fetching {len(repos_to_fetch)} repositories in parallel...", file=output)

    # Lock for thread-safe progress output
    print_lock = threading.Lock()
    # Track if we have an in-progress line that needs clearing
    has_progress_line = False

    def progress_callback(
        completed: int, total: int, path: str, success: bool, error: Optional[str]
    ) -> None:
        nonlocal has_progress_line
        name = os.path.basename(path)
        with print_lock:
            if success:
                # Overwrite the current line with progress
                msg = f"  [{completed}/{total}] Fetching... {name}"
                # Clear line and write progress (no newline)
                output.write(f"\r{msg:<60}")
                output.flush()
                has_progress_line = True
            else:
                # For failures, move to new line first if needed, then print
                if has_progress_line:
                    output.write("\n")
                    has_progress_line = False
                print(f"  [{completed}/{total}] FAILED: {name}: {error}", file=output)

    result, success = fetch_all_repos(
        repos,
        include_submodules=include_submodules,
        max_workers=max_workers,
        error_on_failure=error_on_failure,
        progress_callback=progress_callback,
    )

    # Clear progress line before printing summary
    if has_progress_line:
        output.write("\r" + " " * 60 + "\r")
        output.flush()

    print(f"Fetch complete: {result.succeeded}/{result.total} succeeded.", file=output)

    if error_on_failure and not success:
        print("Aborting due to fetch failure.", file=output)

    return result, success


def get_last_commit_date(repo_path: str) -> Optional[str]:
    """Gets the date of the most recent commit in a repository.

    Args:
        repo_path: Absolute path to the repository.

    Returns:
        ISO date string (YYYY-MM-DD) of the last commit, or None if no commits
        or error.
    """
    # Get the most recent commit date across all branches
    code, out, _ = run_git(repo_path, ["log", "--all", "-1", "--format=%ad", "--date=short"])
    if code != 0 or not out.strip():
        return None
    return out.strip()


def get_last_modified_date(dir_path: str) -> Optional[str]:
    """Gets the most recent file modification date in a directory tree.

    Args:
        dir_path: Absolute path to the directory.

    Returns:
        ISO date string (YYYY-MM-DD) of the most recently modified file,
        or None if directory is empty or error.
    """
    import datetime

    latest_mtime: Optional[float] = None

    try:
        for dirpath, dirnames, filenames in os.walk(dir_path):
            # Skip hidden directories
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            for filename in filenames:
                if filename.startswith("."):
                    continue
                filepath = os.path.join(dirpath, filename)
                try:
                    mtime = os.path.getmtime(filepath)
                    if latest_mtime is None or mtime > latest_mtime:
                        latest_mtime = mtime
                except OSError:
                    continue
    except OSError:
        return None

    if latest_mtime is None:
        return None

    return datetime.date.fromtimestamp(latest_mtime).isoformat()


def discover_top_level_directories(
    root: str,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
) -> list[tuple[str, bool]]:
    """Finds all immediate child directories of `root`.

    Args:
        root: Absolute path to the root directory.
        archive_dir: Name of the archive directory to skip.

    Returns:
        List of tuples (path, is_git_repo) for each directory.

    Raises:
        SystemExit: If the root directory does not exist.
    """
    dirs: list[tuple[str, bool]] = []
    try:
        for entry in sorted(os.listdir(root)):
            if entry.startswith("."):
                continue
            if entry == archive_dir:
                continue
            full = os.path.join(root, entry)
            if os.path.isdir(full):
                dirs.append((full, is_git_repo(full)))
    except FileNotFoundError as exc:
        raise SystemExit(f"Root directory not found: {root}") from exc
    return dirs


@dataclass
class ArchiveResult:
    """Result of an archive operation.

    Attributes:
        archived: List of (repo_path, new_path, last_commit_date) for archived repos.
        skipped: List of (repo_path, last_commit_date) for repos that had recent activity.
        errors: List of (repo_path, error_message) for repos that couldn't be processed.
    """

    archived: list[tuple[str, str, Optional[str]]]
    skipped: list[tuple[str, Optional[str]]]
    errors: list[tuple[str, str]]


def archive_inactive_repos(
    root: str,
    inactive_days: int = 365,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
    dry_run: bool = True,
    include_non_git: bool = False,
    output=None,
) -> ArchiveResult:
    """Archives repositories/directories with no recent activity.

    Moves inactive top-level directories into an archive subdirectory.
    For git repos, activity is determined by commit date. For non-git directories
    (when include_non_git=True), activity is determined by file modification date.

    Args:
        root: Absolute path to the root directory containing project folders.
        inactive_days: Number of days without activity to consider inactive.
        archive_dir: Name of the archive directory (created if needed).
        dry_run: If True, only report what would be done without moving anything.
        include_non_git: If True, also check non-git directories using file
            modification dates.
        output: File-like object for progress output (default: sys.stderr).

    Returns:
        ArchiveResult with lists of archived, skipped, and errored repos.
    """
    import datetime
    import shutil

    if output is None:
        output = sys.stderr

    cutoff_date = datetime.date.today() - datetime.timedelta(days=inactive_days)
    cutoff_str = cutoff_date.isoformat()

    archive_path = os.path.join(root, archive_dir)
    result = ArchiveResult(archived=[], skipped=[], errors=[])

    # Get directories to check
    if include_non_git:
        dirs = discover_top_level_directories(root, archive_dir=archive_dir)
        dir_type_label = "directories"
    else:
        dirs = [
            (p, True)
            for p in discover_top_level_repos(root, include_archived=False, archive_dir=archive_dir)
        ]
        dir_type_label = "repositories"

    print(f"Checking {len(dirs)} {dir_type_label} for inactivity...", file=output)
    print(f"Cutoff date: {cutoff_str} ({inactive_days} days ago)", file=output)
    if dry_run:
        print("DRY RUN - no files will be moved", file=output)
    print("", file=output)

    for dir_path, is_git in dirs:
        dir_name = os.path.basename(dir_path)

        # Get last activity date
        if is_git:
            last_activity = get_last_commit_date(dir_path)
            activity_type = "commit"
        else:
            last_activity = get_last_modified_date(dir_path)
            activity_type = "modified"

        if last_activity is None:
            # Can't determine activity date
            result.errors.append((dir_path, f"Could not determine last {activity_type} date"))
            type_label = "git" if is_git else "non-git"
            print(
                f"  ERROR: {dir_name} [{type_label}] - could not determine last {activity_type} date",
                file=output,
            )
            continue

        type_label = "" if is_git else " [non-git]"

        if last_activity >= cutoff_str:
            # Directory has recent activity
            result.skipped.append((dir_path, last_activity))
            print(
                f"  SKIP: {dir_name}{type_label} (last {activity_type}: {last_activity})",
                file=output,
            )
            continue

        # Directory is inactive - archive it
        new_path = os.path.join(archive_path, dir_name)

        if dry_run:
            result.archived.append((dir_path, new_path, last_activity))
            print(
                f"  ARCHIVE: {dir_name}{type_label} (last {activity_type}: {last_activity}) -> {archive_dir}/",
                file=output,
            )
        else:
            try:
                # Create archive directory if needed
                os.makedirs(archive_path, exist_ok=True)

                # Check if destination already exists
                if os.path.exists(new_path):
                    result.errors.append((dir_path, f"Destination already exists: {new_path}"))
                    print(f"  ERROR: {dir_name} - destination already exists", file=output)
                    continue

                # Move the directory
                shutil.move(dir_path, new_path)
                result.archived.append((dir_path, new_path, last_activity))
                print(
                    f"  ARCHIVED: {dir_name}{type_label} (last {activity_type}: {last_activity})",
                    file=output,
                )

            except OSError as e:
                result.errors.append((dir_path, str(e)))
                print(f"  ERROR: {dir_name} - {e}", file=output)

    print("", file=output)
    print(
        f"Summary: {len(result.archived)} archived, {len(result.skipped)} skipped, {len(result.errors)} errors",
        file=output,
    )

    return result


def list_archived_repos(
    root: str,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
) -> list[tuple[str, Optional[str]]]:
    """Lists repositories in the archive directory.

    Args:
        root: Absolute path to the root directory.
        archive_dir: Name of the archive directory.

    Returns:
        List of (repo_path, last_commit_date) tuples for archived repos.
    """
    archive_path = os.path.join(root, archive_dir)
    if not os.path.isdir(archive_path):
        return []

    result: list[tuple[str, Optional[str]]] = []
    for entry in sorted(os.listdir(archive_path)):
        full = os.path.join(archive_path, entry)
        if os.path.isdir(full) and is_git_repo(full):
            last_commit = get_last_commit_date(full)
            result.append((full, last_commit))

    return result


def unarchive_repo(
    root: str,
    repo_name: str,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
    dry_run: bool = True,
    output=None,
) -> bool:
    """Moves a repository from the archive back to the root.

    Args:
        root: Absolute path to the root directory.
        repo_name: Name of the repository to unarchive.
        archive_dir: Name of the archive directory.
        dry_run: If True, only report what would be done.
        output: File-like object for progress output (default: sys.stderr).

    Returns:
        True if successful (or would be in dry_run mode), False on error.
    """
    import shutil

    if output is None:
        output = sys.stderr

    archive_path = os.path.join(root, archive_dir)
    repo_path = os.path.join(archive_path, repo_name)
    new_path = os.path.join(root, repo_name)

    if not os.path.isdir(repo_path):
        print(f"ERROR: Repository not found in archive: {repo_name}", file=output)
        return False

    if os.path.exists(new_path):
        print(f"ERROR: Destination already exists: {new_path}", file=output)
        return False

    if dry_run:
        print(f"DRY RUN: Would move {repo_name} from {archive_dir}/ to root", file=output)
        return True

    try:
        shutil.move(repo_path, new_path)
        print(f"Unarchived: {repo_name}", file=output)
        return True
    except OSError as e:
        print(f"ERROR: {e}", file=output)
        return False


@dataclass
class CommitInfo:
    """Information about a single commit.

    Attributes:
        sha: Full commit SHA hash.
        author_date: Author date in ISO format.
        author_name: Author name.
        subject: Commit subject line.
        body: Full commit message body (may be empty).
        diff: The diff content for this commit (may be truncated).
    """

    sha: str
    author_date: str
    author_name: str
    subject: str
    body: str = ""
    diff: str = ""


def get_commits_with_diffs(
    repo_path: str,
    since_str: str,
    until_str: str,
    author: Optional[str] = None,
    max_diff_lines: int = 500,
) -> list[CommitInfo]:
    """Retrieves commits with their diffs from a repository.

    Args:
        repo_path: Absolute path to the repository.
        since_str: Start of the window in a git-recognized datetime format.
        until_str: End of the window in a git-recognized datetime format.
        author: Optional author filter passed to git --author.
        max_diff_lines: Maximum number of diff lines per commit to include.

    Returns:
        List of CommitInfo objects with commit details and diffs.
    """
    # First get the list of commits
    args = [
        "log",
        f"--since={since_str}",
        f"--until={until_str}",
        "--all",
        "--no-merges",
        "--date=iso-local",
        "--pretty=format:%H%x1f%ad%x1f%an%x1f%s%x1f%b%x00",
    ]
    if author:
        args.append(f"--author={author}")

    code, out, _ = run_git(repo_path, args)
    if code != 0:
        return []

    commits: list[CommitInfo] = []

    # Parse commit records (separated by null byte)
    for rec in out.split("\x00"):
        rec = rec.strip()
        if not rec:
            continue

        parts = rec.split("\x1f", 4)
        if len(parts) < 4:
            continue

        sha = parts[0]
        author_date = parts[1]
        author_name = parts[2]
        subject = parts[3]
        body = parts[4].strip() if len(parts) > 4 else ""

        # Get the diff for this specific commit
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

        commits.append(
            CommitInfo(
                sha=sha,
                author_date=author_date,
                author_name=author_name,
                subject=subject,
                body=body,
                diff=diff_content,
            )
        )

    return commits


def get_commit_messages(
    repo_path: str,
    since_str: str,
    until_str: str,
    author: Optional[str] = None,
) -> list[CommitInfo]:
    """Retrieves commit messages (without diffs) from a repository.

    Args:
        repo_path: Absolute path to the repository.
        since_str: Start of the window in a git-recognized datetime format.
        until_str: End of the window in a git-recognized datetime format.
        author: Optional author filter passed to git --author.

    Returns:
        List of CommitInfo objects with commit details (no diffs).
    """
    args = [
        "log",
        f"--since={since_str}",
        f"--until={until_str}",
        "--all",
        "--no-merges",
        "--date=iso-local",
        "--pretty=format:%H%x1f%ad%x1f%an%x1f%s%x1f%b%x00",
    ]
    if author:
        args.append(f"--author={author}")

    code, out, _ = run_git(repo_path, args)
    if code != 0:
        return []

    commits: list[CommitInfo] = []

    for rec in out.split("\x00"):
        rec = rec.strip()
        if not rec:
            continue

        parts = rec.split("\x1f", 4)
        if len(parts) < 4:
            continue

        sha = parts[0]
        author_date = parts[1]
        author_name = parts[2]
        subject = parts[3]
        body = parts[4].strip() if len(parts) > 4 else ""

        commits.append(
            CommitInfo(
                sha=sha,
                author_date=author_date,
                author_name=author_name,
                subject=subject,
                body=body,
            )
        )

    return commits


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for git utilities.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Git repository utilities for archiving and fetching.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Archive command
    archive_parser = subparsers.add_parser(
        "archive",
        help="Archive inactive repositories",
        description="Move repositories with no recent commits to an archive directory.",
    )
    archive_parser.add_argument(
        "--root",
        default=str(get_default_scan_root()),
        help="Root directory containing repositories (default: current directory).",
    )
    archive_parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Number of days of inactivity to trigger archive (default: 365).",
    )
    archive_parser.add_argument(
        "--archive-dir",
        default=DEFAULT_ARCHIVE_DIR,
        help=f"Name of archive directory (default: {DEFAULT_ARCHIVE_DIR}).",
    )
    archive_parser.add_argument(
        "--include-non-git",
        action="store_true",
        help="Include non-git directories (uses file modification date for activity).",
    )
    archive_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be archived without moving files (default).",
    )
    archive_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move the files (opposite of --dry-run).",
    )

    # List archived command
    list_parser = subparsers.add_parser(
        "list-archived",
        help="List archived repositories",
    )
    list_parser.add_argument(
        "--root",
        default=str(get_default_scan_root()),
        help="Root directory containing repositories (default: current directory).",
    )
    list_parser.add_argument(
        "--archive-dir",
        default=DEFAULT_ARCHIVE_DIR,
        help=f"Name of archive directory (default: {DEFAULT_ARCHIVE_DIR}).",
    )

    # Unarchive command
    unarchive_parser = subparsers.add_parser(
        "unarchive",
        help="Restore a repository from archive",
    )
    unarchive_parser.add_argument(
        "repo_name",
        help="Name of the repository to unarchive.",
    )
    unarchive_parser.add_argument(
        "--root",
        default=str(get_default_scan_root()),
        help="Root directory containing repositories (default: current directory).",
    )
    unarchive_parser.add_argument(
        "--archive-dir",
        default=DEFAULT_ARCHIVE_DIR,
        help=f"Name of archive directory (default: {DEFAULT_ARCHIVE_DIR}).",
    )
    unarchive_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without moving files (default).",
    )
    unarchive_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move the files (opposite of --dry-run).",
    )

    # Fetch command
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch all repositories",
    )
    fetch_parser.add_argument(
        "--root",
        default=str(get_default_scan_root()),
        help="Root directory containing repositories (default: current directory).",
    )
    fetch_parser.add_argument(
        "--no-submodules",
        action="store_true",
        help="Don't fetch submodules.",
    )
    fetch_parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel fetch operations (default: 8).",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "archive":
        root = os.path.abspath(args.root)
        # Default to dry-run unless --execute is specified
        dry_run = not args.execute
        result = archive_inactive_repos(
            root,
            inactive_days=args.days,
            archive_dir=args.archive_dir,
            dry_run=dry_run,
            include_non_git=args.include_non_git,
        )
        return 0 if not result.errors else 1

    elif args.command == "list-archived":
        root = os.path.abspath(args.root)
        archived = list_archived_repos(root, archive_dir=args.archive_dir)
        if not archived:
            print("No archived repositories found.")
            return 0
        print(f"Archived repositories in {args.archive_dir}/:")
        for path, last_commit in archived:
            name = os.path.basename(path)
            print(f"  {name} (last commit: {last_commit or 'unknown'})")
        print(f"\nTotal: {len(archived)} repositories")
        return 0

    elif args.command == "unarchive":
        root = os.path.abspath(args.root)
        dry_run = not args.execute
        success = unarchive_repo(
            root,
            args.repo_name,
            archive_dir=args.archive_dir,
            dry_run=dry_run,
        )
        return 0 if success else 1

    elif args.command == "fetch":
        root = os.path.abspath(args.root)
        repos = discover_top_level_repos(root)
        if not repos:
            print(f"No repositories found under: {root}")
            return 1
        _, success = fetch_repos_with_progress(
            repos,
            include_submodules=not args.no_submodules,
            max_workers=args.workers,
        )
        return 0 if success else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
