# Code Recap

Generate beautiful activity reports, client summaries, and blog posts from your git history—powered by LLMs.

## What It Does

**Code Recap** helps developers and consultants who work across multiple repositories:

📊 **Year-in-Review Reports** — Generate polished summaries of your work for clients, complete with statistics, achievements, and technology breakdowns. Export as Markdown or HTML.

⏱️ **Daily Time Logging** — Quickly summarize what you worked on today (or any day) for billing and time tracking. No more digging through commits.

✍️ **Blog Post Generation** — Turn your commits into technical blog posts. The AI researches your changes and drafts content with real code examples.

📈 **Activity Statistics** — Track commits, lines changed, languages used, and coding streaks across all your projects.

🗂️ **Multi-Client Organization** — Automatically group repositories by client using pattern matching. Each client gets their own reports.

### 📝 Example Output

See what generated reports look like:

- [**Example HTML Report**](https://nrb-tech.github.io/code-recap/output.example/html/) — Browse the styled HTML version
- [Monthly summary (markdown)](https://github.com/nrb-tech/code-recap/blob/main/output.example/acme_widgets/periods/2024-10.md)
- [Annual client summary (markdown)](https://github.com/nrb-tech/code-recap/blob/main/output.example/acme_widgets/summary-2024.md)

---

## Use Cases

### Year-end report with HTML

```bash
code-recap summarize 2025 --author "Your Name" --html --open
# → Reports saved to ./code-recap-2025/ (markdown + HTML)
```

### Daily time logging

```bash
code-recap daily --author "Your Name" --date yesterday
```

### Blog post from your commits

```bash
code-recap blog full "Building a Custom Protocol" --period 2025-Q3 --author "Your Name"
```

### Multi-year statistics (CSV export)

```bash
code-recap stats 2020:2025 --author "Your Name" --granularity year --format csv
```

---

## How It Works

Run `code-recap` from a directory containing git repositories:

```
~/Documents/Repos/           # Run code-recap here
├── project-a/               # Git repository (scanned)
├── project-b/               # Git repository (scanned)
├── side-project/            # Git repository (scanned)
└── code-recap-2025/         # Generated reports (created)
    ├── summary-2025.md
    └── html/
        └── index.html
```

**No configuration needed for basic use.** Just run the command and get a unified report.

### For Consultants (Optional)

If you work with multiple clients, configure `config/config.yaml` to organize reports by client:

```yaml
clients:
  "Acme Corp":
    directories:
      - "acme-*"           # Matches acme-firmware, acme-ios, etc.
  "Beta Inc":
    directories:
      - "beta-*"
```

This creates separate reports per client in `code-recap-2025/acme_corp/`, etc.

---

## Quick Start

```bash
# Set API key (choose one)
export OPENAI_API_KEY='sk-...'      # For GPT-4o-mini
export GEMINI_API_KEY='...'         # For Gemini Flash
export ANTHROPIC_API_KEY='sk-...'   # For Claude Haiku

# Run directly with uvx (no install needed)
cd ~/Documents/Repos
uvx code-recap summarize 2025 --author "Your Name" --html --open
# → Output: ./code-recap-2025/
```

That's it! Your reports open automatically in the browser.

### Other Installation Options

```bash
# Install globally with uv
uv tool install code-recap

# Install with pip
pip install code-recap

# Install from source
pip install -e .
```

### Configuration (Optional)

For multi-client workflows, create a config file:

```bash
mkdir -p config && cp -r config.example/* config/
# Edit config/config.yaml to define your clients
```

---

## Recommended Models

Code Recap uses [LiteLLM](https://docs.litellm.ai/) to support multiple LLM providers. Choose based on your needs:

| Model | Command | Best For | Cost |
|-------|---------|----------|------|
| **GPT-4o-mini** | `--model gpt-4o-mini` | Default choice, reliable and fast | ~$0.15/year |
| **Gemini 2.0 Flash** | `--model gemini/gemini-2.0-flash` | Large codebases (1M context), very fast | ~$0.05/year |
| **Claude Haiku** | `--model anthropic/claude-3-5-haiku-latest` | Best writing quality for summaries | ~$0.30/year |

*Costs shown are approximate for summarizing 1 year of typical developer activity (~3000 commits).*

```bash
# Examples
code-recap summarize 2025 --author "Your Name"                              # Uses default (GPT-4o-mini)
code-recap summarize 2025 --author "Your Name" --model gemini/gemini-2.0-flash
code-recap summarize --list-models                                           # See all available models
```

---

## Command Reference

All functionality is accessed through the `code-recap` command with subcommands:

```
code-recap <command> [options]

Commands:
  summarize, report    LLM-powered activity summaries (main command)
  daily, today         Daily activity for time logging
  stats, activity      Statistics without LLM (text/markdown/CSV)
  html                 Convert markdown to HTML reports
  blog                 Generate blog posts from commits
  commits              List commits for a date
  deploy               Deploy HTML reports
  git, repos           Repository utilities (fetch, archive)
```

### `code-recap summarize` — LLM-Powered Summaries

Generates narrative summaries of git activity using hierarchical LLM summarization.

```bash
code-recap summarize 2025 --author "Your Name"                    # All clients
code-recap summarize 2025 --author "Your Name" --client "Acme"    # Specific client
code-recap summarize 2025 --author "@company.com"                 # Match by email domain
code-recap summarize 2025 --author "Your Name" --html --open      # With HTML + open browser
code-recap summarize 2025 --author "Your Name" --dry-run          # Preview (no API cost)
```

| Option | Description | Default |
|--------|-------------|---------|
| `--granularity` | Period breakdown (week/month/quarter/year) | `month` |
| `--model` | LLM model (see Recommended Models above) | `gpt-4o-mini` |
| `--client` | Filter to specific client | All clients |
| `--max-cost` | Budget limit in USD | `1.00` |
| `--html` | Also generate HTML reports | `false` |
| `--open` | Open HTML in browser (implies --html) | `false` |
| `--dry-run` | Preview without API calls | `false` |

---

### `code-recap daily` — Daily Time Logging

Generates concise summaries for a specific date—perfect for time tracking and billing.

```bash
code-recap daily --author "Your Name"                  # Today
code-recap daily --author "Your Name" --date yesterday
code-recap daily --author "Your Name" --date -2        # 2 days ago
code-recap daily --author "Your Name" --no-llm         # Just list commits
```

---

### `code-recap stats` — Statistics & CSV Export

Generates detailed statistics with support for text, markdown, and CSV output.

```bash
code-recap stats 2025 --author "Your Name"
code-recap stats 2020:2025 --author "Your Name" --granularity year --format csv
code-recap stats 2025-Q3 --author "Your Name" --format markdown
```

**Output includes:** commit counts, line changes, per-language breakdown, per-project stats, active days, and coding streaks.

---

### `code-recap blog` — AI Blog Post Generator

Two-stage pipeline: research commits first, then generate a polished blog post.

```bash
# Full pipeline
code-recap blog full "Building a Real-Time LED Controller" \
    --period 2025-09 --author "Your Name"

# Or step by step (allows editing research before writing)
code-recap blog research "My Topic" --period 2025-Q3 --author "Your Name"
code-recap blog write output/blog/my-topic/research.md
```

---

### `code-recap html` — Branded HTML Reports

Converts markdown summaries to styled HTML reports with your company branding.

```bash
code-recap html                       # Generate all HTML reports
code-recap html --client "Acme"       # Just one client
```

---

### `code-recap commits` — Daily Commit Log

Lists all commits for a specific date across all repositories.

```bash
code-recap commits 2025-01-03 --author "Your Name"
code-recap commits $(date +%Y-%m-%d) --author "Your Name"  # Today
```

---

### `code-recap git` — Repository Management

Utilities for managing multiple repositories.

```bash
code-recap git fetch                      # Fetch all repos in parallel
code-recap git archive --days 365         # Archive inactive repos (dry run)
code-recap git archive --days 365 --execute
code-recap git unarchive my-project --execute
```

---

## Configuration

Configuration is **optional**. For basic use, just run the scripts—no config needed.

For customization, copy examples from `config.example/`:

```bash
cp -r config.example/* config/
```

### Client Configuration (`config/config.yaml`) — Optional

If you're a consultant working with multiple clients, configure project-to-client mapping:

```yaml
clients:
  "Acme Corp":
    directories:
      - "acme-*"      # Matches acme-firmware, acme-ios, etc.
    exclude:
      - "*-legacy"    # Except acme-legacy (goes to Other)
  
  "Beta Inc":
    directories:
      - "beta-*"      # Matches beta-api, beta-frontend
  
  Personal:
    directories:
      - "dotfiles"    # Exact match
      - "my-*"        # Glob pattern

# Optional: assign unmatched projects to a default client
# default_client: Personal
```

**Matching rules:**
- `directories`: Glob patterns for repo directory names (supports `*`, `?`, `[seq]`)
- `exclude`: Patterns to exclude (takes precedence over `directories`)
- Use `*keyword*` for substring matching
- Matching is case-insensitive
- First match wins (order matters in YAML)
- Unmatched projects go to "Other"

### Exclusion Patterns (`config/excludes.yaml`)

Configure files/directories to exclude from line count statistics:

```yaml
# Global exclusions (apply to all projects)
global:
  - "*.hex"
  - "*/build/*"
  - "package-lock.json"

# Project-specific exclusions
projects:
  MyProject:
    - "vendor/*"
  AnotherProject:
    - "generated/*"
```

### API Keys (`config/keys/`)

For LLM-powered scripts (`summarize_activity.py`, `generate_blog_post.py`), set API keys as environment variables:

```bash
# OpenAI
export OPENAI_API_KEY='sk-...'

# Anthropic
export ANTHROPIC_API_KEY='sk-ant-...'

# Google Gemini
export GEMINI_API_KEY='...'
```

Or create key files in `config/keys/` and source them:
```bash
# Copy examples
cp -r config.example/keys config/keys

# Edit with your keys
nano config/keys/openai.sh

# Source all keys
source config/keys/all.sh
```

---

## Installation

### Using uv (Recommended)

```bash
cd ~/Documents/Repos/code-recap

# Install as a tool
uv tool install .

# Or run directly
uv run code-recap summarize 2024 --author "Your Name"
uv run code-recap stats 2024 --author "Your Name"
uv run code-recap commits 2024-01-15 --author "Your Name"
uv run code-recap git fetch
```

### Using pip

```bash
cd ~/Documents/Repos/code-recap

# Install in development mode
pip install -e .

# Then run
code-recap summarize 2024 --author "Your Name"
```

### Development

```bash
# Install with dev dependencies (includes ruff)
uv sync --dev

# Run linting
uv run ruff check .
uv run ruff format .
```

### Dependencies

- **Python 3.9+**
- **Git** (command line)
- **litellm** (for `summarize_activity.py` and `generate_blog_post.py`)
- **pyyaml** (for configuration parsing)
- **ruff** (dev only, for linting)

---

## Common Options

Most scripts share these options:

| Option | Description | Default |
|--------|-------------|---------|
| `--root` | Root directory containing repos | Parent of cwd |
| `--author` | Filter commits by author | Required |
| `--client` | Client name for organizing outputs | (none) |
| `--output-dir` | Base output directory | `output/` |
| `-o, --output` | Explicit output file path | Auto-generated |
| `--stdout` | Write to stdout instead of file | `false` |
| `--filter` | Filter repos by name pattern | All repos |
| `--no-fetch` | Skip fetching before processing | Fetch enabled |

---

## License

MIT License - see [LICENSE](LICENSE) for details.
