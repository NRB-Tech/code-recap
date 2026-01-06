# Configuration Guide

Code Recap works without configuration, but a config file enables multi-client organization, branding, and advanced features.

## Quick Start

```bash
# Create a template config file
code-recap init

# Or copy the full example
cp config.example/config.yaml config/config.yaml
```

## Configuration File Locations

Code Recap searches for `config.yaml` in:

1. `./config/config.yaml` (project directory)
2. `~/.config/code-recap/config.yaml` (user directory)
3. Path specified via `--config` flag

## API Keys

API keys can be set in `config.yaml` or as environment variables (which take precedence):

```yaml
api_keys:
  openai: "sk-..."
  gemini: "AI..."
  anthropic: "sk-ant-..."
```

Or via environment:

```bash
export OPENAI_API_KEY='sk-...'
export GEMINI_API_KEY='...'
export ANTHROPIC_API_KEY='sk-ant-...'
```

Get keys from:
- [OpenAI](https://platform.openai.com/api-keys)
- [Google Gemini](https://aistudio.google.com/apikey)
- [Anthropic](https://console.anthropic.com/settings/keys)

## Client Configuration

Group repositories by client using directory pattern matching:

```yaml
clients:
  "Acme Corp":
    directories:
      - "acme-*"           # Glob: matches acme-firmware, acme-ios
      - "AcmeSDK"          # Exact match
    exclude:
      - "*-legacy"         # Exclude from this client
    audience: technical    # Summary language complexity
    context: |
      Acme Corp is a consumer electronics company. We develop their
      flagship product's firmware and companion mobile apps.

  "Beta Inc":
    directories:
      - "beta-*"
    audience: business     # Non-technical stakeholders

  Personal:
    directories:
      - "dotfiles"
      - "my-*"
```

### Pattern Matching Rules

- `directories`: Glob patterns for repository directory names
- `exclude`: Patterns to exclude (takes precedence over `directories`)
- Patterns support `*`, `?`, and `[seq]` wildcards
- Use `*keyword*` for substring matching
- Matching is case-insensitive
- First match wins (order matters in YAML)
- Unmatched repositories go to "Other"

### Audience Levels

The `audience` setting controls LLM summary language:

| Level | Description |
|-------|-------------|
| `technical` | Deep technical knowledge assumed, precise terminology |
| `developer` | Developer, but may not know domain specifics |
| `mixed` | Varying technical knowledge (default) |
| `business` | Business stakeholder, focus on value and outcomes |
| `general` | Minimal technical background, plain language |

### Default Client

Assign unmatched repositories to a specific client instead of "Other":

```yaml
default_client: Personal
```

## Global Context

Provide company background for LLM summaries:

```yaml
global_context: |
  Acme Consulting specializes in IoT, mobile apps, and embedded systems.
  We focus on quality, maintainability, and field-updatable products.
```

## Exclusion Patterns

Exclude files from line count statistics:

```yaml
excludes:
  # Global exclusions (all projects)
  global:
    - "*.hex"
    - "*.xcodeproj/*"
    - "node_modules/*"
    - "package-lock.json"

  # Project-specific exclusions
  projects:
    acme-firmware:
      - "vendor/*"
      - "third_party/*"
```

## HTML Report Styling

Customize the appearance of generated HTML reports:

```yaml
html_report:
  theme: "light"              # "light" or "dark"

  # Your company branding
  company:
    name: "Acme Consulting"
    url: "https://www.acme-consulting.example/"
    logo: "company-logo.svg"  # Relative to config directory

  # Default colors
  defaults:
    accent_primary: "#2196F3"
    accent_secondary: "#1976D2"

  # Per-client styling
  clients:
    "Acme Corp":
      accent_primary: "#4CAF50"
      accent_secondary: "#388E3C"
      logo: "acme-logo.svg"
      icon: "🏭"              # Emoji shown in navigation

    "Beta Inc":
      accent_primary: "#FF9800"
      accent_secondary: "#F57C00"
      icon: "🚀"
      # Override header company for subcontracted work
      company_override:
        name: "Partner Company"
        url: "https://partner.example"
        logo: "partner.svg"
```

## Public Summary Privacy

Control how client information appears in public summaries:

```yaml
public_summary:
  # Default: "full", "anonymize", or "suppress"
  default_disclosure: "anonymize"

  # Per-client overrides
  clients:
    "Open Source Project":
      disclosure: "full"              # Show actual name

    "Acme Corp":
      disclosure: "anonymize"
      description: "a consumer electronics company"

    "Confidential Client":
      disclosure: "suppress"          # Exclude entirely
```

**Disclosure levels:**

| Level | Effect |
|-------|--------|
| `full` | Show actual client name |
| `anonymize` | Replace with description (default) |
| `suppress` | Exclude from public summary |

Regenerate just the public summaries after changing disclosure:

```bash
code-recap summarize 2025 --summaries-only
```

## LLM Prompt Overrides

Override the default system prompts:

```yaml
prompts:
  # Period/monthly summaries (client-facing)
  period_summary: |
    You are an expert at summarizing software development activity.
    Given git activity data, provide a concise summary...

  # Client summary reports (client-facing)
  final_summary: |
    You are an expert at creating comprehensive development reports.
    Create a cohesive narrative that captures the work accomplished...

  # Internal company summary
  internal_summary: |
    You are creating an internal activity report.
    Summarize work done across all clients...
```

## Deployment Configuration

See [Deployment Guide](deployment.md) for provider-specific configuration.

## Full Example

See the complete [example config](../config.example/config.yaml) with all options documented.
