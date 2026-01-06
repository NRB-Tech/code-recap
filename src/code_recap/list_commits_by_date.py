#!/usr/bin/env python3

import argparse
import datetime as _dt
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from code_recap.arguments import add_author_arg, add_root_arg, resolve_author
from code_recap.git_utils import (
    discover_all_submodules,
    discover_top_level_repos,
    run_git,
)
from code_recap.paths import get_default_output_dir_name, get_output_dir


@dataclass
class CommitRecord:
    """Represents a single commit entry.

    Attributes:
        sha: Full commit SHA hash.
        author_date: Author date string as reported by git (local time).
        author_name: Author name.
        subject: Commit subject line.
        branches: Collection of branches containing this commit.
    """

    sha: str
    author_date: str
    author_name: str
    subject: str
    branches: Sequence[str]


def parse_date_to_range(date_str: str) -> tuple[str, str]:
    """Parses a YYYY-MM-DD date string into inclusive day range for git.

    Args:
        date_str: Date string in the format YYYY-MM-DD.

    Returns:
        A tuple of (since_str, until_str) formatted for git's --since/--until.

    Notes:
        The time window is local time from 00:00:00 to 23:59:59 of the date.
    """
    try:
        day = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"Invalid date '{date_str}'. Use YYYY-MM-DD.") from exc

    start = _dt.datetime.combine(day, _dt.time.min)
    end = _dt.datetime.combine(day, _dt.time.max)
    # Git accepts many formats. Use ISO-like local times to avoid TZ surprises.
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        end.strftime("%Y-%m-%d %H:%M:%S"),
    )


def get_commits_on_date(
    repo_path: str,
    since_str: str,
    until_str: str,
    author: Optional[str] = None,
) -> list[CommitRecord]:
    """Retrieves commits in a repository within the provided date range.

    Args:
        repo_path: Absolute path to the repository.
        since_str: Start of the window in a git-recognized datetime format.
        until_str: End of the window in a git-recognized datetime format.
        author: Optional author filter passed to git (same semantics as
            `git log --author`). If provided, only commits by matching authors
            are returned.

    Returns:
        A list of CommitRecord entries, with branches field empty. Branches are
        intended to be filled by `populate_branches_for_commits`.
    """
    pretty = "%H%x1f%ad%x1f%an%x1f%s%x1e"
    args = [
        "log",
        f"--since={since_str}",
        f"--until={until_str}",
        "--all",
        "--date=iso-local",
        f"--pretty=format:{pretty}",
    ]
    if author:
        args.append(f"--author={author}")
    code, out, err = run_git(repo_path, args)
    if code != 0:
        return []

    records: list[CommitRecord] = []
    for rec in out.strip().split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) != 4:
            continue
        sha, author_date, author_name, subject = parts
        records.append(
            CommitRecord(
                sha=sha,
                author_date=author_date,
                author_name=author_name,
                subject=subject,
                branches=(),
            )
        )
    return records


def _list_branches(repo_path: str, include_remotes: bool) -> list[tuple[str, str]]:
    """Lists branch references in the repository.

    Args:
        repo_path: Absolute path to the repository.
        include_remotes: Whether to include remote branches in the result.

    Returns:
        A list of tuples of (ref_namespace, ref_short_name). `ref_namespace` is
        one of 'heads' or 'remotes' to indicate local vs remote branch space.
    """
    branches: list[tuple[str, str]] = []
    code, out, _ = run_git(
        repo_path,
        [
            "for-each-ref",
            "--format=%(refname:short)\t%(refname)\t%(objecttype)",
            "refs/heads",
        ],
    )
    if code == 0:
        for line in out.splitlines():
            short, full, objtype = (line.split("\t") + [""] * 3)[:3]
            if objtype != "commit":
                continue
            branches.append(("heads", short))

    if include_remotes:
        code, out, _ = run_git(
            repo_path,
            [
                "for-each-ref",
                "--format=%(refname:short)\t%(refname)\t%(objecttype)",
                "refs/remotes",
            ],
        )
        if code == 0:
            for line in out.splitlines():
                short, full, objtype = (line.split("\t") + [""] * 3)[:3]
                if objtype != "commit":
                    continue
                # Skip HEAD symbolic references like origin/HEAD
                if short.endswith("/HEAD"):
                    continue
                branches.append(("remotes", short))
    return branches


def populate_branches_for_commits(
    repo_path: str,
    commits: list[CommitRecord],
    since_str: str,
    until_str: str,
    include_remotes: bool,
) -> None:
    """Fills the `branches` field for each CommitRecord in-place.

    Args:
        repo_path: Absolute path to the repository.
        commits: List of commit records whose branches should be populated.
        since_str: Start of the window in a git-recognized datetime format.
        until_str: End of the window in a git-recognized datetime format.
        include_remotes: Whether to consider remote branches as well.

    Returns:
        None. The `commits` list is modified in-place.

    Notes:
        To avoid per-commit branch resolution cost, this builds an index by
        iterating branches and collecting commit SHAs within the same date
        window, then mapping back to the target commits.
    """
    if not commits:
        return
    target_shas = {c.sha for c in commits}
    sha_to_branches: dict[str, list[str]] = {sha: [] for sha in target_shas}

    for ref_space, short_name in _list_branches(repo_path, include_remotes):
        rev = short_name
        args = [
            "rev-list",
            f"--since={since_str}",
            f"--until={until_str}",
            rev,
        ]
        code, out, _ = run_git(repo_path, args)
        if code != 0:
            continue
        for sha in out.splitlines():
            if sha in sha_to_branches:
                label = short_name if ref_space == "heads" else f"{short_name}"
                if label not in sha_to_branches[sha]:
                    sha_to_branches[sha].append(label)

    for c in commits:
        c.branches = tuple(sorted(sha_to_branches.get(c.sha, [])))


def format_project_header(project_name: str, project_path: str) -> str:
    """Creates a visual header string for a project group.

    Args:
        project_name: Display name for the project.
        project_path: Absolute path to the project repository directory.

    Returns:
        A formatted header string separating project sections visually.
    """
    line = f"Project: {project_name} ({project_path})"
    underline = "-" * len(line)
    return f"{line}\n{underline}"


def print_repo_commits(
    repo_label: str,
    repo_path: str,
    commits: list[CommitRecord],
) -> None:
    """Prints commits for a repository with a label.

    Args:
        repo_label: Human-readable label for the repository (e.g., "root",
            or a submodule path).
        repo_path: Absolute path to the repository.
        commits: List of commit records to print.

    Returns:
        None. Outputs to stdout.
    """
    if not commits:
        return
    print(f"  Repo: {repo_label}")
    for c in sorted(commits, key=lambda r: (r.author_date, r.sha)):
        branches_str = f" [{', '.join(c.branches)}]" if c.branches else ""
        line = f"    {c.author_date} | {c.author_name} | {c.sha[:12]} | {c.subject}{branches_str}"
        print(line)


def process_repository(
    repo_path: str,
    since_str: str,
    until_str: str,
    include_remotes: bool,
    author: Optional[str] = None,
) -> list[tuple[str, list[CommitRecord]]]:
    """Collects commit records for a repository and its submodules.

    Args:
        repo_path: Absolute path to the top-level repository.
        since_str: Start of the day window for filtering commits.
        until_str: End of the day window for filtering commits.
        include_remotes: Whether to include remote branches when resolving
            branches.
        author: Optional author filter passed to git (same semantics as
            `git log --author`). If provided, only commits by matching authors
            are returned.

    Returns:
        A list of tuples mapping (repo_label, commits) for the top-level repo
        (label: "root") and each submodule (label: submodule relative path).
    """
    results: list[tuple[str, list[CommitRecord]]] = []

    # Top-level repo
    root_commits = get_commits_on_date(repo_path, since_str, until_str, author=author)
    populate_branches_for_commits(
        repo_path,
        root_commits,
        since_str,
        until_str,
        include_remotes,
    )
    if root_commits:
        results.append(("root", root_commits))

    # Submodules (recursive)
    for sub_path in discover_all_submodules(repo_path):
        label = os.path.relpath(sub_path, repo_path)
        commits = get_commits_on_date(sub_path, since_str, until_str, author=author)
        populate_branches_for_commits(
            sub_path,
            commits,
            since_str,
            until_str,
            include_remotes,
        )
        if commits:
            results.append((label, commits))

    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the CLI tool.

    Args:
        argv: Optional sequence of command-line arguments. If None, uses
            sys.argv.

    Returns:
        Process exit code: 0 on success.

    Seealso:
        The function prints results grouped by project (directory under
        `--root`). Each repository group includes the top-level repo and any
        submodules.
    """
    parser = argparse.ArgumentParser(
        description=(
            "List commits across repositories on a given date, grouped by "
            "project. Each immediate child directory of --root is treated "
            "as a project (git repo)."
        )
    )
    parser.add_argument(
        "date",
        help="Date to query in YYYY-MM-DD format (local time).",
    )
    add_root_arg(parser)
    parser.add_argument(
        "--include-remotes",
        action="store_true",
        help=("Include remote branches when indicating branches containing commits."),
    )
    parser.add_argument(
        "--show-empty",
        action="store_true",
        help=("Show projects with no commits on the given date."),
    )
    add_author_arg(parser)
    parser.add_argument(
        "--remove-duplicates",
        action="store_true",
        help=("Remove duplicate commits across projects by tracking commit hashes."),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file path. Default: stdout (use --save for auto-naming).",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save output to file with auto-generated name in output directory.",
    )
    parser.add_argument(
        "--client",
        metavar="NAME",
        help="Client name for organizing output files (creates output/<client>/ subdirectory).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Base output directory (default: {get_default_output_dir_name()}).",
    )

    args = parser.parse_args(argv)

    resolve_author(args, required=False, output=sys.stdout)

    root = os.path.abspath(args.root)
    since_str, until_str = parse_date_to_range(args.date)

    repos = discover_top_level_repos(root)
    if not repos:
        print(f"No git repositories found under: {root}")
        return 0

    # Determine output destination
    output_lines: list[str] = []

    def output(text: str = "") -> None:
        output_lines.append(text)

    printed_shas: set[str] = set()

    for repo in repos:
        project_name = os.path.basename(repo)
        groups = process_repository(
            repo,
            since_str,
            until_str,
            include_remotes=args.include_remotes,
            author=args.author,
        )

        if args.remove_duplicates and groups:
            filtered_groups: list[tuple[str, list[CommitRecord]]] = []
            for label, commits in groups:
                unique_commits: list[CommitRecord] = [
                    c for c in commits if c.sha not in printed_shas
                ]
                if unique_commits:
                    filtered_groups.append((label, unique_commits))
                    for c in unique_commits:
                        printed_shas.add(c.sha)
            groups = filtered_groups

        if not groups and not args.show_empty:
            continue

        output(format_project_header(project_name, repo))
        if not groups:
            output("  (no commits)")
            continue

        for label, group_commits in groups:
            if not group_commits:
                continue
            output(f"  Repo: {label}")
            for c in sorted(group_commits, key=lambda r: (r.author_date, r.sha)):
                branches_str = f" [{', '.join(c.branches)}]" if c.branches else ""
                line = f"    {c.author_date} | {c.author_name} | {c.sha[:12]} | {c.subject}{branches_str}"
                output(line)
        output()

    # Determine output path
    if args.output:
        output_path = args.output
    elif args.save:
        output_dir_path = get_output_dir(
            output_dir=args.output_dir,
            period=args.date[:7],  # Use year-month as period
            client=args.client,
        )
        output_dir = str(output_dir_path)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"commits-{args.date}.txt")
    else:
        output_path = None

    # Write output
    content = "\n".join(output_lines)
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            f.write(content)
        print(f"Output written to: {output_path}", file=sys.stderr)
    else:
        print(content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
