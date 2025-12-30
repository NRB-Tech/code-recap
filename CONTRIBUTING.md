# Contributing to Code Recap

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/code-recap.git`
3. Install dependencies: `uv sync --dev` or `pip install -e ".[dev]"`
4. Create a branch for your changes: `git checkout -b feature/your-feature-name`

## Development Setup

```bash
# Install with development dependencies
uv sync --dev

# Run linting
uv run ruff check .
uv run ruff format .

# Test changes without LLM costs
./summarize_activity.py 2024 --author "Test" --dry-run
```

## Code Style

- Use `ruff` for linting and formatting (configured in `pyproject.toml`)
- Follow existing patterns for CLI argument parsing (argparse)
- Add docstrings to all public functions with Args/Returns sections
- Keep scripts executable with `#!/usr/bin/env python3`

## Important Guidelines

### No Hardcoded Client/Company References

This project is designed to be generic and configurable. **Never** hardcode:
- Company names
- Client names
- Email addresses
- Logos or branding

All personalization should go in `config/config.yaml`. Use generic examples like "Acme Corp", "Your Name", or "client-name" in help text and documentation.

### Configuration

- Add new config options to both the script's `load_config()` and `config.example/config.yaml`
- Document user-facing options in the README

### Testing

Before submitting a PR:
1. Run `ruff check .` and `ruff format .`
2. Test your changes with `--dry-run` where applicable
3. Verify the example config still works

## Pull Request Process

1. Update the README.md if you've added user-facing features
2. Update `config.example/config.yaml` if you've added configuration options
3. Ensure all linting passes
4. Write a clear PR description explaining your changes

## Reporting Issues

When reporting bugs, please include:
- Python version (`python --version`)
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Relevant error messages

## Questions?

Feel free to open an issue for questions or discussions about potential features.
