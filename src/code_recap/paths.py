"""Path utilities for code-recap.

Provides functions for determining configuration and output paths that work
correctly whether the package is installed via pip/uv or run from source.
"""

from pathlib import Path
from typing import Optional


def _is_installed_package() -> bool:
    """Determines if code-recap is running as an installed package.

    Returns:
        True if installed via pip/uv, False if running from source.
    """
    try:
        import code_recap

        package_path = Path(code_recap.__file__).resolve().parent
        # Check if we're in a site-packages or similar installation directory
        # vs being in a development/source directory
        path_parts = package_path.parts
        return any(
            part in ("site-packages", "dist-packages", ".venv", "venv") for part in path_parts
        )
    except (ImportError, AttributeError):
        return False


def _find_project_root() -> Optional[Path]:
    """Finds the project root directory when running from source.

    Looks for pyproject.toml or .git directory as markers.

    Returns:
        Path to project root, or None if not found.
    """
    # Start from current module location
    try:
        import code_recap

        current = Path(code_recap.__file__).resolve().parent
    except (ImportError, AttributeError):
        current = Path(__file__).resolve().parent

    # Walk up looking for project markers
    while current != current.parent:
        if (current / "pyproject.toml").exists() or (current / ".git").exists():
            return current
        current = current.parent

    return None


def get_config_path(config_file: Optional[str] = None) -> Path:
    """Gets the path to the configuration file.

    Search order:
    1. Explicit config_file argument (if provided)
    2. ./config/config.yaml (current working directory)
    3. ~/.config/code-recap/config.yaml (user config)
    4. Project root config (when running from source)

    Args:
        config_file: Explicit path to config file, if specified by user.

    Returns:
        Path to configuration file (may not exist).
    """
    # Explicit path takes precedence
    if config_file:
        return Path(config_file).resolve()

    # Check current directory first
    cwd_config = Path.cwd() / "config" / "config.yaml"
    if cwd_config.exists():
        return cwd_config

    # Check user config directory
    user_config = Path.home() / ".config" / "code-recap" / "config.yaml"
    if user_config.exists():
        return user_config

    # When running from source, check project root
    project_root = _find_project_root()
    if project_root:
        source_config = project_root / "config" / "config.yaml"
        if source_config.exists():
            return source_config

    # Default to current directory config (even if doesn't exist)
    return cwd_config


def get_output_dir(
    output_dir: Optional[str] = None,
    period: Optional[str] = None,
    client: Optional[str] = None,
    subdir: Optional[str] = None,
) -> Path:
    """Gets the output directory for generated files.

    When installed as a package (via pip/uv):
        Default: $(pwd)/code-recap-<period>/<client>/<subdir>

    When running from source (development):
        Default: <project_root>/output/<client>/<subdir>

    Args:
        output_dir: Explicit output directory specified by user.
        period: Period string (e.g., "2025", "2025-01") for default path.
        client: Client name for subdirectory.
        subdir: Additional subdirectory (e.g., "periods", "html").

    Returns:
        Path to output directory.
    """
    import re

    if output_dir:
        # Explicit path provided by user
        base_dir = Path(output_dir).resolve()
    elif _is_installed_package():
        # Installed via pip/uv - use cwd-based path
        if period:
            base_dir = Path.cwd() / f"code-recap-{period}"
        else:
            base_dir = Path.cwd() / "code-recap-output"
    else:
        # Running from source - use project output directory
        project_root = _find_project_root()
        if project_root:
            base_dir = project_root / "output"
        else:
            base_dir = Path.cwd() / "output"

    # Add client subdirectory if specified
    if client:
        client_safe = re.sub(r"[^\w\-]", "_", client.lower())
        base_dir = base_dir / client_safe

    # Add additional subdirectory if specified
    if subdir:
        base_dir = base_dir / subdir

    return base_dir


def get_default_output_dir_name() -> str:
    """Gets the default output directory name for help text.

    Returns:
        Description of default output directory for CLI help.
    """
    if _is_installed_package():
        return "./code-recap-<period>/"
    else:
        return "output/"


def get_default_scan_root() -> Path:
    """Gets the default root directory for scanning repositories.

    When installed as a package (via pip/uv):
        Default: current working directory (scans cwd for repos)

    When running from source (development):
        Default: parent of cwd (scans sibling directories)

    Returns:
        Path to the default scan root directory.
    """
    if _is_installed_package():
        return Path.cwd()
    else:
        return Path.cwd().parent


def is_installed_package() -> bool:
    """Public wrapper for checking if running as installed package.

    Returns:
        True if installed via pip/uv, False if running from source.
    """
    return _is_installed_package()


# API key environment variable names
API_KEY_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def load_api_keys_from_config(
    config_path: Optional[Path] = None,
    verbose: bool = False,
) -> dict[str, str]:
    """Loads API keys from config and sets them as environment variables.

    Keys in the config file are only used if the corresponding environment
    variable is not already set. This allows environment variables to override
    config file values.

    Args:
        config_path: Path to config file. If None, uses get_config_path().
        verbose: If True, print which keys were loaded.

    Returns:
        Dict of provider name to key that were loaded from config.
    """
    import os

    if config_path is None:
        config_path = get_config_path()

    if not config_path.exists():
        return {}

    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        return {}

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except Exception:
        return {}

    if not data or "api_keys" not in data:
        return {}

    api_keys_section = data["api_keys"]
    if not isinstance(api_keys_section, dict):
        return {}

    loaded_keys: dict[str, str] = {}

    for provider, env_var in API_KEY_ENV_VARS.items():
        # Skip if environment variable is already set
        if os.environ.get(env_var):
            continue

        # Check config for this provider's key
        key = api_keys_section.get(provider)
        if key and isinstance(key, str) and key.strip():
            os.environ[env_var] = key.strip()
            loaded_keys[provider] = key.strip()
            if verbose:
                import sys

                print(f"Loaded {provider} API key from config", file=sys.stderr)

    return loaded_keys
