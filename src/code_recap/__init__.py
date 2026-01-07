"""Code Recap - Git activity summary tools.

Tools for analyzing and summarizing git activity across multiple repositories,
with LLM-powered summaries and HTML report generation.
"""

__version__ = "1.3.0"
__author__ = "Code Recap Contributors"

from code_recap.paths import get_config_path, get_output_dir

__all__ = [
    "__version__",
    "get_config_path",
    "get_output_dir",
]
