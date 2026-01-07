<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="logo.svg">
    <img src="logo.svg" alt="Code Recap" width="400">
  </picture>
</p>

<p align="center">
  Generate beautiful activity reports, client summaries, and blog posts from your git history—powered by LLMs.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#use-cases">Use Cases</a> •
  <a href="docs/commands.md">Commands</a> •
  <a href="docs/configuration.md">Configuration</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/code-recap/"><img src="https://img.shields.io/pypi/v/code-recap?color=6366f1&label=PyPI" alt="PyPI"></a>
  <a href="https://pypi.org/project/code-recap/"><img src="https://img.shields.io/pypi/pyversions/code-recap?color=8b5cf6" alt="Python"></a>
  <a href="https://github.com/nrb-tech/code-recap/blob/main/LICENSE"><img src="https://img.shields.io/github/license/nrb-tech/code-recap?color=a78bfa" alt="License"></a>
  <a href="https://pepy.tech/project/code-recap"><img src="https://img.shields.io/pepy/dt/code-recap?color=c4b5fd&label=downloads" alt="Downloads"></a>
</p>

---

## What It Does

**Code Recap** helps developers and consultants who work across multiple repositories:

📊 **Year-in-Review Reports** — Generate polished summaries of your work for clients, complete with statistics, achievements, and technology breakdowns. Export as Markdown or HTML.

⏱️ **Daily Time Logging** — Quickly summarize what you worked on today (or any day) for billing and time tracking. No more digging through commits.

✍️ **Blog Post Generation** — Turn your commits into technical blog posts. The AI researches your changes and drafts content with real code examples.

📈 **Activity Statistics** — Track commits, lines changed, languages used, and coding streaks across all your projects.

🗂️ **Multi-Client Organization** — Automatically group repositories by client using pattern matching. Each client gets their own reports.

### 📝 Example Output

See what generated reports look like:

**For consultants with multiple clients:**
- [**Client Reports (HTML)**](https://nrb-tech.github.io/code-recap/output.example.clients/html/) — Browse the styled HTML version
- [Monthly summary (markdown)](https://github.com/nrb-tech/code-recap/blob/main/output.example.clients/acme_widgets/periods/2024-10.md)
- [Annual client summary (markdown)](https://github.com/nrb-tech/code-recap/blob/main/output.example.clients/acme_widgets/summary-2024.md)

**For solo developers or internal work:**
- [**Solo Reports (HTML)**](https://nrb-tech.github.io/code-recap/output.example.solo/html/) — Without client organization
- [Monthly summary (markdown)](https://github.com/nrb-tech/code-recap/blob/main/output.example.solo/projects/periods/2024-10.md)
- [Annual summary (markdown)](https://github.com/nrb-tech/code-recap/blob/main/output.example.solo/projects/summary-2024.md)

---

## Quick Start

```bash
# Initialize (creates config, prompts for API keys, shows next steps)
uvx code-recap init

# Or set API key manually
export OPENAI_API_KEY='sk-...'

# Generate your year-in-review
cd ~/Documents/Repos
uvx code-recap summarize 2025 --open
# → Output: ./code-recap-2025/
```

That's it! Your reports open automatically in the browser.

### Installation Options

```bash
# Run directly (no install)
uvx code-recap summarize 2025

# Or install globally
uv tool install code-recap    # then use: code-recap summarize ...
pip install code-recap        # then use: code-recap summarize ...
```

---

## Use Cases

### Year-end report with HTML

```bash
code-recap summarize 2025 --open
# → Reports saved to ./code-recap-2025/ (markdown + HTML)
```

### Daily time logging

```bash
code-recap daily --date yesterday
```

### Blog post from your commits

```bash
code-recap blog full "Building a Custom Protocol" --period 2025-Q3
```

### Multi-year statistics (CSV export)

```bash
code-recap stats 2020:2025 --granularity year --format csv
```

---

## How It Works

Run `code-recap` from a directory containing git repositories:

```
~/Documents/Repos/           # Run code-recap here (or use --root <path>)
├── project-a/               # Git repository (scanned)
├── project-b/               # Git repository (scanned)
├── side-project/            # Git repository (scanned)
└── code-recap-2025/         # Generated report (or use --output-dir to specify a different directory)
    ├── summary-2025.md
    └── html/
        └── index.html
```

**No configuration needed for basic use.** Just run the command and get a unified report.

For multi-client workflows, create a config file:

```bash
code-recap init              # Creates config.yaml + API key setup
```

See the [Configuration Guide](docs/configuration.md) for full details.

---

## Recommended Models

Code Recap uses [LiteLLM](https://docs.litellm.ai/) to support multiple LLM providers:

| Model | Command | Best For | Cost |
|-------|---------|----------|------|
| **GPT-4o-mini** | `--model gpt-4o-mini` | Default choice, reliable and fast | ~$0.10/year |
| **Gemini 2.5 Flash** | `--model gemini/gemini-2.5-flash` | Large codebases (1M context), very fast | ~$0.25/year |
| **Gemini 3 Flash** | `--model gemini/gemini-3-flash-preview` | Latest Gemini model | ~$0.40/year |
| **Claude 4.5 Haiku** | `--model claude-haiku-4-5` | Best writing quality for summaries | ~$0.75/year |

*Costs shown are approximate for summarizing 1 year of typical developer activity (~3000 commits).*

```bash
code-recap summarize 2025 --list-models    # See all available models
```

---

## Commands

| Command | Description |
|---------|-------------|
| `summarize` | LLM-powered activity summaries (main command) |
| `daily` | Daily activity for time logging |
| `stats` | Statistics without LLM (text/markdown/CSV) |
| `html` | Convert markdown to HTML reports |
| `blog` | Generate blog posts from commits |
| `deploy` | Deploy HTML reports to providers |
| `git` | Repository utilities (fetch, archive) |

See the [Command Reference](docs/commands.md) for detailed options.

---

## Documentation

- **[Configuration Guide](docs/configuration.md)** — Client mapping, API keys, exclusions, HTML styling
- **[Command Reference](docs/commands.md)** — Detailed options for all CLI commands
- **[Deployment Guide](docs/deployment.md)** — Deploy and share HTML reports
- **[Extending Code Recap](docs/extending.md)** — Implement custom deployment providers
- **[Example Configuration](config.example/config.yaml)** — Full config with all options documented

---

## Installation

### Using uv (Recommended)

```bash
# Install as a tool
uv tool install code-recap

# Or run directly without installing
uvx code-recap summarize 2025
```

### Using pip

```bash
pip install code-recap
```

### Development

```bash
git clone https://github.com/nrb-tech/code-recap.git
cd code-recap
uv sync --dev

# Run linting
uv run ruff check .
uv run ruff format .
```

### Requirements

- **Python 3.9+**
- **Git** (command line)

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

<p align="center">
  Built by <a href="https://nrbtech.io">NRB Tech</a>
</p>
