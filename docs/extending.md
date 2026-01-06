# Extending Code Recap

This guide covers how to extend Code Recap with custom functionality.

## Custom Deployment Providers

Code Recap uses a plugin architecture for deployment providers. You can create your own provider to deploy reports to any service (Netlify, FTP, GitHub Pages, etc.) without modifying Code Recap's source code.

### Quick Start: Creating a Plugin Package

The fastest way to add a custom provider is to create a small Python package.

**Project structure:**

```
code-recap-ftp/
├── pyproject.toml
└── src/
    └── code_recap_ftp/
        ├── __init__.py
        └── provider.py
```

You can scaffold this with uv:

```bash
uv init code-recap-ftp --lib
cd code-recap-ftp
```

**1. Edit `pyproject.toml`:**

```toml
[project]
name = "code-recap-ftp"
version = "0.1.0"
dependencies = ["code-recap"]

[project.entry-points."code_recap.deploy_providers"]
ftp = "code_recap_ftp.provider:FTPProvider"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/code_recap_ftp"]
```

The key is the `[project.entry-points."code_recap.deploy_providers"]` section. This registers your provider with Code Recap using Python's entry point mechanism.

**2. Create `src/code_recap_ftp/provider.py`:**

```python
import ftplib
import os
from pathlib import Path

from code_recap.deploy_reports import DeployConfig, DeployProvider, DeployResult


class FTPProvider(DeployProvider):
    """Deploys reports to an FTP server."""

    def __init__(self, config: DeployConfig):
        self.config = config
        self.host = os.environ.get("FTP_HOST", "")
        self.user = os.environ.get("FTP_USER", "")
        self.password = os.environ.get("FTP_PASSWORD", "")
        self.base_path = os.environ.get("FTP_PATH", "/public_html/reports")

    @property
    def name(self) -> str:
        return "ftp"

    def deploy(self, source_dir: Path, client_name: str, client_slug: str) -> DeployResult:
        if not self.host or not self.user:
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message="FTP_HOST and FTP_USER environment variables required",
            )

        remote_path = f"{self.base_path}/{client_slug}"

        try:
            with ftplib.FTP(self.host, self.user, self.password) as ftp:
                # Create remote directory if needed
                try:
                    ftp.mkd(remote_path)
                except ftplib.error_perm:
                    pass  # Directory exists

                ftp.cwd(remote_path)

                # Upload all files
                for file_path in source_dir.rglob("*"):
                    if file_path.is_file():
                        rel_path = file_path.relative_to(source_dir)
                        with open(file_path, "rb") as f:
                            ftp.storbinary(f"STOR {rel_path}", f)

            return DeployResult(
                success=True,
                provider=self.name,
                client=client_name,
                message=f"Uploaded to FTP: {remote_path}",
                url=f"https://{self.host}/{client_slug}/index.html",
            )
        except Exception as e:
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message=f"FTP upload failed: {e}",
            )
```

**3. Create `src/code_recap_ftp/__init__.py`:**

```python
# Empty file, or re-export the provider
```

**4. Install your plugin:**

```bash
cd code-recap-ftp

# Using uv (recommended)
uv pip install -e .

# Or using pip
pip install -e .

# Or install from PyPI if you publish it
uv pip install code-recap-ftp
pip install code-recap-ftp
```

**5. Use it:**

```bash
code-recap deploy --list-providers
# Output:
#   cloudflare
#   s3
#   zip
#   ftp (plugin)

code-recap deploy --client acme --provider ftp
```

---

## Provider Architecture

Deployment providers inherit from the `DeployProvider` abstract base class:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DeployResult:
    """Result of a deployment operation."""
    success: bool
    provider: str
    client: str
    message: str
    url: Optional[str] = None     # URL where report is accessible
    path: Optional[str] = None    # Local path (for file-based providers)


class DeployProvider(ABC):
    """Abstract base class for deployment providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name used in CLI (--provider NAME)."""
        pass

    @abstractmethod
    def deploy(self, source_dir: Path, client_name: str, client_slug: str) -> DeployResult:
        """Deploy the source directory.

        Args:
            source_dir: Path to the HTML files to deploy.
            client_name: Display name of the client (e.g., "Acme Corp").
            client_slug: URL-safe identifier (e.g., "acme_corp").

        Returns:
            DeployResult with deployment outcome.
        """
        pass
```

### Constructor

Your provider's `__init__` receives a `DeployConfig` object containing:

- Configuration from `config.yaml`
- Per-client deployment overrides
- Any custom fields you add

```python
def __init__(self, config: DeployConfig):
    self.config = config
    # Read your settings from environment or config
    self.bucket = os.environ.get("S3_BUCKET", "")
```

### The `deploy` Method

This is where the actual deployment happens. It receives:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `source_dir` | Path to HTML files | `/path/to/output/html/acme_corp/` |
| `client_name` | Display name | `"Acme Corp"` |
| `client_slug` | URL-safe identifier | `"acme_corp"` |

Return a `DeployResult` indicating success or failure.

---

## Reading Custom Configuration

If you need custom settings in `config.yaml`, you can read them from the raw config data. The `DeployConfig` object provides access to parsed configuration.

For simple cases, use environment variables:

```python
class MyProvider(DeployProvider):
    def __init__(self, config: DeployConfig):
        self.api_key = os.environ.get("MY_PROVIDER_API_KEY", "")
```

For config file integration, you can extend `DeployConfig` if contributing to the main repo, or read the raw YAML yourself.

---

## Best Practices

1. **Fail gracefully** — Check for required tools/credentials and return helpful error messages

2. **Support environment variables** — Allow configuration via env vars for CI/CD pipelines

3. **Preserve idempotency** — Multiple deploys of the same content should produce identical results

4. **Return useful URLs** — Include the deployed URL in `DeployResult.url` when applicable

5. **Handle missing dependencies** — Check for CLI tools (aws, netlify, etc.) and provide install instructions

6. **Validate early** — Check configuration in `__init__` or early in `deploy()` before doing work

---

## Example: Netlify Provider

```python
class NetlifyProvider(DeployProvider):
    """Deploys to Netlify using the CLI."""

    def __init__(self, config: DeployConfig):
        self.config = config
        self.site_prefix = os.environ.get("NETLIFY_SITE_PREFIX", "reports")

    @property
    def name(self) -> str:
        return "netlify"

    def deploy(self, source_dir: Path, client_name: str, client_slug: str) -> DeployResult:
        site_name = f"{self.site_prefix}-{client_slug}".lower().replace("_", "-")

        # Check for netlify CLI
        try:
            subprocess.run(["netlify", "--version"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message="Netlify CLI not found. Install with: npm install -g netlify-cli",
            )

        try:
            cmd = [
                "netlify", "deploy",
                "--dir", str(source_dir),
                "--site", site_name,
                "--prod",
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)

            return DeployResult(
                success=True,
                provider=self.name,
                client=client_name,
                message=f"Deployed to Netlify: {site_name}",
                url=f"https://{site_name}.netlify.app",
            )
        except subprocess.CalledProcessError as e:
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message=f"Netlify deploy failed: {e.stderr}",
            )
```

Register in `pyproject.toml`:

```toml
[project.entry-points."code_recap.deploy_providers"]
netlify = "my_package:NetlifyProvider"
```

---

## Entry Point Reference

Code Recap discovers providers using Python's entry point system. The entry point group is:

```
code_recap.deploy_providers
```

Each entry point maps a provider name to a class:

```toml
[project.entry-points."code_recap.deploy_providers"]
provider_name = "package.module:ClassName"
```

The class must:
- Inherit from `DeployProvider`
- Accept a `DeployConfig` in `__init__`
- Implement the `name` property and `deploy` method

---

## Publishing Your Provider

To share your provider with others:

```bash
cd code-recap-ftp

# Build the package
uv build

# Publish to PyPI (requires PyPI account and token)
uv publish
```

Then others can install it:

```bash
uv pip install code-recap-ftp
# or
pip install code-recap-ftp
```

For widely-useful providers, consider contributing directly to Code Recap. See [CONTRIBUTING.md](../CONTRIBUTING.md).
