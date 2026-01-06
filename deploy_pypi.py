#!/usr/bin/env python3
"""Deploy code-recap to PyPI.

This script handles building and publishing the package to PyPI.
Supports both TestPyPI (for testing) and PyPI (for production releases).

Usage:
    # Build only (no upload)
    ./deploy_pypi.py --build-only

    # Upload to TestPyPI (for testing)
    ./deploy_pypi.py --test

    # Upload to PyPI (production)
    ./deploy_pypi.py --production

    # Build and upload to both
    ./deploy_pypi.py --test --production

Requirements:
    pip install build twine
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run_command(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Runs a command and prints it.

    Args:
        cmd: Command and arguments to run.
        check: Whether to raise on non-zero exit code.

    Returns:
        CompletedProcess result.
    """
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def clean_dist() -> None:
    """Removes existing dist directory."""
    dist_dir = Path("dist")
    if dist_dir.exists():
        print(f"Cleaning {dist_dir}...")
        shutil.rmtree(dist_dir)


def build_package() -> bool:
    """Builds the package using python -m build.

    Returns:
        True if build succeeded.
    """
    print("\n=== Building package ===\n")
    try:
        run_command([sys.executable, "-m", "build"])
        return True
    except subprocess.CalledProcessError:
        print("Build failed!", file=sys.stderr)
        return False


def upload_to_testpypi() -> bool:
    """Uploads to TestPyPI.

    Returns:
        True if upload succeeded.
    """
    print("\n=== Uploading to TestPyPI ===\n")
    print("Note: You need a TestPyPI account and token.")
    print("Set TWINE_USERNAME=__token__ and TWINE_PASSWORD=your-token")
    print("Or use ~/.pypirc for credentials.\n")

    try:
        run_command([
            sys.executable, "-m", "twine", "upload",
            "--repository", "testpypi",
            "dist/*"
        ])
        print("\n✓ Uploaded to TestPyPI")
        print("  Install with: pip install --index-url https://test.pypi.org/simple/ code-recap")
        return True
    except subprocess.CalledProcessError:
        print("Upload to TestPyPI failed!", file=sys.stderr)
        return False


def upload_to_pypi() -> bool:
    """Uploads to production PyPI.

    Returns:
        True if upload succeeded.
    """
    print("\n=== Uploading to PyPI ===\n")
    print("Note: You need a PyPI account and token.")
    print("Set TWINE_USERNAME=__token__ and TWINE_PASSWORD=your-token")
    print("Or use ~/.pypirc for credentials.\n")

    try:
        run_command([
            sys.executable, "-m", "twine", "upload",
            "dist/*"
        ])
        print("\n✓ Uploaded to PyPI")
        print("  Install with: pip install code-recap")
        return True
    except subprocess.CalledProcessError:
        print("Upload to PyPI failed!", file=sys.stderr)
        return False


def check_version() -> str:
    """Reads and displays the current version.

    Returns:
        Version string.
    """
    # Read version from __init__.py
    init_file = Path("src/code_recap/__init__.py")
    version = "unknown"
    if init_file.exists():
        for line in init_file.read_text().splitlines():
            if line.startswith("__version__"):
                version = line.split("=")[1].strip().strip('"\'')
                break

    print(f"Package version: {version}")
    return version


def main() -> int:
    """Main entry point.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        description="Build and deploy code-recap to PyPI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --build-only          # Just build, don't upload
  %(prog)s --test                # Build and upload to TestPyPI
  %(prog)s --production          # Build and upload to PyPI
  %(prog)s --test --production   # Upload to both

Environment variables:
  TWINE_USERNAME   PyPI username (use __token__ for API tokens)
  TWINE_PASSWORD   PyPI password or API token
        """
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Only build the package, don't upload",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Upload to TestPyPI (test.pypi.org)",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Upload to production PyPI (pypi.org)",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Don't clean dist directory before building",
    )

    args = parser.parse_args()

    # Default to build-only if no upload target specified
    if not args.test and not args.production:
        args.build_only = True

    print("=== Code Recap PyPI Deployment ===\n")
    check_version()

    # Clean dist directory
    if not args.no_clean:
        clean_dist()

    # Build package
    if not build_package():
        return 1

    if args.build_only:
        print("\n✓ Build complete. Use --test or --production to upload.")
        return 0

    # Upload to TestPyPI
    if args.test:
        if not upload_to_testpypi():
            return 1

    # Upload to PyPI
    if args.production:
        if not upload_to_pypi():
            return 1

    print("\n=== Deployment complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
