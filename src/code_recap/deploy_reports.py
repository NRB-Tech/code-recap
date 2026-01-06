#!/usr/bin/env python3
"""Deploy HTML reports to various providers.

Supports multiple deployment targets:
- zip: Create a zip file for manual sharing
- cloudflare: Deploy to Cloudflare Pages with optional Access control

Usage:
    ./deploy_reports.py --client acme --provider zip
    ./deploy_reports.py --client acme --provider cloudflare
    ./deploy_reports.py --all --provider zip
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from code_recap.arguments import add_input_dir_arg
from code_recap.paths import get_config_path, get_output_dir

# Default configuration
DEFAULT_CONFIG = {
    "deploy": {
        "providers": {
            "zip": {
                "output_dir": "output/zips",
            },
            "cloudflare": {
                "project_prefix": "reports",
                "account_id": "",  # Uses CLOUDFLARE_ACCOUNT_ID env var if empty
            },
        }
    }
}


@dataclass
class ClientDeployConfig:
    """Per-client deployment configuration."""

    s3_url: Optional[str] = None  # Custom URL for S3-hosted reports (e.g., CloudFront domain)
    cloudflare_project_name: Optional[str] = None  # Override project name
    cloudflare_access_emails: list[str] = field(default_factory=list)  # Emails with access


@dataclass
class DeployConfig:
    """Configuration for deployment."""

    zip_output_dir: str = "output/zips"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_prefix: str = ""  # Optional path prefix within bucket
    cloudflare_project_prefix: str = "reports"
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""  # For Access API
    cloudflare_access_emails: list[str] = field(default_factory=list)  # Global access emails
    client_configs: dict[str, ClientDeployConfig] = None  # Per-client overrides

    def __post_init__(self):
        if self.client_configs is None:
            self.client_configs = {}

    def get_client_config(self, client_slug: str) -> ClientDeployConfig:
        """Gets per-client config, matching by various name formats."""
        # Try exact match first
        if client_slug in self.client_configs:
            return self.client_configs[client_slug]

        # Try normalized match
        normalized = client_slug.lower().replace("_", " ").replace("-", " ")
        for key, config in self.client_configs.items():
            key_normalized = key.lower().replace("_", " ").replace("-", " ")
            if key_normalized == normalized:
                return config

        return ClientDeployConfig()

    @classmethod
    def from_dict(cls, data: dict) -> "DeployConfig":
        """Creates config from dictionary."""
        config = cls()
        if "deploy" in data and "providers" in data["deploy"]:
            providers = data["deploy"]["providers"]
            if "zip" in providers:
                config.zip_output_dir = providers["zip"].get("output_dir", config.zip_output_dir)
            if "s3" in providers:
                s3 = providers["s3"]
                config.s3_bucket = s3.get("bucket", config.s3_bucket)
                config.s3_region = s3.get("region", config.s3_region)
                config.s3_prefix = s3.get("prefix", config.s3_prefix)
            if "cloudflare" in providers:
                cf = providers["cloudflare"]
                config.cloudflare_project_prefix = cf.get(
                    "project_prefix", config.cloudflare_project_prefix
                )
                config.cloudflare_account_id = cf.get("account_id", config.cloudflare_account_id)
                config.cloudflare_api_token = cf.get("api_token", config.cloudflare_api_token)
                config.cloudflare_access_emails = cf.get("access_emails", [])

        # Load per-client deploy configs from html_report.clients section
        if "html_report" in data and "clients" in data["html_report"]:
            for client_name, client_data in data["html_report"]["clients"].items():
                if isinstance(client_data, dict) and "deploy" in client_data:
                    deploy_data = client_data["deploy"]
                    client_config = ClientDeployConfig()
                    if "s3" in deploy_data:
                        s3_data = deploy_data["s3"]
                        client_config.s3_url = s3_data.get("url")
                    if "cloudflare" in deploy_data:
                        cf_data = deploy_data["cloudflare"]
                        client_config.cloudflare_project_name = cf_data.get("project_name")
                        client_config.cloudflare_access_emails = cf_data.get("access_emails", [])
                    config.client_configs[client_name] = client_config

        return config


@dataclass
class DeployResult:
    """Result of a deployment operation."""

    success: bool
    provider: str
    client: str
    message: str
    url: Optional[str] = None
    path: Optional[str] = None


class DeployProvider(ABC):
    """Abstract base class for deployment providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""
        pass

    @abstractmethod
    def deploy(self, source_dir: Path, client_name: str, client_slug: str) -> DeployResult:
        """Deploy the source directory.

        Args:
            source_dir: Path to the HTML files to deploy.
            client_name: Display name of the client.
            client_slug: URL-safe client identifier.

        Returns:
            DeployResult with deployment outcome.
        """
        pass


class ZipProvider(DeployProvider):
    """Creates a zip file for manual sharing."""

    def __init__(self, config: DeployConfig):
        self.output_dir = Path(config.zip_output_dir)

    @property
    def name(self) -> str:
        return "zip"

    def deploy(self, source_dir: Path, client_name: str, client_slug: str) -> DeployResult:
        """Creates a zip file of the client's HTML reports."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename with date
        date_str = datetime.now().strftime("%Y-%m-%d")
        zip_filename = f"{client_name.replace(' ', '-')}-Report-{date_str}.zip"
        zip_path = self.output_dir / zip_filename

        try:
            # Remove existing zip if present
            if zip_path.exists():
                zip_path.unlink()

            # Create zip file
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in source_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(source_dir)
                        zf.write(file_path, arcname)

            return DeployResult(
                success=True,
                provider=self.name,
                client=client_name,
                message=f"Created zip file: {zip_path}",
                path=str(zip_path),
            )
        except Exception as e:
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message=f"Failed to create zip: {e}",
            )


class S3Provider(DeployProvider):
    """Deploys reports to an AWS S3 bucket.

    Requires the AWS CLI to be installed and configured with appropriate credentials.
    The bucket should be configured for static website hosting if you want public access.
    """

    def __init__(self, config: DeployConfig):
        """Initialize with deployment configuration.

        Args:
            config: DeployConfig instance with provider settings.
        """
        self.config = config
        self.bucket = config.s3_bucket or os.environ.get("S3_BUCKET", "")
        self.region = config.s3_region or os.environ.get("AWS_REGION", "us-east-1")
        self.prefix = config.s3_prefix  # Optional path prefix within bucket

    @property
    def name(self) -> str:
        return "s3"

    def _check_aws_cli(self) -> bool:
        """Checks if AWS CLI is available."""
        try:
            subprocess.run(["aws", "--version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _get_s3_path(self, client_slug: str) -> str:
        """Builds the S3 destination path."""
        if self.prefix:
            return f"s3://{self.bucket}/{self.prefix}/{client_slug}/"
        return f"s3://{self.bucket}/{client_slug}/"

    def _get_url(self, client_slug: str) -> str:
        """Builds the public URL for the deployed site."""
        # Check for custom URL in per-client config (e.g., CloudFront domain)
        client_config = self.config.get_client_config(client_slug)
        if client_config.s3_url:
            return client_config.s3_url

        # Default S3 website URL format
        path = f"{self.prefix}/{client_slug}" if self.prefix else client_slug
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{path}/index.html"

    def deploy(self, source_dir: Path, client_name: str, client_slug: str) -> DeployResult:
        """Syncs HTML files to S3."""
        if not self._check_aws_cli():
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message="AWS CLI not found. Install with: pip install awscli",
            )

        if not self.bucket:
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message="S3 bucket not configured. Set S3_BUCKET env var or s3.bucket in config.",
            )

        s3_path = self._get_s3_path(client_slug)

        try:
            cmd = [
                "aws",
                "s3",
                "sync",
                str(source_dir),
                s3_path,
                "--delete",
                "--region",
                self.region,
            ]
            print(f"  Running: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)

            return DeployResult(
                success=True,
                provider=self.name,
                client=client_name,
                message=f"Deployed to S3: {s3_path}",
                url=self._get_url(client_slug),
            )
        except subprocess.CalledProcessError as e:
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message=f"S3 sync failed (exit code {e.returncode})",
            )


class CloudflareProvider(DeployProvider):
    """Deploys to Cloudflare Pages using Wrangler."""

    def __init__(self, config: DeployConfig):
        self.config = config
        self.project_prefix = config.cloudflare_project_prefix
        self.account_id = config.cloudflare_account_id or os.environ.get(
            "CLOUDFLARE_ACCOUNT_ID", ""
        )
        self.api_token = config.cloudflare_api_token or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        self._account_id_fetched = False

    @property
    def name(self) -> str:
        return "cloudflare"

    def _ensure_account_id(self) -> bool:
        """Ensures account_id is set, fetching from wrangler if needed."""
        if self.account_id:
            return True
        if self._account_id_fetched:
            return bool(self.account_id)

        self._account_id_fetched = True

        # Try to get account ID from wrangler whoami
        try:
            result = subprocess.run(
                ["npx", "--yes", "wrangler", "whoami"],
                capture_output=True,
                text=True,
                check=True,
            )
            # Parse output for account ID - looks for pattern like:
            # │ Account Name │ Account ID │
            # or newer format with account details
            import re

            # Look for hex account ID (32 chars)
            match = re.search(r"\b([a-f0-9]{32})\b", result.stdout)
            if match:
                self.account_id = match.group(1)
                return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        return False

    def _check_wrangler(self) -> bool:
        """Checks if wrangler CLI is available (via npx or directly)."""
        # Try npx first (works without global install)
        try:
            subprocess.run(
                ["npx", "--yes", "wrangler", "--version"],
                capture_output=True,
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Fall back to direct wrangler command
        try:
            subprocess.run(
                ["wrangler", "--version"],
                capture_output=True,
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _get_project_name(self, client_slug: str) -> str:
        """Generates Cloudflare Pages project name, using per-client override if set."""
        # Check for per-client override
        client_config = self.config.get_client_config(client_slug)
        if client_config.cloudflare_project_name:
            return client_config.cloudflare_project_name

        # Cloudflare project names must be lowercase, alphanumeric with hyphens
        slug = client_slug.lower().replace("_", "-").replace(" ", "-")
        if self.project_prefix:
            return f"{self.project_prefix}-{slug}"
        return slug

    def _project_exists(self, project_name: str) -> bool:
        """Checks if a Pages project exists."""
        cmd = [
            "npx",
            "--yes",
            "wrangler",
            "pages",
            "project",
            "list",
        ]
        if self.account_id:
            cmd.extend(["--account-id", self.account_id])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            # Check if project name appears in the output
            return project_name in result.stdout
        except subprocess.CalledProcessError:
            return False

    def _create_project(self, project_name: str) -> bool:
        """Creates a new Pages project."""
        print(f"  Creating project: {project_name}")
        cmd = [
            "npx",
            "--yes",
            "wrangler",
            "pages",
            "project",
            "create",
            project_name,
            "--production-branch=main",
        ]
        if self.account_id:
            cmd.extend(["--account-id", self.account_id])

        try:
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def _get_access_emails(self, client_slug: str) -> list[str]:
        """Gets the list of emails allowed to access this project."""
        client_config = self.config.get_client_config(client_slug)
        # Combine global and per-client emails, deduplicate
        emails = set(self.config.cloudflare_access_emails)
        emails.update(client_config.cloudflare_access_emails)
        return list(emails)

    def _api_request(
        self, method: str, endpoint: str, data: Optional[dict] = None
    ) -> Optional[dict]:
        """Makes an authenticated request to the Cloudflare API."""
        if not self.api_token or not self.account_id:
            return None

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            print(f"  API error: {e.code} - {error_body}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"  API error: {e}", file=sys.stderr)
            return None

    def _setup_access(self, project_name: str, emails: list[str]) -> bool:
        """Sets up Cloudflare Access for the Pages project.

        Creates an Access application and policy to restrict access to specified emails.
        """
        if not emails:
            return True  # No access restriction needed

        if not self.api_token:
            print(
                "  Warning: No API token configured, skipping Access setup. "
                "Set CLOUDFLARE_API_TOKEN or add api_token to config.",
                file=sys.stderr,
            )
            return True  # Don't fail deployment, just skip access setup

        if not self._ensure_account_id():
            print(
                "  Warning: Could not determine account ID, skipping Access setup. "
                "Set CLOUDFLARE_ACCOUNT_ID or add account_id to config.",
                file=sys.stderr,
            )
            return True

        domain = f"{project_name}.pages.dev"
        app_name = f"{project_name}-access"

        print(f"  Setting up Access for {domain} ({len(emails)} email(s))")

        # Check if application already exists
        existing_apps = self._api_request("GET", "access/apps")
        if existing_apps and existing_apps.get("success"):
            for app in existing_apps.get("result", []):
                if app.get("name") == app_name:
                    print(f"  Access application already exists: {app_name}")
                    # Update the policy emails
                    app_id = app["id"]
                    return self._update_access_policy(app_id, emails)

        # Create new Access application
        app_data = {
            "name": app_name,
            "domain": domain,
            "type": "self_hosted",
            "session_duration": "24h",
        }

        result = self._api_request("POST", "access/apps", app_data)
        if not result or not result.get("success"):
            print("  Warning: Failed to create Access application", file=sys.stderr)
            return True  # Don't fail deployment

        app_id = result["result"]["id"]

        # Create Access policy with email rules
        policy_data = {
            "name": "Email Access",
            "decision": "allow",
            "include": [{"email": {"email": email}} for email in emails],
            "precedence": 1,
        }

        policy_result = self._api_request("POST", f"access/apps/{app_id}/policies", policy_data)
        if not policy_result or not policy_result.get("success"):
            print("  Warning: Failed to create Access policy", file=sys.stderr)
            return True

        print(f"  Access configured for: {', '.join(emails)}")
        return True

    def _update_access_policy(self, app_id: str, emails: list[str]) -> bool:
        """Updates an existing Access application's policy."""
        # Get existing policies
        policies = self._api_request("GET", f"access/apps/{app_id}/policies")
        if not policies or not policies.get("success"):
            return True

        # Update or create the email policy
        for policy in policies.get("result", []):
            if policy.get("name") == "Email Access":
                policy_id = policy["id"]
                policy_data = {
                    "name": "Email Access",
                    "decision": "allow",
                    "include": [{"email": {"email": email}} for email in emails],
                    "precedence": 1,
                }
                self._api_request("PUT", f"access/apps/{app_id}/policies/{policy_id}", policy_data)
                print(f"  Access policy updated for: {', '.join(emails)}")
                return True

        # No existing policy, create one
        policy_data = {
            "name": "Email Access",
            "decision": "allow",
            "include": [{"email": {"email": email}} for email in emails],
            "precedence": 1,
        }
        self._api_request("POST", f"access/apps/{app_id}/policies", policy_data)
        print(f"  Access policy created for: {', '.join(emails)}")
        return True

    def deploy(self, source_dir: Path, client_name: str, client_slug: str) -> DeployResult:
        """Deploys to Cloudflare Pages."""
        if not self._check_wrangler():
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message="Wrangler/npx not found. Install Node.js or run: npm install -g wrangler",
            )

        project_name = self._get_project_name(client_slug)

        # Check if project exists, create if not
        if not self._project_exists(project_name):
            print(f"  Project '{project_name}' not found, creating...")
            if not self._create_project(project_name):
                return DeployResult(
                    success=False,
                    provider=self.name,
                    client=client_name,
                    message=f"Failed to create project: {project_name}",
                )

        # Build wrangler deploy command
        cmd = [
            "npx",
            "--yes",
            "wrangler",
            "pages",
            "deploy",
            str(source_dir),
            "--project-name",
            project_name,
            "--commit-dirty=true",
            "--branch=main",
        ]

        if self.account_id:
            cmd.extend(["--account-id", self.account_id])

        try:
            # Run interactively
            print(f"  Running: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)

            # Setup access control if emails configured
            access_emails = self._get_access_emails(client_slug)
            if access_emails:
                self._setup_access(project_name, access_emails)

            return DeployResult(
                success=True,
                provider=self.name,
                client=client_name,
                message=f"Deployed to Cloudflare Pages: {project_name}",
                url=f"https://{project_name}.pages.dev",
            )
        except subprocess.CalledProcessError as e:
            return DeployResult(
                success=False,
                provider=self.name,
                client=client_name,
                message=f"Deployment failed (exit code {e.returncode})",
            )


# Built-in providers
_BUILTIN_PROVIDERS: dict[str, type[DeployProvider]] = {
    "zip": ZipProvider,
    "s3": S3Provider,
    "cloudflare": CloudflareProvider,
}


def _discover_providers() -> dict[str, type[DeployProvider]]:
    """Discovers deployment providers from entry points.

    External packages can register providers via the 'code_recap.deploy_providers'
    entry point group. Each entry point should point to a DeployProvider subclass.

    Example pyproject.toml for an external provider package:
        [project.entry-points."code_recap.deploy_providers"]
        s3 = "my_package.providers:S3Provider"

    Returns:
        Dict mapping provider names to provider classes.
    """
    providers = dict(_BUILTIN_PROVIDERS)

    try:
        # Python 3.10+ has importlib.metadata in stdlib
        from importlib.metadata import entry_points

        # entry_points() returns a SelectableGroups object in 3.10+
        # or a dict in 3.9
        eps = entry_points()
        if hasattr(eps, "select"):
            # Python 3.10+
            discovered = eps.select(group="code_recap.deploy_providers")
        else:
            # Python 3.9
            discovered = eps.get("code_recap.deploy_providers", [])

        for ep in discovered:
            try:
                provider_class = ep.load()
                if not (
                    isinstance(provider_class, type) and issubclass(provider_class, DeployProvider)
                ):
                    print(
                        f"Warning: Entry point '{ep.name}' is not a DeployProvider subclass",
                        file=sys.stderr,
                    )
                    continue
                providers[ep.name] = provider_class
            except Exception as e:
                print(f"Warning: Failed to load provider '{ep.name}': {e}", file=sys.stderr)

    except ImportError:
        pass  # importlib.metadata not available

    return providers


def get_providers() -> dict[str, type[DeployProvider]]:
    """Returns all available deployment providers (built-in and plugins).

    Returns:
        Dict mapping provider names to provider classes.
    """
    return _discover_providers()


# For backwards compatibility, PROVIDERS is populated at import time
# Use get_providers() for dynamic discovery including newly installed plugins
PROVIDERS: dict[str, type[DeployProvider]] = _discover_providers()


def load_config(config_path: Path) -> DeployConfig:
    """Loads configuration from YAML file.

    Args:
        config_path: Path to the config file.

    Returns:
        DeployConfig with loaded or default values.
    """
    config = DeployConfig()

    if not config_path.exists():
        return config

    try:
        import yaml

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        config = DeployConfig.from_dict(data)
    except ImportError:
        print("Warning: PyYAML not installed, using defaults", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to load config: {e}", file=sys.stderr)

    return config


def find_client_dirs(html_dir: Path, client_filter: Optional[str] = None) -> list[Path]:
    """Finds client directories in the HTML output.

    Args:
        html_dir: Path to the HTML output directory.
        client_filter: Optional client name/slug to filter by.

    Returns:
        List of client directory paths.
    """
    if not html_dir.exists():
        return []

    clients = []
    for item in sorted(html_dir.iterdir()):
        if not item.is_dir():
            continue
        if item.name.startswith("."):
            continue

        # Apply filter if specified
        if client_filter:
            filter_normalized = client_filter.lower().replace(" ", "_").replace("-", "_")
            dir_normalized = item.name.lower().replace(" ", "_").replace("-", "_")
            if filter_normalized != dir_normalized:
                continue

        # Check if it looks like a client directory (has index.html)
        if (item / "index.html").exists():
            clients.append(item)

    return clients


def deploy_client(
    client_dir: Path, provider: DeployProvider, verbose: bool = False
) -> DeployResult:
    """Deploys a single client's reports.

    Args:
        client_dir: Path to the client's HTML directory.
        provider: Deployment provider to use.
        verbose: Whether to print verbose output.

    Returns:
        DeployResult with deployment outcome.
    """
    client_slug = client_dir.name
    # Convert slug to display name
    client_name = client_slug.replace("_", " ").title()

    if verbose:
        print(f"Deploying {client_name} via {provider.name}...", file=sys.stderr)

    return provider.deploy(client_dir, client_name, client_slug)


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code.
    """
    # Discover providers dynamically (includes plugins)
    providers = get_providers()

    parser = argparse.ArgumentParser(
        description="Deploy HTML reports to various providers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --client acme --provider zip
  %(prog)s --client "Beta Inc" --provider cloudflare
  %(prog)s --client acme --provider s3
  %(prog)s --all --provider zip

Built-in providers:
  zip        - Create a zip file for manual sharing
  s3         - Deploy to AWS S3 (requires AWS CLI)
  cloudflare - Deploy to Cloudflare Pages (requires wrangler CLI)

Custom providers can be installed as plugins. Use --list-providers to see all.
""",
    )

    parser.add_argument(
        "--client",
        "-c",
        help="Deploy specific client (by folder name)",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Deploy all clients",
    )
    parser.add_argument(
        "--provider",
        "-p",
        help="Deployment provider to use (see --list-providers)",
    )
    add_input_dir_arg(parser, help_text="Input HTML directory (default: derived from output dir)")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (default: ./config/config.yaml or ~/.config/code-recap/)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="List available providers and exit",
    )

    args = parser.parse_args(argv)

    if args.list_providers:
        print("Available providers:")
        for name in providers:
            suffix = "" if name in _BUILTIN_PROVIDERS else " (plugin)"
            print(f"  {name}{suffix}")
        return 0

    if not args.provider:
        parser.error("--provider is required (use --list-providers to see options)")

    # Validate provider name
    if args.provider not in providers:
        available = ", ".join(sorted(providers.keys()))
        print(f"Unknown provider: {args.provider}", file=sys.stderr)
        print(f"Available providers: {available}", file=sys.stderr)
        return 1

    if not args.client and not args.all:
        parser.error("Either --client or --all is required")

    # Load config
    config_path = get_config_path(args.config)
    config = load_config(config_path)

    if args.verbose:
        print(f"Config: {config_path}", file=sys.stderr)

    # Find client directories
    # If input_dir is provided, check if it has an html/ subdirectory
    if args.input_dir:
        input_path = Path(args.input_dir)
        html_subdir = input_path / "html"
        html_dir = html_subdir if html_subdir.exists() else input_path
    else:
        html_dir = get_output_dir(subdir="html")
    client_dirs = find_client_dirs(html_dir, args.client if not args.all else None)

    if not client_dirs:
        if args.client:
            print(f"No HTML output found for client: {args.client}", file=sys.stderr)
        else:
            print(f"No client directories found in: {html_dir}", file=sys.stderr)
        return 1

    # Create provider
    provider_class = providers[args.provider]
    provider = provider_class(config)

    # Deploy each client
    results = []
    for client_dir in client_dirs:
        result = deploy_client(client_dir, provider, args.verbose)
        results.append(result)

        # Print result
        if result.success:
            print(f"✓ {result.client}: {result.message}")
            if result.url:
                print(f"  URL: {result.url}")
            if result.path:
                print(f"  Path: {result.path}")
        else:
            print(f"✗ {result.client}: {result.message}")

    # Summary
    success_count = sum(1 for r in results if r.success)
    print(f"\nDeployed {success_count}/{len(results)} clients via {args.provider}")

    return 0 if success_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
