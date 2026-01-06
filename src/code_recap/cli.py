#!/usr/bin/env python3
"""Code Recap CLI - unified command-line interface.

Provides a single `code-recap` command with subcommands for all functionality.
"""

import sys
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point for the code-recap CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code.
    """
    if argv is None:
        argv = sys.argv[1:]

    # Show help if no arguments
    if not argv or argv[0] in ("-h", "--help"):
        print_help()
        return 0

    # Handle version
    if argv[0] in ("-v", "--version"):
        from code_recap import __version__

        print(f"code-recap {__version__}")
        return 0

    # Route to subcommand
    subcommand = argv[0]
    sub_argv = argv[1:]

    if subcommand in ("summarize", "summary", "report"):
        from code_recap.summarize_activity import main as summarize_main

        return summarize_main(sub_argv)

    elif subcommand in ("daily", "today"):
        from code_recap.summarize_daily_activity import main as daily_main

        return daily_main(sub_argv)

    elif subcommand in ("stats", "review", "activity"):
        from code_recap.git_activity_review import main as review_main

        return review_main(sub_argv)

    elif subcommand in ("html", "html-report"):
        from code_recap.generate_html_report import main as html_main

        return html_main(sub_argv)

    elif subcommand in ("blog", "blog-post"):
        from code_recap.generate_blog_post import main as blog_main

        return blog_main(sub_argv)

    elif subcommand in ("commits", "list-commits"):
        from code_recap.list_commits_by_date import main as commits_main

        return commits_main(sub_argv)

    elif subcommand in ("deploy",):
        from code_recap.deploy_reports import main as deploy_main

        return deploy_main(sub_argv)

    elif subcommand in ("git", "repos"):
        from code_recap.git_utils import main as git_main

        return git_main(sub_argv)

    elif subcommand == "help":
        if sub_argv:
            # Show help for specific subcommand
            return main([sub_argv[0], "--help"])
        print_help()
        return 0

    else:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        print("Run 'code-recap --help' for available commands.", file=sys.stderr)
        return 1


def print_help() -> None:
    """Prints the main help message."""
    from code_recap import __version__

    help_text = f"""code-recap {__version__} - Git activity summaries powered by LLMs

Usage: code-recap <command> [options]

Commands:
  summarize, report    Generate LLM-powered activity summaries (main command)
  daily, today         Summarize today's (or any day's) activity for time logging
  stats, activity      Generate statistics without LLM (text/markdown/CSV)
  html                 Convert markdown reports to HTML
  blog                 Generate blog posts from git activity
  commits              List commits for a specific date
  deploy               Deploy HTML reports (zip, Cloudflare)
  git, repos           Repository utilities (fetch, archive)

Quick start:
  code-recap summarize 2025 --author "Your Name"
  code-recap summarize 2025 --author "Your Name" --html
  code-recap daily --author "Your Name"
  code-recap stats 2025 --author "Your Name" --format markdown

Options:
  -h, --help           Show this help message
  -v, --version        Show version number
  help <command>       Show help for a specific command

Examples:
  code-recap summarize 2025 --author "Your Name"           # Year summary
  code-recap summarize 2025-Q3 --author "Your Name"        # Quarter
  code-recap summarize 2025-06 --author "Your Name" --html # Month + HTML
  code-recap daily --author "Your Name" --date yesterday   # Daily summary
  code-recap stats 2020:2025 --author "Your Name" --granularity year

Environment variables:
  OPENAI_API_KEY       For GPT models (default: gpt-4o-mini)
  GEMINI_API_KEY       For Google Gemini models
  ANTHROPIC_API_KEY    For Claude models
"""
    print(help_text)


if __name__ == "__main__":
    sys.exit(main())
