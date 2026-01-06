#!/usr/bin/env python3
"""Summarize git activity using LLM-powered hierarchical summarization.

This script generates narrative summaries of git activity over configurable
time periods. It uses hierarchical summarization to control costs: first
summarizing each granular period (week/month), then aggregating those
summaries into a final narrative.

Requires the litellm package and appropriate API keys set as environment
variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY).
"""

import argparse
import datetime as _dt
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Optional

from code_recap.arguments import (
    add_author_arg,
    add_config_arg,
    add_exclude_args,
    add_fetch_arg,
    add_model_args,
    add_output_dir_arg,
    add_root_arg,
    resolve_author,
)
from code_recap.git_activity_review import (
    DEFAULT_EXCLUDE_PATTERNS,
    ExcludeConfig,
    LanguageStats,
    PeriodStats,
    ProjectSummary,
    aggregate_period_stats,
    date_range_to_git_args,
    format_number,
    get_primary_language,
    load_excludes_file,
    parse_period,
    parse_range,
    process_repos_for_period,
)
from code_recap.git_utils import (
    CommitInfo,
    discover_all_submodules,
    discover_top_level_repos,
    fetch_repos_with_progress,
    get_commit_messages,
    get_commits_with_diffs,
)
from code_recap.paths import (
    get_config_path,
    get_output_dir,
    load_api_keys_from_config,
)

# Default model (cheapest option)
DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class PromptConfig:
    """Configuration for LLM system prompts.

    Attributes:
        period_summary: System prompt for period/monthly summaries (client-facing).
        final_summary: System prompt for client summary reports (client-facing).
        internal_summary: System prompt for internal company summary (company-facing).
    """

    period_summary: Optional[str] = None
    final_summary: Optional[str] = None
    internal_summary: Optional[str] = None


# Valid disclosure levels for public summaries
DISCLOSURE_LEVELS = {"full", "anonymize", "suppress"}
DEFAULT_DISCLOSURE = "anonymize"


@dataclass
class ClientDisclosure:
    """Disclosure rules for a client in public summaries.

    Attributes:
        disclosure: How to handle client in public content ("full", "anonymize", "suppress").
        description: How to refer to client when anonymized (e.g., "a music tech company").
    """

    disclosure: str = DEFAULT_DISCLOSURE
    description: str = ""


@dataclass
class PublicSummaryConfig:
    """Configuration for public-facing summary generation.

    Attributes:
        default_disclosure: Default disclosure level for all clients.
        client_disclosures: Per-client disclosure overrides.
        enabled: Whether to generate public summaries at all.
    """

    default_disclosure: str = DEFAULT_DISCLOSURE
    client_disclosures: dict[str, ClientDisclosure] = field(default_factory=dict)
    enabled: bool = True

    def get_disclosure(self, client_name: str) -> ClientDisclosure:
        """Gets the disclosure rules for a client.

        Args:
            client_name: Name of the client.

        Returns:
            ClientDisclosure with the applicable rules.
        """
        if client_name in self.client_disclosures:
            return self.client_disclosures[client_name]
        return ClientDisclosure(disclosure=self.default_disclosure)

    def format_client_info_for_prompt(self, client_results: dict[str, str]) -> str:
        """Formats client information for the public summary prompt.

        Args:
            client_results: Dict of client name to their summary content.

        Returns:
            Formatted string with disclosure-aware client information.
        """
        lines = []
        for client_name, summary in client_results.items():
            disclosure = self.get_disclosure(client_name)

            if disclosure.disclosure == "suppress":
                continue
            elif disclosure.disclosure == "full":
                lines.append(f"## {client_name}")
                lines.append(summary)
                lines.append("")
            else:  # anonymize
                anon_name = disclosure.description or f"Client ({client_name[:1]}...)"
                lines.append(f"## {anon_name}")
                lines.append(f"[Anonymize references to '{client_name}' as '{anon_name}']")
                lines.append(summary)
                lines.append("")

        return "\n".join(lines)


# Valid audience levels for client summaries
AUDIENCE_LEVELS = {
    "technical": "The reader has deep technical knowledge. Use precise technical terminology freely.",
    "developer": "The reader is a developer but may not know all domain-specific details. "
    "Use technical terms but briefly explain domain-specific concepts.",
    "mixed": "The audience has varying technical knowledge. Balance technical accuracy with "
    "accessibility. Explain technical terms when first used.",
    "business": "The reader is a business stakeholder without deep technical knowledge. "
    "Explain WHAT was done and WHY it matters in plain language. Avoid jargon or explain it simply.",
    "general": "The reader has minimal technical background. Focus entirely on outcomes, benefits, "
    "and business value. Translate all technical concepts into everyday language.",
}
DEFAULT_AUDIENCE = "mixed"


@dataclass
class ClientMatcher:
    """Holds matching rules for a single client.

    Attributes:
        directories: Directory name patterns to include (exact or glob, case-insensitive).
        exclude: Directory name patterns to exclude (takes precedence over directories).
        context: Optional context about the client for LLM summaries.
        audience: Target audience level for summaries (technical, developer, mixed, business, general).
    """

    directories: list[str]
    exclude: list[str]
    context: str = ""
    audience: str = DEFAULT_AUDIENCE

    def matches(self, project_name: str) -> bool:
        """Checks if a project matches this client's rules.

        Args:
            project_name: Name of the project (repo directory name).

        Returns:
            True if project matches any directory pattern and no exclude pattern.
        """
        import fnmatch

        project_lower = project_name.lower()

        # Check exclusions first
        if any(fnmatch.fnmatch(project_lower, pattern.lower()) for pattern in self.exclude):
            return False

        # Check inclusions
        return any(fnmatch.fnmatch(project_lower, pattern.lower()) for pattern in self.directories)


@dataclass
class ClientConfig:
    """Configuration for client-based project categorization.

    Attributes:
        clients: Mapping of client name to ClientMatcher.
        default_client: Client name for unmatched projects (None = "Other").
        global_context: Context about the company for all summaries.
    """

    clients: dict[str, ClientMatcher]
    default_client: Optional[str] = None
    global_context: str = ""

    def get_client_context(self, client_name: str) -> str:
        """Gets the context string for a client.

        Args:
            client_name: Name of the client.

        Returns:
            Client-specific context, or empty string if not found.
        """
        if client_name in self.clients:
            return self.clients[client_name].context
        return ""

    def get_client_audience(self, client_name: str) -> str:
        """Gets the audience level for a client.

        Args:
            client_name: Name of the client.

        Returns:
            Audience level (technical, developer, mixed, business, general).
        """
        if client_name in self.clients:
            return self.clients[client_name].audience
        return DEFAULT_AUDIENCE

    def categorize_project(self, project_name: str) -> str:
        """Determines which client a project belongs to.

        Args:
            project_name: Name of the project (repo directory name).

        Returns:
            Client name, or default_client, or "Other".
        """
        for client, matcher in self.clients.items():
            if matcher.matches(project_name):
                return client
        return self.default_client or "Other"

    def categorize_repos(self, repos: list[str]) -> dict[str, list[str]]:
        """Groups repositories by client.

        Args:
            repos: List of repository paths.

        Returns:
            Dict mapping client name to list of repo paths.
        """
        result: dict[str, list[str]] = {}
        for repo in repos:
            project_name = os.path.basename(repo)
            client = self.categorize_project(project_name)
            if client not in result:
                result[client] = []
            result[client].append(repo)
        return result


def load_config(
    config_path: str,
) -> tuple[
    Optional[ClientConfig],
    Optional[ExcludeConfig],
    Optional[PromptConfig],
    Optional[PublicSummaryConfig],
]:
    """Loads unified configuration from YAML file.

    Args:
        config_path: Path to the config.yaml file.

    Returns:
        Tuple of (ClientConfig, ExcludeConfig, PromptConfig, PublicSummaryConfig),
        any may be None if not present.
    """
    if not os.path.isfile(config_path):
        return None, None, None, None

    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        print(
            "Warning: PyYAML not installed. Install with: pip install pyyaml",
            file=sys.stderr,
        )
        return None, None, None, None

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)

        if not data:
            return None, None, None, None

        # Load client configuration
        client_config: Optional[ClientConfig] = None
        if "clients" in data and data["clients"]:
            clients: dict[str, ClientMatcher] = {}
            for client_name, client_data in data["clients"].items():
                directories: list[str] = []
                exclude: list[str] = []
                context: str = ""
                audience: str = DEFAULT_AUDIENCE

                if isinstance(client_data, dict):
                    directories = client_data.get("directories", []) or []
                    exclude = client_data.get("exclude", []) or []
                    context = client_data.get("context", "") or ""
                    audience = client_data.get("audience", DEFAULT_AUDIENCE) or DEFAULT_AUDIENCE
                    # Validate audience level
                    if audience not in AUDIENCE_LEVELS:
                        print(
                            f"Warning: Unknown audience '{audience}' for client '{client_name}', "
                            f"using '{DEFAULT_AUDIENCE}'. Valid options: {', '.join(AUDIENCE_LEVELS.keys())}",
                            file=sys.stderr,
                        )
                        audience = DEFAULT_AUDIENCE
                elif isinstance(client_data, list):
                    # Simple list format
                    directories = client_data

                clients[client_name] = ClientMatcher(
                    directories=directories, exclude=exclude, context=context, audience=audience
                )

            default_client = data.get("default_client")
            global_context = data.get("global_context", "") or ""
            client_config = ClientConfig(
                clients=clients, default_client=default_client, global_context=global_context
            )

        # Load exclude configuration
        exclude_config: Optional[ExcludeConfig] = None
        if "excludes" in data and data["excludes"]:
            excludes_data = data["excludes"]
            exclude_config = ExcludeConfig()

            if "global" in excludes_data and excludes_data["global"]:
                exclude_config.global_patterns.extend(excludes_data["global"])

            if "projects" in excludes_data and excludes_data["projects"]:
                for project, patterns in excludes_data["projects"].items():
                    exclude_config.project_patterns[project] = patterns

        # Load prompt overrides
        prompt_config: Optional[PromptConfig] = None
        if "prompts" in data and data["prompts"]:
            prompts_data = data["prompts"]
            prompt_config = PromptConfig(
                period_summary=prompts_data.get("period_summary"),
                final_summary=prompts_data.get("final_summary"),
                internal_summary=prompts_data.get("internal_summary"),
            )

        # Load public summary configuration
        public_config: Optional[PublicSummaryConfig] = None
        if "public_summary" in data and data["public_summary"]:
            ps_data = data["public_summary"]
            default_disclosure = ps_data.get("default_disclosure", DEFAULT_DISCLOSURE)
            if default_disclosure not in DISCLOSURE_LEVELS:
                print(
                    f"Warning: Unknown disclosure level '{default_disclosure}', "
                    f"using '{DEFAULT_DISCLOSURE}'. Valid: {', '.join(DISCLOSURE_LEVELS)}",
                    file=sys.stderr,
                )
                default_disclosure = DEFAULT_DISCLOSURE

            client_disclosures: dict[str, ClientDisclosure] = {}
            if "clients" in ps_data and ps_data["clients"]:
                for client_name, cd_data in ps_data["clients"].items():
                    if isinstance(cd_data, dict):
                        disclosure = cd_data.get("disclosure", default_disclosure)
                        if disclosure not in DISCLOSURE_LEVELS:
                            print(
                                f"Warning: Unknown disclosure '{disclosure}' for '{client_name}', "
                                f"using '{default_disclosure}'",
                                file=sys.stderr,
                            )
                            disclosure = default_disclosure
                        description = cd_data.get("description", "") or ""
                        client_disclosures[client_name] = ClientDisclosure(
                            disclosure=disclosure, description=description
                        )
                    elif isinstance(cd_data, str):
                        # Simple format: just the disclosure level
                        if cd_data in DISCLOSURE_LEVELS:
                            client_disclosures[client_name] = ClientDisclosure(disclosure=cd_data)

            enabled = ps_data.get("enabled", True)
            public_config = PublicSummaryConfig(
                default_disclosure=default_disclosure,
                client_disclosures=client_disclosures,
                enabled=enabled,
            )

        return client_config, exclude_config, prompt_config, public_config

    except Exception as e:
        print(f"Warning: Failed to load config: {e}", file=sys.stderr)
        return None, None, None, None


# Recommended models by provider (for --list-models)
RECOMMENDED_MODELS = {
    "openai": [
        ("gpt-4o-mini", "Cheapest, good for most tasks"),
        ("gpt-4o", "Capable, moderate cost"),
        ("gpt-5.2-mini", "Latest mini model"),
        ("gpt-5.2", "Latest flagship model"),
    ],
    "anthropic": [
        ("claude-haiku-4-5", "Cheapest Claude, fast"),
        ("claude-sonnet-4-5", "Balanced performance"),
        ("claude-opus-4-5", "Most capable Claude"),
    ],
    "gemini": [
        ("gemini/gemini-2.0-flash-lite", "Cheapest Gemini"),
        ("gemini/gemini-2.5-flash", "Good balance, 1M context"),
        ("gemini/gemini-3-flash-preview", "Latest Flash"),
        ("gemini/gemini-3-pro-preview", "Most capable Gemini"),
    ],
}


def list_available_models(provider: Optional[str] = None) -> None:
    """Lists available models, optionally filtered by provider.

    Args:
        provider: Optional provider filter ('openai', 'anthropic', 'gemini', or None for all).
    """
    try:
        import litellm  # pyright: ignore[reportMissingImports]
    except ImportError:
        print("litellm not installed. Install with: pip install litellm", file=sys.stderr)
        return

    if provider:
        provider = provider.lower()
        if provider not in RECOMMENDED_MODELS:
            print(f"Unknown provider: {provider}", file=sys.stderr)
            print(f"Available providers: {', '.join(RECOMMENDED_MODELS.keys())}", file=sys.stderr)
            return
        providers_to_show = [provider]
    else:
        providers_to_show = list(RECOMMENDED_MODELS.keys())

    print("Recommended models for summarization:\n")

    for prov in providers_to_show:
        print(f"=== {prov.upper()} ===")
        for model, description in RECOMMENDED_MODELS[prov]:
            # Try to get cost info from litellm
            cost_info = ""
            try:
                if model in litellm.model_cost:
                    cost = litellm.model_cost[model]
                    input_cost = cost.get("input_cost_per_token", 0) * 1_000_000
                    output_cost = cost.get("output_cost_per_token", 0) * 1_000_000
                    cost_info = f" [${input_cost:.2f}/${output_cost:.2f} per 1M tokens]"
            except Exception:
                pass
            print(f"  {model:<45} {description}{cost_info}")
        print()

    # Also show how to list all models from litellm
    print("To see all models supported by litellm:")
    print('  python3 -c "import litellm; print(sorted(litellm.model_cost.keys()))"')
    print()
    print("Or query provider APIs directly:")
    print(
        "  # Gemini: curl 'https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY'"
    )


def _generate_subperiods(
    start: _dt.date,
    end: _dt.date,
    granularity: str,
) -> list[tuple[str, _dt.date, _dt.date]]:
    """Generates sub-periods within a date range based on granularity.

    Args:
        start: Start date of the range.
        end: End date of the range.
        granularity: One of "year", "quarter", "month", "week".

    Returns:
        List of (label, start_date, end_date) tuples.
    """
    periods: list[tuple[str, _dt.date, _dt.date]] = []

    if granularity == "year":
        year = start.year
        while year <= end.year:
            period_start = _dt.date(year, 1, 1)
            period_end = _dt.date(year, 12, 31)
            if period_end > end:
                period_end = end
            if period_start < start:
                period_start = start
            periods.append((str(year), period_start, period_end))
            year += 1

    elif granularity == "quarter":
        current = _dt.date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
        while current <= end:
            year = current.year
            quarter = (current.month - 1) // 3 + 1
            label = f"{year}-Q{quarter}"
            period_start = current
            if quarter == 4:
                period_end = _dt.date(year, 12, 31)
            else:
                period_end = _dt.date(year, quarter * 3 + 1, 1) - _dt.timedelta(days=1)
            if period_end > end:
                period_end = end
            if period_start < start:
                period_start = start
            periods.append((label, period_start, period_end))
            if quarter == 4:
                current = _dt.date(year + 1, 1, 1)
            else:
                current = _dt.date(year, quarter * 3 + 1, 1)

    elif granularity == "month":
        current = _dt.date(start.year, start.month, 1)
        while current <= end:
            year = current.year
            month = current.month
            label = f"{year}-{month:02d}"
            period_start = current
            if month == 12:
                period_end = _dt.date(year, 12, 31)
                next_month = _dt.date(year + 1, 1, 1)
            else:
                period_end = _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)
                next_month = _dt.date(year, month + 1, 1)
            if period_end > end:
                period_end = end
            if period_start < start:
                period_start = start
            periods.append((label, period_start, period_end))
            current = next_month

    elif granularity == "week":
        iso_year, iso_week, _ = start.isocalendar()
        current = _dt.date.fromisocalendar(iso_year, iso_week, 1)
        while current <= end:
            iso_year, iso_week, _ = current.isocalendar()
            label = f"{iso_year}-W{iso_week:02d}"
            period_start = current
            period_end = current + _dt.timedelta(days=6)
            if period_end > end:
                period_end = end
            if period_start < start:
                period_start = start
            periods.append((label, period_start, period_end))
            current = current + _dt.timedelta(days=7)

    return periods


# System prompts for hierarchical summarization
# CLIENT-FACING prompts are used when generating reports for specific clients
# INTERNAL prompts are used when no clients are configured (personal/team use)
# {audience_guidance} placeholder is replaced with audience-specific instructions

PERIOD_SUMMARY_SYSTEM_PROMPT = """You are an expert at summarizing software development activity.
Given git activity data for a specific time period, provide a concise but comprehensive summary.

IMPORTANT: This is a CLIENT-FACING summary. Write as a professional report for the client,
not for internal consulting company use. Do not reference or write from the perspective of
a consulting company.

AUDIENCE: {audience_guidance}

Start with a 1-2 sentence introduction paragraph summarizing the main focus of the period.

Then use EXACTLY these section headings (### level):

### Key Changes & Features
The main accomplishments, features implemented, and improvements made.
Group related items with sub-bullets. For each item, explain what was built and why it's valuable.

### Technologies & Languages
The primary languages, frameworks, tools, and protocols used.
Mention specific versions or SDKs where relevant.

### Patterns & Architecture
Notable development patterns, architectural decisions, refactoring efforts,
and any significant structural changes to the codebase.

Be specific about what was accomplished, mentioning project names and technologies where relevant.
Keep the summary focused and actionable - this will be combined with other period summaries later.
Do NOT add any other sections or headings beyond those specified above."""

# Internal period prompt (for personal/team use without clients)
INTERNAL_PERIOD_SUMMARY_PROMPT = """You are an expert at summarizing software development activity.
Given git activity data for a specific time period, provide a concise but comprehensive summary.

This is an INTERNAL summary for the developer or team. Be direct and technical.

Start with a 1-2 sentence introduction paragraph summarizing the main focus of the period.

Then use EXACTLY these section headings (### level):

### Key Changes & Features
The main accomplishments, features implemented, and improvements made.
Group related items with sub-bullets. Be specific about what was built.

### Technologies & Languages
The primary languages, frameworks, tools, and protocols used.
Mention specific versions or SDKs where relevant.

### Patterns & Architecture
Notable development patterns, architectural decisions, refactoring efforts,
and any significant structural changes to the codebase.

Be specific about what was accomplished, mentioning project names and technologies where relevant.
Keep the summary focused - this will be combined with other period summaries later.
Do NOT add any other sections or headings beyond those specified above."""


def build_period_system_prompt(
    global_context: str = "",
    client_context: str = "",
    base_prompt: Optional[str] = None,
    audience: str = DEFAULT_AUDIENCE,
) -> str:
    """Builds the system prompt for period summaries with optional context.

    Args:
        global_context: Company-wide context to include.
        client_context: Client-specific context to include.
        base_prompt: Override for the base system prompt.
        audience: Target audience level (technical, developer, mixed, business, general).

    Returns:
        Complete system prompt with context.
    """
    prompt = base_prompt if base_prompt else PERIOD_SUMMARY_SYSTEM_PROMPT

    # Insert audience guidance
    audience_guidance = AUDIENCE_LEVELS.get(audience, AUDIENCE_LEVELS[DEFAULT_AUDIENCE])
    prompt = prompt.replace("{audience_guidance}", audience_guidance)

    context_parts = []
    if global_context:
        context_parts.append(f"Company Background:\n{global_context}")
    if client_context:
        context_parts.append(f"Client Context:\n{client_context}")

    if context_parts:
        prompt += "\n\n---\n\n" + "\n\n".join(context_parts)

    return prompt


def build_final_system_prompt(
    global_context: str = "",
    client_context: str = "",
    base_prompt: Optional[str] = None,
    audience: str = DEFAULT_AUDIENCE,
) -> str:
    """Builds the system prompt for final summaries with optional context.

    Args:
        global_context: Company-wide context to include.
        client_context: Client-specific context to include.
        base_prompt: Override for the base system prompt.
        audience: Target audience level (technical, developer, mixed, business, general).

    Returns:
        Complete system prompt with context.
    """
    prompt = base_prompt if base_prompt else FINAL_SUMMARY_SYSTEM_PROMPT

    # Insert audience guidance
    audience_guidance = AUDIENCE_LEVELS.get(audience, AUDIENCE_LEVELS[DEFAULT_AUDIENCE])
    prompt = prompt.replace("{audience_guidance}", audience_guidance)

    context_parts = []
    if global_context:
        context_parts.append(f"Company Background:\n{global_context}")
    if client_context:
        context_parts.append(f"Client Context:\n{client_context}")

    if context_parts:
        prompt += "\n\n---\n\n" + "\n\n".join(context_parts)

    return prompt


FINAL_SUMMARY_SYSTEM_PROMPT = """You are an expert at creating comprehensive development activity reports.
Given summaries of multiple time periods, create a cohesive narrative that captures the full scope
of work accomplished.

IMPORTANT: This report is CLIENT-FACING. Write it as a professional summary for the client,
NOT for internal use. Avoid any language that references "our company" or the consulting
company, or discusses why work would be interesting from the consulting company's perspective.

AUDIENCE: {audience_guidance}

Structure your response as follows:

## Executive Summary
2-3 sentences capturing the overall theme and scale of work delivered.

## Key Achievements & Features
A numbered list of the most significant accomplishments, features shipped, or milestones reached.
For each item, explain what it does and why it's valuable. Be specific with project names,
version numbers, and measurable outcomes where possible.

## Technology & Language Trends
What technologies were most used, any shifts in focus.

## Project Focus Areas
Which projects received the most attention and why.

## Development Highlights
Notable engineering decisions, optimizations, or architectural improvements.

## Suggested Blog Posts
Suggest 3-5 potential blog post topics based on the technical work completed. These should be
ideas the CLIENT could publish to showcase their product development. For each suggestion include:
- A catchy title
- 1-2 sentence description of what the post would cover
Write these from the client's perspective, not the consulting company's.

Be specific and cite actual projects, technologies, and accomplishments from the summaries provided."""

# Internal final summary (for personal/team use without clients)
INTERNAL_FINAL_SUMMARY_PROMPT = """You are an expert at creating comprehensive development activity reports.
Given summaries of multiple time periods, create a cohesive narrative that captures the full scope
of work accomplished.

This is an INTERNAL summary for the developer or team. Be direct and comprehensive.

Structure your response as follows:

## Overview
2-3 sentences capturing the overall theme and scale of work delivered.

## Key Achievements & Features
A numbered list of the most significant accomplishments, features shipped, or milestones reached.
Be specific with project names, version numbers, and measurable outcomes where possible.

## Technology & Language Trends
What technologies were most used, any shifts in focus.

## Project Focus Areas
Which projects received the most attention and why.

## Development Highlights
Notable engineering decisions, optimizations, or architectural improvements.

## Suggested Blog Posts
Suggest 3-5 potential blog post topics based on the technical work completed. For each:
- A catchy title
- 1-2 sentence description of what it would cover

Be specific and cite actual projects, technologies, and accomplishments from the summaries provided."""

# Multi-client internal summary (for consultancies aggregating all client work)
INTERNAL_SUMMARY_SYSTEM_PROMPT = """You are an expert at creating internal company activity reports.
You are summarizing all work done across multiple clients/projects for an INTERNAL audience
at the consulting company. The goal is to showcase the company's capabilities and
accomplishments to internal stakeholders.

Structure your response as follows:

## Company Year in Review

A narrative overview (3-4 paragraphs) of everything the company accomplished this period.
Emphasize the breadth and depth of technical capabilities demonstrated.

## Client Highlights

For each client, provide a 2-3 sentence highlight of the most impactful work delivered.

## Technical Capabilities Demonstrated

List the key technical skills and domains the work showcases:
- Embedded systems, mobile development, backend, etc.
- Specific technologies mastered
- Complex problems solved

## Key Metrics

Aggregate statistics across all clients:
- Total commits, lines of code
- Number of projects/clients served
- Technologies used

## Suggested Blog Posts

Create a comprehensive list of 10-15 blog post ideas. Review the blog post suggestions from
each client summary provided and include the best ones, plus add new cross-cutting ideas that
span multiple clients or highlight unique technical challenges.

For each post include:
- A catchy, SEO-friendly title
- 2-3 sentence description of what it would cover
- Which client work it draws from (can be anonymized as "a music tech client", "an IoT project", etc.)
- Why it would resonate with readers

Organize the suggestions into categories:
1. **Technical Deep-Dives** - Detailed explorations of specific implementations
2. **Problem-Solving Stories** - Interesting challenges and how they were solved
3. **Best Practices & Patterns** - Reusable approaches discovered during client work
4. **Technology Comparisons** - Insights from working with different tools/frameworks

Focus on posts that demonstrate expertise without revealing confidential client details."""

# Public-facing summary (for blog posts, annual reports, social media)
PUBLIC_SUMMARY_SYSTEM_PROMPT = """You are an expert at writing engaging public announcements about software development.
Create a polished, public-facing summary of the year's work suitable for a blog post or annual report.

IMPORTANT: This is PUBLIC content. Do NOT mention specific client names or confidential details.
Anonymize client references (e.g., "a music technology company", "an IoT startup", "a healthcare client").

Write in an engaging, professional tone that showcases capabilities and achievements.

Structure your response as follows:

## Year in Review: {year}

An engaging 2-3 paragraph introduction summarizing the year's themes and scale of work.
Mention the breadth of projects, technologies, and industries served.

## Key Achievements

5-7 bullet points highlighting the most impressive accomplishments. Be specific about
technical achievements but anonymize client details. Focus on outcomes and impact.

## Technologies & Expertise

A summary of the key technologies, frameworks, and domains worked with.
Organize by category (e.g., Mobile, Embedded, Backend, etc.).

## By the Numbers

Key statistics that showcase productivity:
- Projects delivered
- Lines of code
- Technologies used
- Any other impressive metrics

## Looking Ahead

1-2 paragraphs about capabilities demonstrated and areas of expertise.
End on a forward-looking note.

Keep the tone professional but approachable. This should be suitable for LinkedIn, a company blog,
or an annual report. Avoid technical jargon that wouldn't resonate with a general audience."""


@dataclass
class CostTracker:
    """Tracks LLM API costs across multiple calls.

    Attributes:
        total_input_tokens: Total input tokens used.
        total_output_tokens: Total output tokens used.
        total_cost: Total estimated cost in USD.
        calls: Number of API calls made.
    """

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    calls: int = 0

    def add(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        """Adds usage from a single API call.

        Args:
            input_tokens: Number of input tokens used.
            output_tokens: Number of output tokens generated.
            cost: Cost of this call in USD.
        """
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost
        self.calls += 1

    def summary(self) -> str:
        """Returns a formatted summary of costs.

        Returns:
            Human-readable cost summary string.
        """
        return (
            f"API calls: {self.calls}, "
            f"Input tokens: {format_number(self.total_input_tokens)}, "
            f"Output tokens: {format_number(self.total_output_tokens)}, "
            f"Total cost: ${self.total_cost:.4f}"
        )


def print_separator(width: int = 60) -> None:
    """Prints a separator line.

    Args:
        width: Width of the separator line in characters.
    """
    print("=" * width, file=sys.stderr)


def print_heading(title: str, width: int = 60) -> None:
    """Prints a section heading with separator lines.

    Args:
        title: The heading text to display.
        width: Width of the separator lines in characters.
    """
    print_separator(width)
    print(title, file=sys.stderr)
    print_separator(width)


def call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    cost_tracker: CostTracker,
    max_cost: float,
) -> str:
    """Calls the LLM via LiteLLM and tracks costs.

    Args:
        model: LiteLLM model string (e.g., 'gpt-4o-mini').
        system_prompt: System prompt to set context.
        user_prompt: User prompt with the actual request.
        temperature: Sampling temperature.
        cost_tracker: CostTracker instance to update.
        max_cost: Maximum allowed total cost in USD.

    Returns:
        The LLM's response text.

    Raises:
        SystemExit: If max_cost would be exceeded.
    """
    try:
        from litellm import completion  # pyright: ignore[reportMissingImports]
    except ImportError as err:
        raise SystemExit("litellm is required. Install with: pip install litellm") from err

    # Check if we're approaching budget limit
    if cost_tracker.total_cost >= max_cost:
        raise SystemExit(
            f"Budget limit reached (${max_cost:.2f}). Current spend: ${cost_tracker.total_cost:.4f}"
        )

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

    # LiteLLM provides cost tracking
    cost = 0.0
    if hasattr(response, "_hidden_params"):
        cost = response._hidden_params.get("response_cost", 0.0) or 0.0

    cost_tracker.add(input_tokens, output_tokens, cost)

    # Check if we've exceeded budget after this call
    if cost_tracker.total_cost > max_cost:
        print(
            f"Warning: Budget exceeded (${cost_tracker.total_cost:.4f} > ${max_cost:.2f})",
            file=sys.stderr,
        )

    return response.choices[0].message.content


def gather_period_data(
    repos: list[str],
    start_date: _dt.date,
    end_date: _dt.date,
    author: str,
    include_diffs: bool,
    max_diff_lines: int,
    exclude_config: Optional[ExcludeConfig] = None,
) -> tuple[PeriodStats, list[CommitInfo]]:
    """Gathers statistics and commit data for a time period.

    Args:
        repos: List of repository paths to process.
        start_date: First day of the period.
        end_date: Last day of the period.
        author: Author filter for git commands.
        include_diffs: Whether to include diff content.
        max_diff_lines: Maximum diff lines per commit.
        exclude_config: Optional exclusion patterns.

    Returns:
        Tuple of (PeriodStats, list of CommitInfo).
    """
    # Get statistics using existing function
    summaries = process_repos_for_period(repos, start_date, end_date, author, exclude_config)

    # Get period label
    if (
        start_date.month == 1
        and start_date.day == 1
        and end_date.month == 12
        and end_date.day == 31
        and start_date.year == end_date.year
    ):
        label = str(start_date.year)
    else:
        label = f"{start_date.isoformat()} to {end_date.isoformat()}"

    stats = aggregate_period_stats(label, start_date, end_date, summaries)

    # Gather commit info with optional diffs
    since_str, until_str = date_range_to_git_args(start_date, end_date)
    all_commits: list[CommitInfo] = []
    seen_shas: set[str] = set()

    for repo_path in repos:
        # Process main repo
        if include_diffs:
            commits = get_commits_with_diffs(
                repo_path, since_str, until_str, author, max_diff_lines
            )
        else:
            commits = get_commit_messages(repo_path, since_str, until_str, author)

        for c in commits:
            if c.sha not in seen_shas:
                seen_shas.add(c.sha)
                all_commits.append(c)

        # Process submodules
        for sub_path in discover_all_submodules(repo_path):
            if include_diffs:
                sub_commits = get_commits_with_diffs(
                    sub_path, since_str, until_str, author, max_diff_lines
                )
            else:
                sub_commits = get_commit_messages(sub_path, since_str, until_str, author)

            for c in sub_commits:
                if c.sha not in seen_shas:
                    seen_shas.add(c.sha)
                    all_commits.append(c)

    return stats, all_commits


def format_period_prompt(
    stats: PeriodStats,
    commits: list[CommitInfo],
    include_diffs: bool,
) -> str:
    """Formats period data into a prompt for the LLM.

    Args:
        stats: Period statistics.
        commits: List of commits in the period.
        include_diffs: Whether to include diff content.

    Returns:
        Formatted prompt string.
    """
    lines = [
        f"# Git Activity: {stats.period_label}",
        f"Period: {stats.start_date} to {stats.end_date}",
        "",
        "## Statistics",
        f"- Commits: {stats.commits}",
        f"- Lines added: {format_number(stats.lines_added)}",
        f"- Lines removed: {format_number(stats.lines_removed)}",
        f"- Files changed: {format_number(stats.files_changed)}",
        f"- Active days: {stats.active_days}",
        f"- Projects active: {stats.projects_active}",
        "",
    ]

    # Add language breakdown
    if stats.languages:
        lines.append("## Languages")
        sorted_langs = sorted(stats.languages.values(), key=lambda x: x.lines_added, reverse=True)[
            :10
        ]
        for lang in sorted_langs:
            lines.append(
                f"- {lang.name}: +{format_number(lang.lines_added)}/-{format_number(lang.lines_removed)}"
            )
        lines.append("")

    # Add project breakdown
    if stats.project_summaries:
        lines.append("## Projects")
        sorted_projects = sorted(
            stats.project_summaries, key=lambda x: x.commit_count, reverse=True
        )[:15]
        for proj in sorted_projects:
            top_lang = get_primary_language(proj.languages)
            lines.append(
                f"- {proj.project_name}: {proj.commit_count} commits, "
                f"+{format_number(proj.lines_added)}/-{format_number(proj.lines_removed)} ({top_lang})"
            )
        lines.append("")

    # Add commit messages
    if commits:
        lines.append("## Commits")
        # Sort by date
        sorted_commits = sorted(commits, key=lambda c: c.author_date)
        for c in sorted_commits:
            lines.append(f"- [{c.author_date[:10]}] {c.subject}")
            if c.body:
                # Include first few lines of body
                body_lines = c.body.split("\n")[:3]
                for bl in body_lines:
                    if bl.strip():
                        lines.append(f"  {bl.strip()}")
        lines.append("")

    # Add diffs if requested
    if include_diffs and commits:
        lines.append("## Code Changes (Diffs)")
        for c in sorted_commits[:20]:  # Limit to first 20 commits for diffs
            if c.diff:
                lines.append(f"### {c.subject} ({c.sha[:8]})")
                lines.append("```diff")
                # Truncate individual diffs if too long
                diff_lines = c.diff.split("\n")[:100]
                lines.extend(diff_lines)
                if len(c.diff.split("\n")) > 100:
                    lines.append("... (diff truncated)")
                lines.append("```")
                lines.append("")

    return "\n".join(lines)


def format_stats_summary(stats: PeriodStats) -> str:
    """Formats period statistics as a compact summary string.

    Args:
        stats: Period statistics.

    Returns:
        Formatted stats string.
    """
    lines = [
        f"**Stats:** {stats.commits} commits, "
        f"+{format_number(stats.lines_added)}/-{format_number(stats.lines_removed)} lines, "
        f"{stats.files_changed} files, {stats.active_days} active days",
    ]

    # Top languages
    if stats.languages:
        sorted_langs = sorted(stats.languages.values(), key=lambda x: x.lines_added, reverse=True)[
            :5
        ]
        lang_strs = [f"{lang.name} (+{format_number(lang.lines_added)})" for lang in sorted_langs]
        lines.append(f"**Languages:** {', '.join(lang_strs)}")

    # Top projects
    if stats.project_summaries:
        sorted_projects = sorted(
            stats.project_summaries, key=lambda x: x.commit_count, reverse=True
        )[:5]
        proj_strs = [f"{p.project_name} ({p.commit_count})" for p in sorted_projects]
        lines.append(f"**Projects:** {', '.join(proj_strs)}")

    return "\n".join(lines)


def aggregate_all_period_stats(
    period_summaries: list[tuple[str, PeriodStats, str]],
    label: str,
) -> PeriodStats:
    """Aggregates statistics from multiple periods into a single summary.

    Args:
        period_summaries: List of (period_label, stats, summary_text) tuples.
        label: Label for the aggregated period.

    Returns:
        Aggregated PeriodStats.
    """
    if not period_summaries:
        return PeriodStats(
            period_label=label, start_date=_dt.date.today(), end_date=_dt.date.today()
        )

    # Get date range
    all_stats = [s for _, s, _ in period_summaries]
    start_date = min(s.start_date for s in all_stats)
    end_date = max(s.end_date for s in all_stats)

    # Sum basic stats
    total_commits = sum(s.commits for s in all_stats)
    total_lines_added = sum(s.lines_added for s in all_stats)
    total_lines_removed = sum(s.lines_removed for s in all_stats)
    total_files_changed = sum(s.files_changed for s in all_stats)
    total_active_days = sum(s.active_days for s in all_stats)

    # Aggregate languages
    lang_totals: dict[str, LanguageStats] = {}
    for s in all_stats:
        if s.languages:
            for lang_name, lang_stats in s.languages.items():
                if lang_name not in lang_totals:
                    lang_totals[lang_name] = LanguageStats(name=lang_name)
                lang_totals[lang_name].lines_added += lang_stats.lines_added
                lang_totals[lang_name].lines_removed += lang_stats.lines_removed
                lang_totals[lang_name].files_changed += lang_stats.files_changed

    # Aggregate projects
    proj_totals: dict[str, ProjectSummary] = {}
    for s in all_stats:
        if s.project_summaries:
            for proj in s.project_summaries:
                if proj.project_name not in proj_totals:
                    proj_totals[proj.project_name] = ProjectSummary(
                        project_name=proj.project_name,
                        project_path=proj.project_path,
                        commit_count=0,
                        lines_added=0,
                        lines_removed=0,
                        files_changed=0,
                        languages={},
                    )
                proj_totals[proj.project_name].commit_count += proj.commit_count
                proj_totals[proj.project_name].lines_added += proj.lines_added
                proj_totals[proj.project_name].lines_removed += proj.lines_removed

    # Determine top language
    top_lang = ""
    top_lang_lines = 0
    if lang_totals:
        top = max(lang_totals.values(), key=lambda x: x.lines_added)
        top_lang = top.name
        top_lang_lines = top.lines_added

    return PeriodStats(
        period_label=label,
        start_date=start_date,
        end_date=end_date,
        commits=total_commits,
        lines_added=total_lines_added,
        lines_removed=total_lines_removed,
        files_changed=total_files_changed,
        active_days=total_active_days,
        longest_streak=0,  # Can't aggregate streaks meaningfully
        top_language=top_lang,
        top_language_lines=top_lang_lines,
        projects_active=len(proj_totals),
        languages=lang_totals,
        project_summaries=list(proj_totals.values()),
    )


def format_final_prompt(period_summaries: list[tuple[str, PeriodStats, str]]) -> str:
    """Formats period summaries into a final aggregation prompt.

    Args:
        period_summaries: List of (period_label, stats, summary_text) tuples.

    Returns:
        Formatted prompt string.
    """
    lines = [
        "# Development Activity Summary",
        "",
        f"The following are summaries of {len(period_summaries)} time periods.",
        "Please create a comprehensive narrative combining all of this activity.",
        "",
    ]

    for label, stats, summary in period_summaries:
        lines.append(f"## {label}")
        lines.append(format_stats_summary(stats))
        lines.append("")
        lines.append(summary)
        lines.append("")

    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the CLI tool.

    Args:
        argv: Optional sequence of command-line arguments.

    Returns:
        Process exit code: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Summarize git activity using LLM-powered hierarchical summarization. "
            "Supports multiple LLM providers via LiteLLM."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 2024                                      # Uses git config user.name
  %(prog)s 2024 --author "@example.com"              # Match by email domain
  %(prog)s 2024 --model gemini/gemini-2.0-flash
  %(prog)s 2020:2025 --granularity year

Models (LiteLLM format):
  gpt-4o-mini                              OpenAI (cheapest, default)
  gpt-4o                                   OpenAI (balanced)
  gemini/gemini-2.0-flash                  Google (fast, 1M context)
  gemini/gemini-2.0-flash-lite             Google (cheapest Gemini)
  anthropic/claude-haiku-4-5-20241022      Anthropic Haiku 4.5
  anthropic/claude-sonnet-4-5-20250929     Anthropic Sonnet 4.5

Environment variables for API keys:
  OPENAI_API_KEY      For OpenAI models
  GEMINI_API_KEY      For Google Gemini models
  ANTHROPIC_API_KEY   For Anthropic models
        """,
    )
    parser.add_argument(
        "period",
        nargs="?",
        help=(
            "Time period to analyze. Formats: YYYY (year), YYYY-QN (quarter), "
            "YYYY-MM (month), YYYY-WNN (week), or START:END for ranges."
        ),
    )
    add_author_arg(parser)
    parser.add_argument(
        "--list-models",
        nargs="?",
        const="all",
        metavar="PROVIDER",
        help="List available models. Optionally filter by provider (openai, anthropic, gemini).",
    )
    parser.add_argument(
        "--client",
        metavar="NAME",
        help="Override automatic client detection with explicit client name.",
    )
    add_config_arg(parser)
    parser.add_argument(
        "--no-client-grouping",
        action="store_true",
        help="Disable client-based grouping (single combined output).",
    )
    add_root_arg(parser)
    parser.add_argument(
        "--granularity",
        choices=["year", "quarter", "month", "week"],
        default="month",
        help="Granularity for summarization periods (default: month).",
    )
    add_model_args(parser, DEFAULT_MODEL)
    # Override temperature default for summarization
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
        help="Show data that would be sent without calling LLM.",
    )
    parser.add_argument(
        "--include-diffs",
        action="store_true",
        default=True,
        help="Include code diffs in summaries (default: true).",
    )
    parser.add_argument(
        "--no-diffs",
        action="store_true",
        help="Exclude code diffs from summaries.",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=500,
        help="Maximum diff lines per commit (default: 500).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file path. Default: output/[client/]summary-<period>.md",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write output to stdout instead of files.",
    )
    add_output_dir_arg(parser)
    add_fetch_arg(parser)
    add_exclude_args(parser)
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Filter repositories by name pattern.",
    )
    parser.add_argument(
        "--summaries-only",
        action="store_true",
        help=(
            "Only regenerate internal/public summaries from existing client markdown files. "
            "Skips LLM processing of individual periods - useful for changing disclosure settings."
        ),
    )
    parser.add_argument(
        "--no-html",
        action="store_true",
        help="Skip HTML report generation (HTML is generated by default).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the HTML report in browser after generation.",
    )

    args = parser.parse_args(argv)

    # Handle --list-models
    if args.list_models:
        provider = None if args.list_models == "all" else args.list_models
        list_available_models(provider)
        return 0

    # Validate required arguments for normal operation
    if not args.period:
        parser.error("period is required (unless using --list-models)")

    resolve_author(args, parser)

    root = os.path.abspath(args.root)

    # Handle --no-diffs flag
    include_diffs = args.include_diffs and not args.no_diffs

    # Load unified configuration
    config_file = get_config_path(args.config)

    # Load API keys from config (if not already in environment)
    load_api_keys_from_config(config_file)

    file_client_config, file_exclude_config, file_prompt_config, file_public_config = load_config(
        str(config_file)
    )
    if file_client_config or file_exclude_config or file_prompt_config:
        print(f"Loaded config from: {config_file}", file=sys.stderr)

    # Build exclude configuration
    exclude_config = ExcludeConfig()

    if not args.no_default_excludes:
        exclude_config.global_patterns.extend(DEFAULT_EXCLUDE_PATTERNS)

    exclude_config.global_patterns.extend(args.exclude)

    # Add excludes from config file
    if file_exclude_config and not args.no_excludes_file:
        exclude_config.global_patterns.extend(file_exclude_config.global_patterns)
        for project, patterns in file_exclude_config.project_patterns.items():
            if project not in exclude_config.project_patterns:
                exclude_config.project_patterns[project] = []
            exclude_config.project_patterns[project].extend(patterns)

    # Legacy: also check for separate excludes file if specified
    if args.excludes_file and os.path.isfile(args.excludes_file):
        legacy_config = load_excludes_file(args.excludes_file)
        exclude_config.global_patterns.extend(legacy_config.global_patterns)
        for project, patterns in legacy_config.project_patterns.items():
            if project not in exclude_config.project_patterns:
                exclude_config.project_patterns[project] = []
            exclude_config.project_patterns[project].extend(patterns)
        print(f"Loaded excludes from: {args.excludes_file}", file=sys.stderr)

    # Discover repositories
    all_repos = discover_top_level_repos(root)
    if not all_repos:
        print(f"No git repositories found under: {root}", file=sys.stderr)
        return 1

    # Filter repositories by name if requested
    if args.filter:
        import fnmatch

        filtered_repos = []
        for repo in all_repos:
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
        all_repos = filtered_repos
        if not all_repos:
            print(f"No repositories matched filter(s): {', '.join(args.filter)}", file=sys.stderr)
            return 1
        print(f"Filtered to {len(all_repos)} repositories", file=sys.stderr)

    # Use client configuration from unified config (only if clients are defined)
    client_config: Optional[ClientConfig] = None
    has_clients = (
        file_client_config is not None
        and file_client_config.clients
        and not args.no_client_grouping
    )
    if has_clients:
        client_config = file_client_config
        print(f"Clients: {', '.join(client_config.clients.keys())}", file=sys.stderr)

    # Group repos by client or use explicit --client
    if args.client:
        # Filter to only repos matching the specified client
        if client_config and args.client in client_config.clients:
            # Use client config to filter repos
            all_categorized = client_config.categorize_repos(all_repos)
            matching_repos = all_categorized.get(args.client, [])
            repos_by_client = {args.client: matching_repos}
        else:
            # No config or unknown client - use all repos with that client name
            repos_by_client = {args.client: all_repos}
    elif has_clients:
        repos_by_client = client_config.categorize_repos(all_repos)
        # Show categorization
        for client, client_repos in repos_by_client.items():
            repo_names = [os.path.basename(r) for r in client_repos]
            print(
                f"  {client}: {len(client_repos)} repos ({', '.join(repo_names[:5])}{'...' if len(repo_names) > 5 else ''})",
                file=sys.stderr,
            )
    else:
        # No client grouping - single internal-facing output
        repos_by_client = {None: all_repos}

    # Fetch repos if --fetch specified
    if args.fetch:
        _, fetch_success = fetch_repos_with_progress(
            all_repos,
            include_submodules=True,
            max_workers=8,
            error_on_failure=False,
            output=sys.stderr,
        )

    # Determine periods to process
    is_range = ":" in args.period and not re.match(
        r"^\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$", args.period
    )

    if is_range:
        periods = parse_range(args.period, args.granularity)
    else:
        # Single period - break it down by granularity for hierarchical summarization
        label, start, end = parse_period(args.period)
        periods = _generate_subperiods(start, end, args.granularity)

    print(
        f"Processing {len(periods)} periods with granularity: {args.granularity}", file=sys.stderr
    )
    print(f"Model: {args.model}", file=sys.stderr)
    print(f"Max cost: ${args.max_cost:.2f}", file=sys.stderr)
    print(f"Include diffs: {include_diffs}", file=sys.stderr)
    print("", file=sys.stderr)

    # Initialize global cost tracking
    total_cost_tracker = CostTracker()

    # Collect all client results for internal summary
    all_client_results: list[tuple[str, PeriodStats, str]] = []

    # Handle --summaries-only mode: read existing markdown instead of processing
    if args.summaries_only:
        print("Summaries-only mode: reading existing client markdown files...", file=sys.stderr)
        base_output_dir = get_output_dir(
            output_dir=args.output_dir,
            period=args.period.split(":")[0] if ":" in args.period else args.period,
        )

        for client_name in repos_by_client:
            if client_name is None:
                client_slug = None
                summary_path = base_output_dir / f"summary-{args.period.replace(':', '-to-')}.md"
            else:
                import re as re_module

                client_slug = re_module.sub(r"[^\w\-]", "_", client_name.lower())
                summary_path = (
                    base_output_dir / client_slug / f"summary-{args.period.replace(':', '-to-')}.md"
                )

            if summary_path.exists():
                content = summary_path.read_text()
                # Create placeholder stats from file (parse if possible, or use zeros)
                stats = PeriodStats(
                    period_label=args.period,
                    start_date=_dt.date.min,
                    end_date=_dt.date.max,
                )
                # Try to extract stats from content
                # Format: **Stats:** 1003 commits, +177,557/-94,958 lines, 3582 files, 143 active days
                import re as re_module

                stats_match = re_module.search(
                    r"\*\*Stats:\*\*\s*([\d,]+)\s*commits?,\s*\+?([\d,]+)\s*/\s*-?([\d,]+)\s*lines?,\s*([\d,]+)\s*files?,\s*([\d,]+)\s*active",
                    content,
                )
                if stats_match:
                    stats.commits = int(stats_match.group(1).replace(",", ""))
                    stats.lines_added = int(stats_match.group(2).replace(",", ""))
                    stats.lines_removed = int(stats_match.group(3).replace(",", ""))
                    stats.files_changed = int(stats_match.group(4).replace(",", ""))
                    stats.active_days = int(stats_match.group(5).replace(",", ""))

                display_name = client_name if client_name else "All Projects"
                print(f"  Loaded: {display_name} ({summary_path})", file=sys.stderr)
                all_client_results.append((display_name, stats, content))
            else:
                display_name = client_name if client_name else "All Projects"
                print(f"  Warning: Not found: {summary_path}", file=sys.stderr)

        if not all_client_results:
            print(
                "Error: No existing summaries found. Run without --summaries-only first.",
                file=sys.stderr,
            )
            return 1

        # Skip to internal/public summary generation (handled below)
    else:
        # Normal processing mode
        pass

    # Process each client (skipped in summaries-only mode)
    for client_name, repos in repos_by_client.items():
        if args.summaries_only:
            break  # Skip processing in summaries-only mode
        if not repos:
            continue

        # Determine if this is internal mode (no clients configured)
        is_internal_mode = client_name is None
        client_display = client_name if client_name else "All Projects"
        print_heading(f"Processing: {client_display} ({len(repos)} repos)")

        # Get context and audience for this client
        global_context = ""
        client_context = ""
        client_audience = DEFAULT_AUDIENCE
        if client_config and client_name:
            global_context = client_config.global_context
            client_context = client_config.get_client_context(client_name)
            client_audience = client_config.get_client_audience(client_name)
        elif file_client_config:
            # Use global context even without clients
            global_context = file_client_config.global_context

        # Build system prompts - use internal prompts when no clients
        period_base = file_prompt_config.period_summary if file_prompt_config else None
        final_base = file_prompt_config.final_summary if file_prompt_config else None

        if is_internal_mode:
            # Internal mode: use internal-facing prompts
            period_system_prompt = period_base or INTERNAL_PERIOD_SUMMARY_PROMPT
            final_system_prompt = final_base or INTERNAL_FINAL_SUMMARY_PROMPT
            if global_context:
                period_system_prompt += f"\n\n---\n\nBackground:\n{global_context}"
                final_system_prompt += f"\n\n---\n\nBackground:\n{global_context}"
        else:
            # Client mode: use client-facing prompts with audience
            period_system_prompt = build_period_system_prompt(
                global_context, client_context, period_base, client_audience
            )
            final_system_prompt = build_final_system_prompt(
                global_context, client_context, final_base, client_audience
            )

        # Initialize cost tracking for this client
        cost_tracker = CostTracker()
        period_summaries: list[tuple[str, PeriodStats, str]] = []

        # Process each period
        for i, (label, start, end) in enumerate(periods):
            print(f"[{i + 1}/{len(periods)}] Processing {label}...", file=sys.stderr)

            # Gather data for this period
            stats, commits = gather_period_data(
                repos, start, end, args.author, include_diffs, args.max_diff_lines, exclude_config
            )

            if stats.commits == 0:
                print("  No commits found, skipping.", file=sys.stderr)
                continue

            print(f"  {stats.commits} commits, {len(commits)} with details", file=sys.stderr)

            # Format prompt for this period
            prompt = format_period_prompt(stats, commits, include_diffs)

            if args.dry_run:
                print(f"  [DRY RUN] Would send {len(prompt)} chars to LLM", file=sys.stderr)
                period_summaries.append((label, stats, f"[DRY RUN] {stats.commits} commits"))
                continue

            # Call LLM for period summary
            try:
                summary = call_llm(
                    args.model,
                    period_system_prompt,
                    prompt,
                    args.temperature,
                    cost_tracker,
                    args.max_cost,
                )
                period_summaries.append((label, stats, summary))
                print(
                    f"  Summary generated (${cost_tracker.total_cost:.4f} total)", file=sys.stderr
                )
            except SystemExit as e:
                print(f"  Error: {e}", file=sys.stderr)
                raise

        if not period_summaries:
            print(f"No activity found for {client_display}.", file=sys.stderr)
            continue

        # Generate final summary
        print("", file=sys.stderr)
        print("Generating final summary...", file=sys.stderr)

        final_prompt = format_final_prompt(period_summaries)

        if args.dry_run:
            print(
                f"[DRY RUN] Would send {len(final_prompt)} chars for final summary", file=sys.stderr
            )
            final_output = "*(Dry run - LLM summary would appear here)*\n\n"
            final_output += "## Period Details\n\n"
            for label, stats, summary in period_summaries:
                final_output += f"### {label}\n\n"
                final_output += "#### Overview\n\n"
                final_output += f"{format_stats_summary(stats)}\n\n"
                final_output += f"{summary}\n\n"
        else:
            try:
                final_summary = call_llm(
                    args.model,
                    final_system_prompt,
                    final_prompt,
                    args.temperature,
                    cost_tracker,
                    args.max_cost,
                )
                final_output = final_summary
            except SystemExit as e:
                print(f"Error generating final summary: {e}", file=sys.stderr)
                # Fall back to just concatenating period summaries
                final_output = "*(Summary generation failed - showing period details)*\n\n"
                final_output += "## Period Details\n\n"
                for label, stats, summary in period_summaries:
                    final_output += f"### {label}\n\n"
                    final_output += "#### Overview\n\n"
                    final_output += f"{format_stats_summary(stats)}\n\n"
                    final_output += f"{summary}\n\n"

        # Print cost summary for this client
        print("", file=sys.stderr)
        print(f"Cost for {client_display}: {cost_tracker.summary()}", file=sys.stderr)

        # Add to total cost
        total_cost_tracker.total_input_tokens += cost_tracker.total_input_tokens
        total_cost_tracker.total_output_tokens += cost_tracker.total_output_tokens
        total_cost_tracker.total_cost += cost_tracker.total_cost
        total_cost_tracker.calls += cost_tracker.calls

        # Determine output directory and filenames
        if args.output and len(repos_by_client) == 1:
            # Explicit output path provided (only valid for single client)
            output_path = args.output
            output_dir = os.path.dirname(output_path) or "."
            save_period_summaries = False
        elif args.stdout:
            # Write to stdout
            output_path = None
            output_dir = None
            save_period_summaries = False
        else:
            # Default: save to output directory with sensible names
            output_dir_path = get_output_dir(
                output_dir=args.output_dir,
                period=args.period.split(":")[0] if ":" in args.period else args.period,
                client=client_name,
            )
            output_dir = str(output_dir_path)

            os.makedirs(output_dir, exist_ok=True)

            # Generate filename from period
            output_path = os.path.join(output_dir, f"summary-{args.period.replace(':', '-to-')}.md")
            save_period_summaries = True

        # Save period summaries if using default output
        if save_period_summaries and output_dir and not args.dry_run:
            periods_dir = os.path.join(output_dir, "periods")
            os.makedirs(periods_dir, exist_ok=True)
            for label, stats, summary in period_summaries:
                period_file = os.path.join(periods_dir, f"{label}.md")
                with open(period_file, "w") as f:
                    f.write(f"# Activity Summary: {label}\n\n")
                    if client_name:
                        f.write(f"**Client:** {client_name}\n\n")
                    f.write("## Overview\n\n")
                    f.write(f"{format_stats_summary(stats)}\n\n")
                    f.write("---\n\n")
                    f.write(summary)
            print(f"Period summaries saved to: {periods_dir}/", file=sys.stderr)

        # Calculate aggregated stats for the full period
        total_stats = aggregate_all_period_stats(period_summaries, args.period)

        # Output final result
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w") as f:
                # Add metadata header
                f.write(f"# Activity Summary: {args.period}\n\n")
                if client_name:
                    f.write(f"**Client:** {client_name}\n\n")
                f.write(f"**Author:** {args.author}\n\n")
                f.write(f"**Generated:** {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                f.write(f"**Cost:** {cost_tracker.summary()}\n\n")
                # Add full period stats
                f.write("## Overview\n\n")
                f.write(f"{format_stats_summary(total_stats)}\n\n")
                f.write("---\n\n")
                f.write(final_output)
            print(f"Output written to: {output_path}", file=sys.stderr)
        else:
            if client_name:
                print(f"\n## {client_name}\n")
            print(f"## Overview\n\n{format_stats_summary(total_stats)}\n\n---\n\n")
            print(final_output)

        # Store results for internal summary (exclude "Other" catch-all)
        if client_name and client_name != "Other":
            all_client_results.append((client_name, total_stats, final_output))

        print("", file=sys.stderr)

    # Print total cost summary
    if len(repos_by_client) > 1:
        print_separator()
        print(f"Total cost: {total_cost_tracker.summary()}", file=sys.stderr)

    # Generate internal company summary if multiple clients
    if len(all_client_results) > 1 and not args.stdout:
        print("", file=sys.stderr)
        print_heading("Generating internal company summary...")

        # Format internal summary prompt
        internal_lines = [
            f"# Company Activity Summary: {args.period}",
            "",
            f"This summarizes work across {len(all_client_results)} clients.",
            "",
        ]

        for client_name, client_stats, client_summary in all_client_results:
            internal_lines.append(f"## {client_name}")
            internal_lines.append(format_stats_summary(client_stats))
            internal_lines.append("")
            internal_lines.append(client_summary)
            internal_lines.append("")

        internal_prompt = "\n".join(internal_lines)

        if args.dry_run:
            print(
                f"[DRY RUN] Would send {len(internal_prompt)} chars for internal summary",
                file=sys.stderr,
            )
            internal_output = "*(Dry run - LLM summary would appear here)*\n\n"
            internal_output += "## Client Details\n\n"
            for client_name, client_stats, _ in all_client_results:
                internal_output += f"### {client_name}\n\n"
                internal_output += "#### Overview\n\n"
                internal_output += f"{format_stats_summary(client_stats)}\n\n"
        else:
            try:
                # Use prompt override from config if provided
                internal_base = file_prompt_config.internal_summary if file_prompt_config else None
                internal_system_prompt = internal_base or INTERNAL_SUMMARY_SYSTEM_PROMPT
                internal_output = call_llm(
                    args.model,
                    internal_system_prompt,
                    internal_prompt,
                    args.temperature,
                    total_cost_tracker,
                    args.max_cost,
                )
            except SystemExit as e:
                print(f"Error generating internal summary: {e}", file=sys.stderr)
                internal_output = "*(Summary generation failed - showing client details)*\n\n"
                internal_output += "## Client Details\n\n"
                for client_name, client_stats, client_summary in all_client_results:
                    internal_output += f"### {client_name}\n\n"
                    internal_output += "#### Overview\n\n"
                    internal_output += f"{format_stats_summary(client_stats)}\n\n"
                    internal_output += f"{client_summary}\n\n"

        # Calculate combined stats
        combined_stats = aggregate_all_period_stats(
            [(c, s, o) for c, s, o in all_client_results],
            args.period,
        )

        # Save internal summary
        internal_dir_path = get_output_dir(
            output_dir=args.output_dir,
            period=args.period.split(":")[0] if ":" in args.period else args.period,
        )
        internal_dir = str(internal_dir_path)
        os.makedirs(internal_dir, exist_ok=True)
        internal_path = os.path.join(
            internal_dir, f"internal-summary-{args.period.replace(':', '-to-')}.md"
        )

        with open(internal_path, "w") as f:
            f.write(f"# Internal Company Summary: {args.period}\n\n")
            f.write(f"**Generated:** {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"**Clients:** {', '.join(c for c, _, _ in all_client_results)}\n\n")
            f.write(f"**Cost:** {total_cost_tracker.summary()}\n\n")
            f.write("## Combined Overview\n\n")
            f.write(f"{format_stats_summary(combined_stats)}\n\n")
            f.write("---\n\n")
            f.write(internal_output)

        print(f"Internal summary written to: {internal_path}", file=sys.stderr)
        print(f"Final cost: {total_cost_tracker.summary()}", file=sys.stderr)

    # Generate public-facing summary (for blog posts, annual reports)
    # Check if public summary is enabled
    public_config = file_public_config or PublicSummaryConfig()
    if all_client_results and not args.stdout and public_config.enabled:
        print("", file=sys.stderr)
        print_heading("Generating public-facing summary...")

        # Filter results based on disclosure settings
        included_results = []
        for client_name, client_stats, client_summary in all_client_results:
            disclosure = public_config.get_disclosure(client_name)
            if disclosure.disclosure != "suppress":
                included_results.append((client_name, client_stats, client_summary, disclosure))

        if not included_results:
            print("All clients suppressed from public summary - skipping.", file=sys.stderr)
        else:
            # Format public summary prompt with disclosure-aware client info
            public_lines = [
                f"# Activity Summary: {args.period}",
                "",
                f"Summarizing work across {len(included_results)} projects/clients.",
                "",
            ]

            # Calculate combined stats (only from included clients)
            if len(included_results) == 1:
                _, combined_stats, _, _ = included_results[0]
            else:
                combined_stats = aggregate_all_period_stats(
                    [(c, s, o) for c, s, o, _ in included_results],
                    args.period,
                )

            public_lines.append(f"**Total Stats:** {combined_stats.commits} commits, ")
            public_lines.append(
                f"+{format_number(combined_stats.lines_added)}/-{format_number(combined_stats.lines_removed)} lines"
            )
            public_lines.append("")

            # Add client summaries with disclosure rules
            for i, (client_name, client_stats, client_summary, disclosure) in enumerate(
                included_results, 1
            ):
                if disclosure.disclosure == "full":
                    # Show full client name
                    public_lines.append(f"## {client_name}")
                    public_lines.append(
                        f"[Use the actual client name '{client_name}' in the summary]"
                    )
                else:
                    # Anonymize - use description or generic label
                    anon_label = disclosure.description or f"Project {i}"
                    public_lines.append(f"## {anon_label}")
                    public_lines.append(
                        f"[IMPORTANT: Do NOT mention '{client_name}'. "
                        f"Refer to this as '{anon_label}' only.]"
                    )

                public_lines.append(f"({client_stats.commits} commits)")
                public_lines.append("")
                public_lines.append(client_summary)
                public_lines.append("")

            public_prompt = "\n".join(public_lines)

            # Extract year from period for the prompt
            year = args.period.split("-")[0] if "-" in args.period else args.period
            public_system_prompt = PUBLIC_SUMMARY_SYSTEM_PROMPT.replace("{year}", year)

            if args.dry_run:
                print(
                    f"[DRY RUN] Would send {len(public_prompt)} chars for public summary",
                    file=sys.stderr,
                )
                public_output = "*(Dry run - public summary would appear here)*"
            else:
                try:
                    public_output = call_llm(
                        args.model,
                        public_system_prompt,
                        public_prompt,
                        args.temperature,
                        total_cost_tracker,
                        args.max_cost,
                    )
                except SystemExit as e:
                    print(f"Error generating public summary: {e}", file=sys.stderr)
                    public_output = "*(Summary generation failed)*"

            # Save public summary
            public_dir_path = get_output_dir(
                output_dir=args.output_dir,
                period=args.period.split(":")[0] if ":" in args.period else args.period,
            )
            public_dir = str(public_dir_path)
            os.makedirs(public_dir, exist_ok=True)
            public_path = os.path.join(
                public_dir, f"public-summary-{args.period.replace(':', '-to-')}.md"
            )

            with open(public_path, "w") as f:
                f.write(public_output)

            print(f"Public summary written to: {public_path}", file=sys.stderr)

    # Generate HTML reports by default (unless --no-html)
    if not args.no_html and not args.stdout and not args.dry_run:
        print("", file=sys.stderr)
        print_heading("Generating HTML reports...")

        from code_recap.generate_html_report import main as html_main

        # Determine input and output directories
        base_output = get_output_dir(
            output_dir=args.output_dir,
            period=args.period.split(":")[0] if ":" in args.period else args.period,
        )

        html_args = [
            "--input",
            str(base_output),
            "--output",
            str(base_output / "html"),
        ]
        if args.client:
            html_args.extend(["--client", args.client])

        html_result = html_main(html_args)

        if html_result == 0 and args.open:
            import webbrowser

            index_path = base_output / "html" / "index.html"
            if index_path.exists():
                webbrowser.open(f"file://{index_path.resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
