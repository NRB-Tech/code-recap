# Command Reference

All functionality is accessed through the `code-recap` command with subcommands.

```
code-recap <command> [options]
```

## Commands Overview

| Command | Aliases | Description |
|---------|---------|-------------|
| `summarize` | `report` | LLM-powered activity summaries |
| `daily` | `today` | Daily activity for time logging |
| `stats` | `activity` | Statistics without LLM |
| `html` | — | Convert markdown to HTML |
| `blog` | — | Generate blog posts |
| `commits` | — | List commits for a date |
| `deploy` | — | Deploy HTML reports |
| `git` | `repos` | Repository utilities |
| `init` | — | Initialize configuration |

## Common Options

Most commands share these options:

| Option | Description | Default |
|--------|-------------|---------|
| `--root` | Root directory containing repos | Parent of cwd |
| `--author` | Filter commits by author | git config user.name |
| `--client` | Filter to specific client | All clients |
| `--output-dir` | Base output directory | `output/` |
| `-o, --output` | Explicit output file path | Auto-generated |
| `--stdout` | Write to stdout instead of file | `false` |
| `--filter` | Filter repos by name pattern | All repos |
| `--fetch` | Fetch repos before processing | `false` |
| `--config` | Path to config file | Auto-detected |

---

## `summarize` — LLM-Powered Summaries

Generates narrative summaries using hierarchical LLM summarization.

```bash
code-recap summarize 2025                        # Full year
code-recap summarize 2025-Q3                     # Quarter
code-recap summarize 2025-01:2025-06             # Date range
code-recap summarize 2025 --client "Acme"        # Specific client
code-recap summarize 2025 --open                 # Open in browser
code-recap summarize 2025 --dry-run              # Preview, no API cost
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--granularity` | Period breakdown: `week`, `month`, `quarter`, `year` | `month` |
| `--model` | LLM model to use | `gpt-4o-mini` |
| `--max-cost` | Budget limit in USD | `1.00` |
| `--summaries-only` | Regenerate summaries from existing markdown | `false` |
| `--no-html` | Skip HTML generation | `false` |
| `--open` | Open HTML in browser | `false` |
| `--dry-run` | Preview without API calls | `false` |
| `--list-models` | Show available models and exit | — |

### Period Formats

| Format | Example | Description |
|--------|---------|-------------|
| Year | `2025` | Full year |
| Quarter | `2025-Q3` | Q1-Q4 |
| Month | `2025-06` | Single month |
| Range | `2025-01:2025-06` | Start to end (inclusive) |

---

## `daily` — Daily Time Logging

Summarizes commits for a specific date—ideal for time tracking.

```bash
code-recap daily                       # Today
code-recap daily --date yesterday
code-recap daily --date -2             # 2 days ago
code-recap daily --date 2025-01-15     # Specific date
code-recap daily --no-llm              # Just list commits
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--date` | Target date (YYYY-MM-DD, "yesterday", or -N) | Today |
| `--no-llm` | Skip LLM, just list commits | `false` |
| `--model` | LLM model to use | `gpt-4o-mini` |

---

## `stats` — Statistics & CSV Export

Generates detailed statistics without LLM calls.

```bash
code-recap stats 2025                              # Current year
code-recap stats 2020:2025 --granularity year      # Multi-year
code-recap stats 2025-Q3 --format csv              # CSV export
code-recap stats 2025 --format markdown            # Markdown table
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--granularity` | Breakdown: `week`, `month`, `quarter`, `year` | `month` |
| `--format` | Output: `text`, `markdown`, `csv` | `text` |

### Output Includes

- Commit counts per period
- Lines added/removed
- Per-language breakdown
- Per-project statistics
- Active days and coding streaks

---

## `html` — Branded HTML Reports

Converts markdown summaries to styled HTML with your branding.

```bash
code-recap html                        # Generate all
code-recap html --client "Acme"        # Specific client
code-recap html --open                 # Open in browser
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--client` | Generate for specific client | All clients |
| `--open` | Open in browser after generation | `false` |
| `--input-dir` | Input directory with markdown | `output/` |

---

## `blog` — AI Blog Post Generator

Two-stage pipeline: research commits, then generate a polished post.

```bash
# Full pipeline (research + write)
code-recap blog full "Building a Real-Time LED Controller" --period 2025-09

# Step-by-step (allows editing research before writing)
code-recap blog research "My Topic" --period 2025-Q3
# Edit output/blog/my-topic/research.md if needed
code-recap blog write output/blog/my-topic/research.md
```

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `research` | Analyze commits and gather material |
| `write` | Generate blog post from research |
| `full` | Research + write in one step |

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--period` | Time period to analyze | Required |
| `--model` | LLM model | `gpt-4o-mini` |
| `--client` | Filter to specific client | All |

---

## `commits` — Daily Commit Log

Lists all commits for a specific date across repositories.

```bash
code-recap commits 2025-01-15
code-recap commits $(date +%Y-%m-%d)   # Today
code-recap commits yesterday
```

### Output

Shows commits grouped by repository with:
- Commit hash (short)
- Author
- Timestamp
- Commit message

---

## `deploy` — Deploy Reports

Deploys HTML reports to sharing providers.

```bash
code-recap deploy --client acme --provider zip
code-recap deploy --client "Beta Inc" --provider cloudflare
code-recap deploy --all --provider zip
code-recap deploy --list-providers
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `--client, -c` | Deploy specific client | — |
| `--all, -a` | Deploy all clients | — |
| `--provider, -p` | Provider to use | Required |
| `--input-dir` | HTML directory | `output/html/` |
| `--list-providers` | Show available providers | — |

### Providers

| Provider | Description |
|----------|-------------|
| `zip` | Create zip file for manual sharing |
| `cloudflare` | Deploy to Cloudflare Pages |

See [Deployment Guide](deployment.md) for configuration and custom providers.

---

## `git` — Repository Management

Utilities for managing multiple repositories.

```bash
code-recap git fetch                           # Fetch all in parallel
code-recap git archive --days 365              # Archive inactive (dry run)
code-recap git archive --days 365 --execute    # Actually archive
code-recap git unarchive my-project --execute  # Restore from archive
```

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `fetch` | Fetch all repositories in parallel |
| `archive` | Move inactive repos to archive folder |
| `unarchive` | Restore repos from archive |

### Archive Options

| Option | Description | Default |
|--------|-------------|---------|
| `--days` | Inactivity threshold | 365 |
| `--execute` | Actually perform (else dry run) | `false` |
| `--archive-dir` | Archive folder name | `_archive` |

---

## `init` — Initialize Configuration

Creates a configuration file and prompts for API keys.

```bash
code-recap init                # Interactive setup
code-recap init --force        # Overwrite existing config
```

Creates `config/config.yaml` with commented examples.
