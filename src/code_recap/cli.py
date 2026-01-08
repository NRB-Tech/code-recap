#!/usr/bin/env python3
"""Code Recap CLI - unified command-line interface.

Provides a single `code-recap` command with subcommands for all functionality.
"""

import sys
from typing import Optional

from code_recap.formatting import print_heading, print_separator


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

    elif subcommand in ("init", "config"):
        return init_config(sub_argv)

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


def init_config(argv: list[str]) -> int:
    """Creates config files and sets up API keys.

    Args:
        argv: Command-line arguments.

    Returns:
        Exit code.
    """
    import argparse
    import getpass
    import os
    from pathlib import Path

    # Default config location in user's home directory
    default_config = Path.home() / ".config" / "code-recap" / "config.yaml"

    parser = argparse.ArgumentParser(
        prog="code-recap init",
        description="Initialize code-recap configuration and API keys",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(default_config),
        help=f"Config file path (default: {default_config})",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )
    parser.add_argument(
        "--no-keys",
        action="store_true",
        help="Skip API key setup",
    )
    parser.add_argument(
        "--keys-only",
        action="store_true",
        help="Only set up API keys, skip config.yaml",
    )
    args = parser.parse_args(argv)

    output_path = Path(args.output)
    created_files: list[str] = []

    # Create config.yaml
    if not args.keys_only:
        if output_path.exists() and not args.force:
            print(f"Config file {output_path} already exists (use --force to overwrite)")
        else:
            # Prompt for organization details
            print_heading("Organization Setup (press Enter to skip any)", file=sys.stdout)
            print()

            try:
                org_name = input("Organization/company name: ").strip()
                website = input("Website URL (for HTML reports): ").strip()
                print()
                print("Describe yourself or your organization (for LLM context).")
                print("This helps the AI understand your work. Press Enter twice to finish:")
                description_lines = []
                while True:
                    line = input()
                    if line == "" and description_lines and description_lines[-1] == "":
                        description_lines.pop()  # Remove trailing empty line
                        break
                    description_lines.append(line)
                description = "\n".join(description_lines).strip()
            except (KeyboardInterrupt, EOFError):
                print("\nSkipping organization setup...")
                org_name = ""
                website = ""
                description = ""

            # Build config content
            config_lines = [
                "# Code Recap Configuration",
                "# See: https://github.com/NRB-Tech/code-recap",
                "",
            ]

            # Global context
            if description:
                config_lines.append("# Context for LLM summaries")
                config_lines.append("global_context: |")
                for line in description.split("\n"):
                    config_lines.append(f"  {line}")
                config_lines.append("")
            else:
                config_lines.extend(
                    [
                        "# Global context for LLM summaries (optional)",
                        "# global_context: |",
                        "#   Brief description of your work for context in summaries.",
                        "",
                    ]
                )

            # Client configuration (always commented as example)
            config_lines.extend(
                [
                    "# Client configuration (optional - for consultants with multiple clients)",
                    "# clients:",
                    '#   "Client Name":',
                    "#     directories:",
                    '#       - "project-*"      # Glob patterns for repo names',
                    "#     context: |",
                    "#       Brief description of work for this client.",
                    "",
                    "# File patterns to exclude from statistics (optional)",
                    "# excludes:",
                    "#   global:",
                    '#     - "*.lock"',
                    '#     - "package-lock.json"',
                    '#     - "*/node_modules/*"',
                    "",
                ]
            )

            # HTML report branding
            if org_name or website:
                config_lines.append("# HTML report branding")
                config_lines.append("html_report:")
                config_lines.append("  company:")
                if org_name:
                    config_lines.append(f'    name: "{org_name}"')
                if website:
                    config_lines.append(f'    url: "{website}"')
                config_lines.append('  # accent_primary: "#2563eb"')
                config_lines.append("")
            else:
                config_lines.extend(
                    [
                        "# HTML report branding (optional)",
                        "# html_report:",
                        "#   company:",
                        '#     name: "Your Company"',
                        '#     url: "https://example.com"',
                        '#   accent_primary: "#2563eb"',
                        "",
                    ]
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("\n".join(config_lines))
            created_files.append(str(output_path))

    # Set up API keys
    keys_added = False
    if not args.no_keys:
        print()
        print_heading("API Key Setup (press Enter to skip any)", file=sys.stdout)
        print("\nGet API keys from:")
        print("  OpenAI:    https://platform.openai.com/api-keys")
        print("  Gemini:    https://aistudio.google.com/apikey")
        print("  Anthropic: https://console.anthropic.com/settings/keys")
        print()

        keys: dict[str, str] = {}  # provider -> key

        # Prompt for each key
        for name, env_var, example in [
            ("OpenAI", "OPENAI_API_KEY", "sk-..."),
            ("Gemini", "GEMINI_API_KEY", "AI..."),
            ("Anthropic", "ANTHROPIC_API_KEY", "sk-ant-..."),
        ]:
            # Check if already set in environment
            existing = os.environ.get(env_var)
            if existing:
                print(f"{name}: Already set in environment ✓")
                continue

            try:
                key = getpass.getpass(f"{name} API key ({example}): ").strip()
                if key:
                    keys[name.lower()] = key
            except (KeyboardInterrupt, EOFError):
                print("\nSkipping remaining keys...")
                break

        # Add keys to config.yaml
        if keys:
            # Read existing config or start fresh
            if output_path.exists():
                existing_content = output_path.read_text()
            else:
                existing_content = ""

            # Check if api_keys section already exists
            if "api_keys:" in existing_content:
                # Update existing api_keys section using YAML
                try:
                    import yaml  # pyright: ignore[reportMissingModuleSource]

                    data = yaml.safe_load(existing_content) or {}
                    if "api_keys" not in data:
                        data["api_keys"] = {}
                    data["api_keys"].update(keys)

                    # Write back, preserving comments if possible by appending
                    with open(output_path, "w") as f:
                        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                    print(f"\nUpdated API keys in {output_path}")
                except Exception as e:
                    print(f"Warning: Could not update config: {e}", file=sys.stderr)
            else:
                # Append new api_keys section
                api_keys_section = [
                    "",
                    "# API Keys (loaded automatically, env vars take precedence)",
                    "api_keys:",
                ]
                for provider, key in keys.items():
                    api_keys_section.append(f'  {provider}: "{key}"')
                api_keys_section.append("")

                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "a") as f:
                    f.write("\n".join(api_keys_section))
                print(f"\nAdded API keys to {output_path}")

            keys_added = True

    # Summary
    print()
    print_separator(file=sys.stdout)
    if created_files or keys_added:
        print("Setup complete:")
        for f in created_files:
            print(f"  ✓ Created {f}")
        if keys_added:
            print(f"  ✓ API keys saved to {output_path}")
    else:
        print("No files created.")

    # Next steps
    print()
    print_heading("Next steps:", file=sys.stdout)

    print("""
# Generate a year-in-review summary
code-recap summarize 2025 --open

# Quick daily summary for time logging
code-recap daily

# Statistics only (no LLM, no API key needed)
code-recap stats 2025 --format markdown
""")
    print("Documentation: https://github.com/NRB-Tech/code-recap")
    return 0


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
  init                 Create a template config.yaml file

Quick start:
  code-recap summarize 2025                # Generates markdown + HTML reports
  code-recap summarize 2025 --open         # Also opens HTML in browser
  code-recap daily
  code-recap stats 2025 --format markdown

Options:
  -h, --help           Show this help message
  -v, --version        Show version number
  help <command>       Show help for a specific command

Examples:
  code-recap summarize 2025                               # Year summary + HTML
  code-recap summarize 2025-Q3                            # Quarter
  code-recap summarize 2025-06 --no-html                  # Month, skip HTML
  code-recap daily --date yesterday                       # Daily summary
  code-recap stats 2020:2025 --granularity year

Environment variables:
  OPENAI_API_KEY       For GPT models (default: gpt-4o-mini)
  GEMINI_API_KEY       For Google Gemini models
  ANTHROPIC_API_KEY    For Claude models
"""
    print(help_text)


if __name__ == "__main__":
    sys.exit(main())
