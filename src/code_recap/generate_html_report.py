#!/usr/bin/env python3
"""Generate HTML reports from markdown activity summaries.

This script converts markdown activity summaries into an attractive, shareable
HTML website with client navigation and responsive design. Configuration is
loaded from config/html_report.yaml.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from code_recap.arguments import add_input_dir_arg, add_output_dir_arg
from code_recap.paths import get_config_path, get_output_dir

# Default configuration (generic - customize in config/config.yaml)
DEFAULT_CONFIG = {
    "company": {
        "name": "Your Company",
        "url": "",
        "logo": "",
    },
    "defaults": {
        "accent_primary": "#2196F3",
        "accent_secondary": "#1976D2",
    },
    "clients": {},
}

# Default client icons (generic fallbacks)
DEFAULT_CLIENT_ICONS = {
    "other": "📁",
}

# Month names for display
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# Short month names for compact date ranges
MONTH_NAMES_SHORT = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def get_date_range_label(clients: list["ClientData"], year_hint: str = "") -> tuple[str, str]:
    """Determines a human-readable date range label from available periods.

    Analyzes all periods across clients to generate an appropriate label like:
    - "2024" if all 12 months of a single year
    - "Q4 2024" if Oct-Dec
    - "H1 2024" if Jan-Jun
    - "Oct-Dec 2024" if a partial range
    - "2024-2025" if spanning multiple years

    Args:
        clients: List of client data containing periods.
        year_hint: Year extracted from filename (fallback if no periods).

    Returns:
        Tuple of (range_label, title_suffix) where:
        - range_label: e.g., "2024", "Q4 2024", "Oct-Dec 2024"
        - title_suffix: e.g., "Year in Review", "Quarter in Review", "Activity Summary"
    """
    all_periods: list[tuple[int, int]] = []
    for client in clients:
        for period in client.periods:
            all_periods.append((period.year, period.month))

    if not all_periods:
        if year_hint:
            return year_hint, "Year in Review"
        return "", "Activity Summary"

    all_periods.sort()
    min_year, min_month = all_periods[0]
    max_year, max_month = all_periods[-1]

    # Multi-year range
    if min_year != max_year:
        return f"{min_year}–{max_year}", "Activity Summary"

    year = min_year
    months = sorted({m for _, m in all_periods})

    # Full year (all 12 months)
    if len(months) == 12:
        return str(year), "Year in Review"

    # Check for standard quarters
    q1 = {1, 2, 3}
    q2 = {4, 5, 6}
    q3 = {7, 8, 9}
    q4 = {10, 11, 12}
    h1 = {1, 2, 3, 4, 5, 6}
    h2 = {7, 8, 9, 10, 11, 12}

    months_set = set(months)

    if months_set == q1:
        return f"Q1 {year}", "Quarter in Review"
    if months_set == q2:
        return f"Q2 {year}", "Quarter in Review"
    if months_set == q3:
        return f"Q3 {year}", "Quarter in Review"
    if months_set == q4:
        return f"Q4 {year}", "Quarter in Review"
    if months_set == h1:
        return f"H1 {year}", "Half-Year Summary"
    if months_set == h2:
        return f"H2 {year}", "Half-Year Summary"

    # Contiguous range within a year
    if len(months) == (max_month - min_month + 1):
        start_name = MONTH_NAMES_SHORT[min_month - 1]
        end_name = MONTH_NAMES_SHORT[max_month - 1]
        if min_month == max_month:
            return f"{MONTH_NAMES[min_month - 1]} {year}", "Activity Summary"
        return f"{start_name}–{end_name} {year}", "Activity Summary"

    # Non-contiguous months - just use year
    return str(year), "Activity Summary"


def get_css_styles(accent_primary: str, accent_secondary: str, theme: str = "light") -> str:
    """Generates CSS styles with custom accent colors and theme toggle support.

    Args:
        accent_primary: Primary accent color (CSS value).
        accent_secondary: Secondary accent color (CSS value).
        theme: Default color theme ("light" or "dark").

    Returns:
        Complete CSS stylesheet string.
    """
    return f"""
:root,
[data-theme="light"] {{
    --bg-primary: #f8fafc;
    --bg-secondary: #f1f5f9;
    --bg-tertiary: #e2e8f0;
    --bg-card: #ffffff;
    --text-primary: #1e293b;
    --text-secondary: #475569;
    --text-muted: #94a3b8;
    --border-color: #e2e8f0;
    --code-color: #0f766e;
    --code-bg: #f0fdfa;
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
    --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.07);
    --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.1);
    --nav-bg: rgba(248, 250, 252, 0.9);
    --bg-pattern-1: 5%;
    --bg-pattern-2: 4%;
    --accent-primary: {accent_primary};
    --accent-secondary: {accent_secondary};
    --accent-gradient: linear-gradient(135deg, {accent_primary} 0%, {accent_secondary} 100%);
    --success: #10b981;
    --warning: #f59e0b;
    --danger: #ef4444;
    --font-display: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-body: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-mono: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
    --shadow-glow: 0 0 20px color-mix(in srgb, {accent_primary} 20%, transparent);
    --radius-sm: 6px;
    --radius-md: 10px;
    --radius-lg: 16px;
}}

[data-theme="dark"] {{
    --bg-primary: #0f0f14;
    --bg-secondary: #1a1a24;
    --bg-tertiary: #252532;
    --bg-card: #1e1e2a;
    --text-primary: #e8e6e3;
    --text-secondary: #9d9d9d;
    --text-muted: #6b6b6b;
    --border-color: #2d2d3d;
    --code-color: #5eead4;
    --code-bg: #1e293b;
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
    --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.4);
    --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.5);
    --nav-bg: rgba(15, 15, 20, 0.9);
    --bg-pattern-1: 8%;
    --bg-pattern-2: 6%;
    --radius-lg: 16px;
}}

* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}

html {{
    scroll-behavior: smooth;
}}

body {{
    font-family: var(--font-body);
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.7;
    font-size: 16px;
    min-height: 100vh;
}}

/* Background pattern */
body::before {{
    content: '';
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background:
        radial-gradient(ellipse at 20% 0%, color-mix(in srgb, var(--accent-primary) var(--bg-pattern-1), transparent) 0%, transparent 50%),
        radial-gradient(ellipse at 80% 100%, color-mix(in srgb, var(--accent-secondary) var(--bg-pattern-2), transparent) 0%, transparent 50%);
    pointer-events: none;
    z-index: -1;
}}

a {{
    color: var(--accent-secondary);
    text-decoration: none;
    transition: color 0.2s ease;
}}

a:hover {{
    color: var(--accent-primary);
}}

/* Navigation */
.nav {{
    position: sticky;
    top: 0;
    background: var(--nav-bg);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border-color);
    z-index: 1000;
    padding: 0 2rem;
}}

.nav-inner {{
    max-width: 1400px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 72px;
}}

/* Collab brand header */
.nav-brand {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.15rem;
    color: var(--text-primary);
    transition: opacity 0.2s ease;
    text-decoration: none;
    padding: 0.5rem 0;
}}

.nav-brand:hover {{
    opacity: 0.85;
    color: var(--text-primary);
}}

.nav-brand-company {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-family: var(--font-display);
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-secondary);
    letter-spacing: 0.02em;
}}

.nav-brand-company svg {{
    height: 20px;
    width: auto;
}}

.nav-brand-separator {{
    font-size: 0.7rem;
    color: var(--text-muted);
    line-height: 1;
}}

.nav-brand-client {{
    font-family: var(--font-display);
    font-size: 1.1rem;
    font-weight: 700;
    background: var(--accent-gradient);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.01em;
}}

.nav-brand-client-logo {{
    display: flex;
    align-items: center;
}}

.nav-brand-client-logo svg {{
    height: 24px;
    width: auto;
    max-width: 120px;
}}

/* Simple brand for index page */
.nav-brand-simple {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
    color: var(--text-primary);
    transition: opacity 0.2s ease;
    text-decoration: none;
}}

.nav-brand-simple:hover {{
    opacity: 0.85;
    color: var(--text-primary);
}}

.nav-brand-simple svg {{
    height: 32px;
    width: auto;
}}

/* Standalone collab header (for client pages) */
.collab-header {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 1.5rem 2rem 1rem;
    gap: 0.25rem;
    text-decoration: none;
    color: var(--text-primary);
    transition: opacity 0.2s ease;
}}

.collab-header:hover {{
    opacity: 0.9;
    color: var(--text-primary);
}}

.collab-header-company {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
}}

.collab-header-company svg {{
    height: 48px;
    width: auto;
}}

.collab-header-separator {{
    font-size: 1.5rem;
    color: var(--text-muted);
    margin: 0.25rem 0;
    font-weight: 300;
}}

.collab-header-client {{
    font-family: var(--font-display);
    font-size: 2.25rem;
    font-weight: 800;
    background: var(--accent-gradient);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.02em;
}}

.collab-header-client-logo {{
    display: flex;
    align-items: center;
    justify-content: center;
}}

.collab-header-client-logo svg {{
    height: 52px;
    width: auto;
    max-width: 240px;
}}

/* Year subtitle for summary pages */
.year-subtitle {{
    text-align: center;
    font-family: var(--font-display);
    font-size: 1.1rem;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.15em;
    margin-bottom: 2rem;
}}

.nav-links {{
    display: flex;
    gap: 0.5rem;
    align-items: center;
}}

.nav-link {{
    padding: 0.5rem 1rem;
    border-radius: var(--radius-sm);
    font-size: 0.9rem;
    font-weight: 500;
    color: var(--text-secondary);
    transition: all 0.2s ease;
}}

.nav-link:hover {{
    background: var(--bg-tertiary);
    color: var(--text-primary);
}}

.nav-link.active {{
    background: var(--accent-gradient);
    color: var(--bg-primary);
}}

/* Theme toggle */
.theme-toggle {{
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-sm);
    padding: 0.5rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 38px;
    height: 38px;
    transition: all 0.2s ease;
    color: var(--text-secondary);
}}

.theme-toggle:hover {{
    background: var(--bg-secondary);
    border-color: var(--accent-primary);
    color: var(--text-primary);
}}

.theme-toggle svg {{
    width: 18px;
    height: 18px;
}}

.theme-toggle .sun-icon {{
    display: none;
}}

.theme-toggle .moon-icon {{
    display: block;
}}

[data-theme="dark"] .theme-toggle .sun-icon {{
    display: block;
}}

[data-theme="dark"] .theme-toggle .moon-icon {{
    display: none;
}}

/* Standalone header wrapper (for client pages) */
.standalone-header-wrapper {{
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 0.75rem 2rem 0;
    gap: 0.5rem;
    position: relative;
}}

.standalone-header-wrapper .theme-toggle {{
    position: absolute;
    top: 0.75rem;
    right: 1.5rem;
}}

/* Main content */
.main {{
    max-width: 1000px;
    margin: 0 auto;
    padding: 3rem 2rem 5rem;
}}

/* Hero section */
.hero {{
    text-align: center;
    padding: 4rem 0;
    margin-bottom: 3rem;
}}

.hero-badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 1rem;
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: 100px;
    font-size: 0.85rem;
    color: var(--text-secondary);
    margin-bottom: 1.5rem;
}}

.hero-badge::before {{
    content: '';
    width: 8px;
    height: 8px;
    background: var(--success);
    border-radius: 50%;
    animation: pulse 2s infinite;
}}

@keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.5; }}
}}

.hero h1 {{
    font-family: var(--font-display);
    font-size: clamp(2.5rem, 5vw, 3.5rem);
    font-weight: 800;
    line-height: 1.1;
    margin-bottom: 1rem;
    letter-spacing: -0.02em;
}}

.hero h1 .gradient {{
    background: var(--accent-gradient);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}

.hero-subtitle {{
    font-size: 1.1rem;
    color: var(--text-secondary);
    max-width: 600px;
    margin: 0 auto;
}}

/* Internal summary section */
/* Public summary section (for blog posts, external sharing) */
.public-summary {{
    max-width: 900px;
    margin: 0 auto 3rem;
}}

.public-summary .content {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 2rem;
}}

.public-summary h2:first-child {{
    margin-top: 0;
}}

/* Internal summary section */
.internal-summary {{
    max-width: 900px;
    margin: 0 auto 3rem;
}}

.internal-summary .content {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 2rem;
}}

/* Summary page cards on overview */
.summary-cards {{
    display: flex;
    gap: 1.5rem;
    max-width: 900px;
    margin: 0 auto 3rem;
    flex-wrap: wrap;
}}

.summary-card {{
    flex: 1;
    min-width: 280px;
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 1.25rem 1.5rem;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    text-decoration: none;
    color: inherit;
    transition: all 0.2s ease;
}}

.summary-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
    border-color: var(--accent-primary);
}}

.summary-card-icon {{
    font-size: 2rem;
    flex-shrink: 0;
}}

.summary-card-content {{
    flex: 1;
}}

.summary-card-content h3 {{
    font-size: 1.1rem;
    font-weight: 600;
    margin: 0 0 0.25rem;
    color: var(--text-primary);
}}

.summary-card-content p {{
    font-size: 0.875rem;
    color: var(--text-secondary);
    margin: 0;
    line-height: 1.4;
}}

.summary-card-arrow {{
    font-size: 1.25rem;
    color: var(--text-secondary);
    transition: transform 0.2s ease;
}}

.summary-card:hover .summary-card-arrow {{
    transform: translateX(4px);
    color: var(--accent-primary);
}}

/* Page header for summary pages */
.summary-page-header {{
    text-align: center;
    margin-bottom: 2rem;
}}

.summary-page-header h1 {{
    font-size: 2rem;
    font-weight: 700;
    margin: 0 0 0.5rem;
}}

.summary-page-header .badge {{
    display: inline-block;
    padding: 0.25rem 0.75rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}

.summary-page-header .badge.public {{
    background: linear-gradient(135deg, #10b981, #059669);
    color: white;
}}

.summary-page-header .badge.internal {{
    background: linear-gradient(135deg, #6366f1, #4f46e5);
    color: white;
}}

.summary-back-link {{
    display: inline-block;
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 0.9rem;
    padding: 0.5rem 1rem;
    border-radius: 6px;
    transition: all 0.2s ease;
}}

.summary-back-link:hover {{
    color: var(--accent-primary);
    background: var(--bg-card);
}}

.clients-heading {{
    max-width: 900px;
    margin: 0 auto 1.5rem;
    font-size: 1.5rem;
    font-weight: 600;
    color: var(--text-primary);
}}

/* Cards grid */
.cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1.5rem;
    margin-bottom: 3rem;
}}

.card {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 1.75rem;
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
}}

.card::before {{
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: var(--accent-gradient);
    opacity: 0;
    transition: opacity 0.3s ease;
}}

.card:hover {{
    border-color: var(--accent-primary);
    transform: translateY(-4px);
    box-shadow: var(--shadow-glow);
}}

.card:hover::before {{
    opacity: 1;
}}

.card-icon {{
    width: 48px;
    height: 48px;
    background: var(--bg-tertiary);
    border-radius: var(--radius-md);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 1rem;
    font-size: 1.5rem;
}}

.card-logo {{
    height: 40px;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
}}

.card-logo svg {{
    height: 100%;
    width: auto;
    max-width: 140px;
}}

.card h3 {{
    font-family: var(--font-display);
    font-size: 1.25rem;
    font-weight: 700;
    margin-bottom: 0.5rem;
    color: var(--text-primary);
}}

.card p {{
    color: var(--text-secondary);
    font-size: 0.95rem;
    margin-bottom: 1rem;
}}

.card-meta {{
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    font-size: 0.85rem;
    color: var(--text-muted);
}}

.card-meta span {{
    display: flex;
    align-items: center;
    gap: 0.35rem;
}}

/* Period navigation */
.period-nav {{
    margin-bottom: 0.5rem;
}}

.back-link {{
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    color: var(--text-muted);
    font-size: 0.85rem;
    font-weight: 500;
    text-decoration: none;
    transition: all 0.2s ease;
}}

.back-link:hover {{
    color: var(--accent-secondary);
}}

/* Page header */
.page-header {{
    margin-bottom: 2rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border-color);
}}

.breadcrumb {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.9rem;
    color: var(--text-muted);
    margin-bottom: 1rem;
}}

.breadcrumb a {{
    color: var(--text-secondary);
}}

.breadcrumb span {{
    color: var(--text-muted);
}}

.page-header h1 {{
    font-family: var(--font-display);
    font-size: 2.5rem;
    font-weight: 800;
    margin-bottom: 0.75rem;
    letter-spacing: -0.02em;
}}

.page-header .meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 1.5rem;
    color: var(--text-secondary);
    font-size: 0.95rem;
}}

.page-header .meta span {{
    display: flex;
    align-items: center;
    gap: 0.4rem;
}}

/* Stats grid */
.stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
}}

.stats-item {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    padding: 1rem 1.25rem;
}}

.stats-label {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.5rem;
}}

.stats-icon {{
    font-size: 1rem;
}}

.stats-value {{
    font-size: 0.95rem;
    color: var(--text-primary);
    line-height: 1.5;
}}

/* Content styling */
.content {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 2.5rem;
}}

.content h2 {{
    font-family: var(--font-display);
    font-size: 1.5rem;
    font-weight: 700;
    margin: 2.5rem 0 1rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--border-color);
    color: var(--text-primary);
}}

.content h2:first-child {{
    margin-top: 0;
    padding-top: 0;
    border-top: none;
}}

.content h3 {{
    font-family: var(--font-display);
    font-size: 1.2rem;
    font-weight: 600;
    margin: 2rem 0 0.75rem;
    color: var(--text-primary);
}}

.content p {{
    margin-bottom: 1rem;
    color: var(--text-secondary);
}}

.content p:last-child {{
    margin-bottom: 0;
}}

.content strong {{
    color: var(--text-primary);
    font-weight: 600;
}}

.content em {{
    color: var(--text-secondary);
    font-style: italic;
}}

.content ul, .content ol {{
    margin: 1rem 0 1.5rem;
    padding-left: 1.5rem;
}}

.content li {{
    margin-bottom: 0.6rem;
    color: var(--text-secondary);
}}

.content li strong {{
    color: var(--text-primary);
}}

.content code {{
    font-family: var(--font-mono);
    font-size: 0.9em;
    background: var(--code-bg);
    padding: 0.2em 0.5em;
    border-radius: var(--radius-sm);
    color: var(--code-color);
}}

.content pre {{
    background: var(--bg-tertiary);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    padding: 1.25rem;
    overflow-x: auto;
    margin: 1.5rem 0;
}}

.content pre code {{
    background: none;
    padding: 0;
    font-size: 0.9rem;
    line-height: 1.6;
}}

.content blockquote {{
    border-left: 3px solid var(--accent-primary);
    padding-left: 1.25rem;
    margin: 1.5rem 0;
    color: var(--text-secondary);
    font-style: italic;
}}

.content hr {{
    border: none;
    height: 1px;
    background: var(--border-color);
    margin: 2rem 0;
}}

/* Tables */
.content table {{
    width: 100%;
    border-collapse: collapse;
    margin: 1.5rem 0;
    font-size: 0.95rem;
}}

.content th, .content td {{
    text-align: left;
    padding: 0.875rem 1rem;
    border-bottom: 1px solid var(--border-color);
}}

.content th {{
    background: var(--bg-tertiary);
    font-weight: 600;
    color: var(--text-primary);
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}

.content th:first-child {{
    border-top-left-radius: var(--radius-sm);
}}

.content th:last-child {{
    border-top-right-radius: var(--radius-sm);
}}

.content td {{
    color: var(--text-secondary);
}}

.content tr:hover td {{
    background: color-mix(in srgb, var(--accent-primary) 5%, transparent);
}}

/* Charts section */
.charts-section {{
    margin: 2rem 0 3rem;
}}

.charts-section h2 {{
    font-family: var(--font-display);
    font-size: 1.5rem;
    font-weight: 700;
    margin-bottom: 1.5rem;
}}

.charts-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1.5rem;
}}

@media (max-width: 900px) {{
    .charts-grid {{
        grid-template-columns: 1fr;
    }}
}}

.chart-container {{
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 1.5rem;
    box-shadow: var(--shadow-sm);
}}

.chart-container h3 {{
    font-family: var(--font-display);
    font-size: 1rem;
    font-weight: 600;
    margin-bottom: 1rem;
    color: var(--text-secondary);
}}

.chart-container canvas {{
    width: 100% !important;
}}

/* Period navigation */
.periods-section {{
    margin-top: 3rem;
}}

.periods-section h2 {{
    font-family: var(--font-display);
    font-size: 1.5rem;
    font-weight: 700;
    margin-bottom: 1.5rem;
}}

.periods-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 1rem;
}}

.period-link {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 1.25rem 1rem;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-weight: 500;
    transition: all 0.2s ease;
    text-align: center;
}}

.period-link:hover {{
    border-color: var(--accent-primary);
    color: var(--text-primary);
    transform: translateY(-2px);
    box-shadow: var(--shadow-glow);
}}

.period-link.current {{
    background: var(--accent-gradient);
    border-color: transparent;
    color: var(--bg-primary);
    cursor: default;
}}

.period-link.current:hover {{
    transform: none;
    box-shadow: none;
}}

.period-link.current .month,
.period-link.current .year {{
    color: var(--bg-primary);
}}

.period-link .month {{
    font-family: var(--font-display);
    font-size: 1.1rem;
    font-weight: 600;
}}

.period-link .year {{
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-top: 0.25rem;
}}

/* Footer */
.footer {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 2rem;
    text-align: center;
    color: var(--text-muted);
    font-size: 0.875rem;
    border-top: 1px solid var(--border-color);
}}

.footer a {{
    color: var(--text-secondary);
}}

.footer a:hover {{
    color: var(--accent-primary);
}}

/* Responsive */
@media (max-width: 768px) {{
    .nav {{
        padding: 0 1rem;
    }}

    .nav-inner {{
        height: 64px;
    }}

    .nav-brand-company {{
        font-size: 0.75rem;
    }}

    .nav-brand-company svg {{
        height: 16px;
    }}

    .nav-brand-client {{
        font-size: 0.95rem;
    }}

    .nav-brand-simple svg {{
        height: 24px;
    }}

    .nav-links {{
        gap: 0.25rem;
    }}

    .nav-link {{
        padding: 0.4rem 0.75rem;
        font-size: 0.85rem;
    }}

    .main {{
        padding: 2rem 1rem 4rem;
    }}

    .hero {{
        padding: 2rem 0;
    }}

    .content {{
        padding: 1.5rem;
    }}

    .cards-grid {{
        grid-template-columns: 1fr;
    }}

    .page-header h1 {{
        font-size: 2rem;
    }}
}}
"""


@dataclass
class CompanyConfig:
    """Company branding configuration.

    Attributes:
        name: Company name to display.
        url: Company website URL.
        logo: SVG logo markup.
    """

    name: str = "Your Company"
    url: str = ""
    logo: str = ""


@dataclass
class ClientBranding:
    """Client-specific branding configuration.

    Attributes:
        accent_primary: Primary accent color.
        accent_secondary: Secondary accent color.
        logo: Client logo SVG markup.
        icon: Icon emoji for cards.
        company_override: Optional company to use instead of the default.
    """

    accent_primary: str = "#2FF6FB"
    accent_secondary: str = "#047CC3"
    logo: str = ""
    icon: str = "📊"
    company_override: Optional["CompanyConfig"] = None


@dataclass
class ReportConfig:
    """Complete configuration for report generation.

    Attributes:
        theme: Color theme ("light" or "dark").
        company: Company branding configuration.
        defaults: Default client branding.
        clients: Per-client branding overrides.
    """

    theme: str = "light"
    company: CompanyConfig = field(default_factory=CompanyConfig)
    defaults: ClientBranding = field(default_factory=ClientBranding)
    clients: dict[str, ClientBranding] = field(default_factory=dict)

    def get_client_branding(self, client_slug: str) -> ClientBranding:
        """Gets branding for a specific client.

        Args:
            client_slug: Client directory name (case-insensitive lookup).

        Returns:
            ClientBranding with client-specific or default values.
        """
        # Try exact match first
        if client_slug in self.clients:
            return self.clients[client_slug]

        # Try case-insensitive match
        slug_lower = client_slug.lower()
        for name, branding in self.clients.items():
            if name.lower() == slug_lower or name.lower().replace(" ", "_") == slug_lower:
                return branding

        # Return defaults with appropriate icon
        icon = DEFAULT_CLIENT_ICONS.get(slug_lower, "📊")
        return ClientBranding(
            accent_primary=self.defaults.accent_primary,
            accent_secondary=self.defaults.accent_secondary,
            logo="",
            icon=icon,
        )


_logo_counter = 0


def _make_svg_classes_unique(svg_content: str) -> str:
    """Makes SVG class names unique to avoid conflicts when multiple SVGs are embedded.

    Args:
        svg_content: SVG markup string.

    Returns:
        SVG with uniquely prefixed class names.
    """
    global _logo_counter
    _logo_counter += 1
    prefix = f"svg{_logo_counter}_"

    # Find all class definitions in style blocks and update them
    import re

    def replace_class_def(match):
        return f".{prefix}{match.group(1)}{{"

    def replace_class_use(match):
        classes = match.group(1)
        new_classes = " ".join(f"{prefix}{c}" for c in classes.split())
        return f'class="{new_classes}"'

    # Replace class definitions in style blocks (.st0, .cls-1, etc.)
    svg_content = re.sub(r"\.([a-zA-Z][a-zA-Z0-9_-]*)\s*\{", replace_class_def, svg_content)

    # Replace class usages in elements
    svg_content = re.sub(r'class="([^"]+)"', replace_class_use, svg_content)

    # Also handle IDs that might be referenced (like gradient IDs)
    def replace_id_def(match):
        return f'id="{prefix}{match.group(1)}"'

    def replace_id_ref(match):
        return f"url(#{prefix}{match.group(1)})"

    svg_content = re.sub(r'id="([^"]+)"', replace_id_def, svg_content)
    svg_content = re.sub(r"url\(#([^)]+)\)", replace_id_ref, svg_content)

    return svg_content


def _resolve_logo(logo_value: str, config_dir: Path) -> str:
    """Resolves a logo value to SVG markup.

    Args:
        logo_value: Logo path or inline SVG.
        config_dir: Directory containing the config file (for relative paths).

    Returns:
        SVG markup string, or empty string if not found.
    """
    if not logo_value:
        return ""

    logo_value = logo_value.strip()
    svg_content = ""

    # Check if it's inline SVG
    if logo_value.startswith("<"):
        svg_content = logo_value
    else:
        # Try as file path
        logo_path = Path(logo_value)

        # Check absolute path first
        if logo_path.is_absolute() and logo_path.exists():
            svg_content = logo_path.read_text(encoding="utf-8")
        else:
            # Try relative to config directory
            relative_path = config_dir / logo_value
            if relative_path.exists():
                svg_content = relative_path.read_text(encoding="utf-8")

    # Make class names unique to avoid conflicts
    if svg_content:
        svg_content = _make_svg_classes_unique(svg_content)

    return svg_content


def load_config(config_path: Path) -> ReportConfig:
    """Loads configuration from a YAML file.

    Supports both the unified config.yaml format (with html_report section)
    and the legacy html_report.yaml format.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        ReportConfig with loaded values, or defaults if file not found.
    """
    config = ReportConfig()

    if not config_path.exists():
        return config

    config_dir = config_path.parent

    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
    except ImportError:
        print(
            "Warning: PyYAML not installed. Using default configuration.",
            file=sys.stderr,
        )
        return config

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)

        if not data:
            return config

        # Check if this is unified config format (has html_report section)
        if "html_report" in data:
            data = data["html_report"]

        # Load theme
        config.theme = data.get("theme", "light")

        # Load company config
        if "company" in data:
            company_data = data["company"]
            logo_svg = _resolve_logo(company_data.get("logo", ""), config_dir)
            config.company = CompanyConfig(
                name=company_data.get("name", config.company.name),
                url=company_data.get("url", config.company.url),
                logo=logo_svg,
            )

        # Load defaults
        if "defaults" in data:
            defaults_data = data["defaults"]
            config.defaults = ClientBranding(
                accent_primary=defaults_data.get("accent_primary", "#2FF6FB"),
                accent_secondary=defaults_data.get("accent_secondary", "#047CC3"),
            )

        # Load client-specific config
        if "clients" in data and data["clients"]:
            for client_name, client_data in data["clients"].items():
                if not client_data:
                    continue
                client_logo = _resolve_logo(client_data.get("logo", ""), config_dir)

                # Handle company_override if specified
                company_override = None
                if "company_override" in client_data:
                    override_data = client_data["company_override"]
                    override_logo = _resolve_logo(override_data.get("logo", ""), config_dir)
                    company_override = CompanyConfig(
                        name=override_data.get("name", ""),
                        url=override_data.get("url", ""),
                        logo=override_logo,
                    )

                config.clients[client_name] = ClientBranding(
                    accent_primary=client_data.get(
                        "accent_primary", config.defaults.accent_primary
                    ),
                    accent_secondary=client_data.get(
                        "accent_secondary", config.defaults.accent_secondary
                    ),
                    logo=client_logo,
                    icon=client_data.get("icon", "📊"),
                    company_override=company_override,
                )

    except Exception as e:
        print(f"Warning: Failed to load config: {e}", file=sys.stderr)

    return config


@dataclass
class PeriodStats:
    """Statistics for a single period."""

    commits: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    files: int = 0
    active_days: int = 0


@dataclass
class Period:
    """Represents a single period (month) of activity."""

    filename: str
    year: int
    month: int
    content_html: str
    stats: PeriodStats = field(default_factory=PeriodStats)

    @property
    def label(self) -> str:
        """Returns a human-readable label for the period."""
        return MONTH_NAMES[self.month - 1]

    @property
    def slug(self) -> str:
        """Returns the URL slug for the period."""
        return f"{self.year}-{self.month:02d}"


@dataclass
class ClientData:
    """Data for a single client."""

    name: str
    slug: str
    summary_html: str = ""
    author: str = ""
    generated: str = ""
    periods: list[Period] = field(default_factory=list)
    languages: list[tuple[str, int]] = field(default_factory=list)
    projects: list[tuple[str, int]] = field(default_factory=list)


def _convert_stats_section(text: str) -> str:
    """Converts Stats/Languages/Projects lines into a styled grid.

    Detects consecutive lines starting with **Stats:**, **Languages:**, **Projects:**
    and converts them into a styled stats grid.
    """
    lines = text.split("\n")
    result = []
    stats_block = []

    for line in lines:
        # Check if this is a stats-type line
        if re.match(r"^\*\*(Stats|Languages|Projects|Client):\*\*", line):
            stats_block.append(line)
        else:
            # If we have accumulated stats lines, convert them
            if stats_block:
                result.append(_build_stats_html(stats_block))
                stats_block = []
            result.append(line)

    # Handle any remaining stats block
    if stats_block:
        result.append(_build_stats_html(stats_block))

    return "\n".join(result)


def _build_stats_html(lines: list[str]) -> str:
    """Builds HTML for a stats section."""
    items = []

    for line in lines:
        # Skip Client line as it's redundant on client pages
        if line.startswith("**Client:**"):
            continue

        # Parse the line: **Label:** content
        match = re.match(r"^\*\*(.+?):\*\*\s*(.+)$", line)
        if match:
            label = match.group(1)
            content = match.group(2)

            # Choose icon based on label
            if label == "Stats":
                icon = "📊"
            elif label == "Languages":
                icon = "💻"
            elif label == "Projects":
                icon = "📁"
            else:
                icon = "📋"

            items.append(f"""<div class="stats-item">
                <div class="stats-label"><span class="stats-icon">{icon}</span>{label}</div>
                <div class="stats-value">{content}</div>
            </div>""")

    if not items:
        return ""

    return f'<div class="stats-grid">{"".join(items)}</div>'


def markdown_to_html(markdown_text: str) -> str:
    """Converts markdown text to HTML.

    Supports basic markdown features: headers, bold, italic, lists, code,
    tables, links, and horizontal rules.

    Args:
        markdown_text: Raw markdown content.

    Returns:
        Converted HTML string.
    """
    html = markdown_text

    # Extract and convert stats section BEFORE escaping (so we can detect ** markers)
    stats_placeholder = "___STATS_GRID_PLACEHOLDER___"
    stats_html = ""

    # Find stats lines and replace with placeholder
    lines = html.split("\n")
    new_lines = []
    stats_lines = []

    for line in lines:
        if re.match(r"^\*\*(Stats|Languages|Projects|Client):\*\*", line):
            stats_lines.append(line)
        else:
            if stats_lines:
                # Convert accumulated stats and add placeholder
                stats_html = _build_stats_html(stats_lines)
                new_lines.append(stats_placeholder)
                stats_lines = []
            new_lines.append(line)

    # Handle any remaining stats at end
    if stats_lines:
        stats_html = _build_stats_html(stats_lines)
        new_lines.append(stats_placeholder)

    html = "\n".join(new_lines)

    # Escape HTML special chars first (but preserve markdown syntax)
    html = html.replace("&", "&amp;")
    html = html.replace("<", "&lt;")
    html = html.replace(">", "&gt;")

    # Restore stats grid HTML (already properly formatted, doesn't need escaping)
    if stats_html:
        html = html.replace(stats_placeholder, stats_html)

    # Convert tables (must be done before other processing)
    html = _convert_tables(html)

    # Headers (h1-h3, skip the title which we handle separately)
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)

    # Horizontal rules
    html = re.sub(r"^---+$", r"<hr>", html, flags=re.MULTILINE)

    # Convert lists BEFORE bold/italic to avoid confusion with * markers
    html = _convert_nested_lists(html)

    # Bold and italic (after lists so * markers aren't confused)
    html = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", html)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)

    # Inline code
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)

    # Links
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)

    # Paragraphs
    html = _convert_paragraphs(html)

    return html


def _convert_tables(html: str) -> str:
    """Converts markdown tables to HTML tables."""
    lines = html.split("\n")
    result = []
    in_table = False
    table_lines = []

    for line in lines:
        if re.match(r"^\|.+\|$", line.strip()):
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(line)
        else:
            if in_table:
                result.append(_build_table(table_lines))
                in_table = False
                table_lines = []
            result.append(line)

    if in_table and table_lines:
        result.append(_build_table(table_lines))

    return "\n".join(result)


def _build_table(lines: list[str]) -> str:
    """Builds an HTML table from markdown table lines."""
    if len(lines) < 2:
        return "\n".join(lines)

    rows = []
    for line in lines:
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return ""

    html = ["<table>"]
    html.append("<thead><tr>")
    for cell in rows[0]:
        html.append(f"<th>{cell}</th>")
    html.append("</tr></thead>")

    if len(rows) > 1:
        html.append("<tbody>")
        for row in rows[1:]:
            html.append("<tr>")
            for cell in row:
                html.append(f"<td>{cell}</td>")
            html.append("</tr>")
        html.append("</tbody>")

    html.append("</table>")
    return "".join(html)


def _convert_nested_lists(html: str) -> str:
    """Converts markdown lists (ordered and unordered) to HTML with nesting support."""
    lines = html.split("\n")
    result = []
    list_stack = []  # Stack of (indent_level, list_type)

    def get_indent(line: str) -> int:
        """Returns the number of leading spaces."""
        return len(line) - len(line.lstrip())

    def close_lists_to_level(target_indent: int):
        """Closes lists down to the target indentation level."""
        while list_stack and list_stack[-1][0] >= target_indent:
            _, list_type = list_stack.pop()
            result.append(f"</li></{list_type}>")

    def close_all_lists():
        """Closes all open lists."""
        while list_stack:
            _, list_type = list_stack.pop()
            result.append(f"</li></{list_type}>")

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for ordered list item: "1. " or "1.  " with optional leading spaces
        ol_match = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
        # Check for unordered list item: "* " or "- " or "+ " with optional leading spaces
        ul_match = re.match(r"^(\s*)[\*\-\+]\s+(.+)$", line)

        if ol_match or ul_match:
            if ol_match:
                indent = len(ol_match.group(1))
                content = ol_match.group(2)
                list_type = "ol"
            else:
                indent = len(ul_match.group(1))
                content = ul_match.group(2)
                list_type = "ul"

            if not list_stack:
                # Start a new list
                result.append(f"<{list_type}>")
                result.append(f"<li>{content}")
                list_stack.append((indent, list_type))
            elif indent > list_stack[-1][0]:
                # Nested list - start a new sublist
                result.append(f"<{list_type}>")
                result.append(f"<li>{content}")
                list_stack.append((indent, list_type))
            elif indent < list_stack[-1][0]:
                # Going back up - close nested lists
                close_lists_to_level(indent + 1)
                if list_stack and list_stack[-1][1] == list_type:
                    result.append("</li>")
                    result.append(f"<li>{content}")
                else:
                    # Different list type at same level
                    if list_stack:
                        close_lists_to_level(indent)
                    result.append(f"<{list_type}>")
                    result.append(f"<li>{content}")
                    list_stack.append((indent, list_type))
            else:
                # Same level
                if list_stack[-1][1] == list_type:
                    result.append("</li>")
                    result.append(f"<li>{content}")
                else:
                    # Different list type - close current, start new
                    _, old_type = list_stack.pop()
                    result.append(f"</li></{old_type}>")
                    result.append(f"<{list_type}>")
                    result.append(f"<li>{content}")
                    list_stack.append((indent, list_type))
        else:
            # Not a list item
            if list_stack:
                # Check if this is continuation text for the current list item
                stripped = line.strip()
                if stripped and not stripped.startswith("<"):
                    # Continuation of list item - append to current item
                    result.append(f" {stripped}")
                elif stripped:
                    # Other content - close all lists first
                    close_all_lists()
                    result.append(line)
                else:
                    # Empty line - might end the list
                    # Look ahead to see if more list items follow
                    has_more_list = False
                    for j in range(i + 1, min(i + 3, len(lines))):
                        if re.match(r"^\s*(\d+\.|\*|\-|\+)\s+", lines[j]):
                            has_more_list = True
                            break
                        elif lines[j].strip():
                            break

                    if not has_more_list:
                        close_all_lists()
                    result.append(line)
            else:
                result.append(line)

        i += 1

    # Close any remaining open lists
    close_all_lists()

    return "\n".join(result)


def _convert_paragraphs(html: str) -> str:
    """Wraps text blocks in paragraph tags."""
    lines = html.split("\n")
    result = []
    paragraph = []

    def flush_paragraph():
        if paragraph:
            text = " ".join(paragraph)
            if text.strip():
                result.append(f"<p>{text.strip()}</p>")
            paragraph.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<") or stripped.startswith("#") or not stripped:
            flush_paragraph()
            if stripped:
                result.append(line)
        else:
            paragraph.append(stripped)

    flush_paragraph()
    return "\n".join(result)


def extract_metadata(markdown_text: str) -> tuple[str, str, str]:
    """Extracts metadata from markdown content."""
    client = ""
    author = ""
    generated = ""

    client_match = re.search(r"\*\*Client:\*\*\s*(.+)", markdown_text)
    if client_match:
        client = client_match.group(1).strip()

    author_match = re.search(r"\*\*Author:\*\*\s*(.+)", markdown_text)
    if author_match:
        author = author_match.group(1).strip()

    generated_match = re.search(r"\*\*Generated:\*\*\s*(.+)", markdown_text)
    if generated_match:
        generated = generated_match.group(1).strip()

    return client, author, generated


def strip_header_metadata(markdown_text: str, strip_stats: bool = False) -> str:
    """Removes the header metadata section from markdown.

    Args:
        markdown_text: Raw markdown content.
        strip_stats: If True, also removes Stats/Languages/Projects lines.

    Returns:
        Cleaned markdown text.
    """
    text = re.sub(r"^# Activity Summary:.+\n+", "", markdown_text)
    text = re.sub(r"^\*\*Client:\*\*.+\n+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\*\*Author:\*\*.+\n+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\*\*Generated:\*\*.+\n+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\*\*Cost:\*\*.+\n+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^---+\n+", "", text, flags=re.MULTILINE)

    if strip_stats:
        # Remove Stats/Languages/Projects lines (covered by charts)
        text = re.sub(r"^\*\*Stats:\*\*.+\n*", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\*\*Languages:\*\*.+\n*", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\*\*Projects:\*\*.+\n*", "", text, flags=re.MULTILINE)
        # Remove empty Overview section if it only contained stats
        text = re.sub(r"^## Overview\s*\n+(?=##|\Z)", "", text, flags=re.MULTILINE)

    return text.strip()


def format_client_name(slug: str) -> str:
    """Converts a client slug to a display name."""
    return slug.replace("_", " ").title()


def get_company_logo(config: ReportConfig) -> str:
    """Gets the company logo SVG markup.

    Args:
        config: Report configuration.

    Returns:
        SVG markup string, or empty string if no logo configured.
    """
    if config.company.logo:
        return config.company.logo
    return ""


def generate_html_page(
    title: str,
    content: str,
    nav_items: list[tuple[str, str, bool]],
    config: ReportConfig,
    client_name: Optional[str] = None,
    branding: Optional[ClientBranding] = None,
    breadcrumbs: Optional[list[tuple[str, str]]] = None,
    use_standalone_header: bool = False,
) -> str:
    """Generates a complete HTML page.

    Args:
        title: Page title for the <title> tag.
        content: Main content HTML.
        nav_items: List of (label, href, is_active) tuples for navigation.
        config: Report configuration.
        client_name: Client name for collab header (None for index page).
        branding: Client-specific branding (None uses defaults).
        breadcrumbs: Optional list of (label, href) tuples for breadcrumb.
        use_standalone_header: If True, uses centered collab header without nav bar.

    Returns:
        Complete HTML document string.
    """
    if branding is None:
        branding = config.defaults

    # Use company override if specified for this client
    effective_company = branding.company_override if branding.company_override else config.company

    css = get_css_styles(branding.accent_primary, branding.accent_secondary, config.theme)
    company_logo = effective_company.logo if effective_company.logo else ""
    client_logo = branding.logo if branding.logo else ""

    nav_html = ""
    for label, href, is_active in nav_items:
        active_class = " active" if is_active else ""
        nav_html += f'<a href="{href}" class="nav-link{active_class}">{label}</a>\n'

    breadcrumb_html = ""
    if breadcrumbs:
        parts = []
        for i, (label, href) in enumerate(breadcrumbs):
            if i == len(breadcrumbs) - 1:
                parts.append(f"<span>{label}</span>")
            else:
                parts.append(f'<a href="{href}">{label}</a>')
        breadcrumb_html = f'<div class="breadcrumb">{" › ".join(parts)}</div>'

    # Build header HTML based on page type
    # Client display: use logo if available, otherwise gradient text
    if client_logo:
        client_display = f'<span class="collab-header-client-logo">{client_logo}</span>'
        client_display_nav = f'<span class="nav-brand-client-logo">{client_logo}</span>'
    else:
        client_display = f'<span class="collab-header-client">{client_name}</span>'
        client_display_nav = f'<span class="nav-brand-client">{client_name}</span>'

    # Theme toggle button HTML
    theme_toggle = """<button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">
            <svg class="sun-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="5"></circle>
                <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"></path>
            </svg>
            <svg class="moon-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
            </svg>
        </button>"""

    company_url = effective_company.url if effective_company.url else "#"

    if use_standalone_header and client_name:
        # Centered collab header for client pages (no nav bar)
        header_html = f"""
    <div class="standalone-header-wrapper">
        <a href="{company_url}" class="collab-header" target="_blank" rel="noopener">
            <span class="collab-header-company">
                {company_logo}
            </span>
            <span class="collab-header-separator">×</span>
            {client_display}
        </a>
        {theme_toggle}
    </div>
"""
    elif client_name:
        # Collab-style header in nav bar
        header_html = f"""
    <nav class="nav">
        <div class="nav-inner">
            <a href="{company_url}" class="nav-brand" target="_blank" rel="noopener">
                <span class="nav-brand-company">
                    {company_logo}
                </span>
                <span class="nav-brand-separator">×</span>
                {client_display_nav}
            </a>
            <div class="nav-links">
{nav_html}
                {theme_toggle}
            </div>
        </div>
    </nav>
"""
    else:
        # Simple header for index page
        header_html = f"""
    <nav class="nav">
        <div class="nav-inner">
            <a href="{company_url}" class="nav-brand-simple" target="_blank" rel="noopener">
                {company_logo}
            </a>
            <div class="nav-links">
{nav_html}
                {theme_toggle}
            </div>
        </div>
    </nav>
"""

    default_theme = config.theme

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{default_theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&family=Outfit:wght@600;700;800&display=swap" rel="stylesheet">
    <script>
        (function() {{
            const saved = localStorage.getItem('theme');
            if (saved) {{
                document.documentElement.setAttribute('data-theme', saved);
            }}
        }})();
    </script>
    <style>
{css}
    </style>
</head>
<body>
    {header_html}

    <main class="main">
        {breadcrumb_html}
        {content}
    </main>

    <footer class="footer">
        Generated on {datetime.now().strftime("%B %d, %Y")} by <a href="{config.company.url}" target="_blank" rel="noopener">{config.company.name}</a>
        with <a href="https://github.com/nrb-tech/code-recap" target="_blank" rel="noopener">code-recap</a>
    </footer>

    <script>
        function toggleTheme() {{
            const html = document.documentElement;
            const current = html.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            html.setAttribute('data-theme', next);
            localStorage.setItem('theme', next);
        }}
    </script>
</body>
</html>
"""


def parse_stats_from_markdown(md_content: str) -> PeriodStats:
    """Parses statistics from markdown content.

    Looks for **Stats:** line in format:
    **Stats:** 112 commits, +6,504/-2,314 lines, 125 files, 18 active days

    Args:
        md_content: Raw markdown content.

    Returns:
        PeriodStats with parsed values.
    """
    stats = PeriodStats()

    # Match: **Stats:** 112 commits, +6,504/-2,314 lines, 125 files, 18 active days
    stats_match = re.search(
        r"\*\*Stats:\*\*\s*(\d[\d,]*)\s*commits?,\s*\+?([\d,]+)/-([\d,]+)\s*lines?",
        md_content,
    )
    if stats_match:
        stats.commits = int(stats_match.group(1).replace(",", ""))
        stats.lines_added = int(stats_match.group(2).replace(",", ""))
        stats.lines_removed = int(stats_match.group(3).replace(",", ""))

    # Also try to get files and active days
    files_match = re.search(r"([\d,]+)\s*files?", md_content)
    if files_match:
        stats.files = int(files_match.group(1).replace(",", ""))

    days_match = re.search(r"([\d,]+)\s*active\s*days?", md_content)
    if days_match:
        stats.active_days = int(days_match.group(1).replace(",", ""))

    return stats


def parse_languages_from_markdown(md_content: str) -> list[tuple[str, int]]:
    """Parses language statistics from markdown content.

    Looks for **Languages:** line in format:
    **Languages:** Swift (+42,836), Python (+29,624), C (+20,963)

    Args:
        md_content: Raw markdown content.

    Returns:
        List of (language_name, lines_added) tuples.
    """
    languages = []
    lang_match = re.search(r"\*\*Languages:\*\*\s*(.+?)(?:\n|$)", md_content)
    if lang_match:
        lang_str = lang_match.group(1)
        # Match each "Language (+N)" or "Language (+N,NNN)"
        for m in re.finditer(r"(\w+(?:/\w+)?(?:\s+\w+)?)\s*\(\+?([\d,]+)\)", lang_str):
            name = m.group(1).strip()
            value = int(m.group(2).replace(",", ""))
            languages.append((name, value))
    return languages


def parse_projects_from_markdown(md_content: str) -> list[tuple[str, int]]:
    """Parses project statistics from markdown content.

    Looks for **Projects:** line in format:
    **Projects:** acme-firmware (483), acme-ios-app (116)

    Args:
        md_content: Raw markdown content.

    Returns:
        List of (project_name, commits) tuples.
    """
    projects = []
    proj_match = re.search(r"\*\*Projects:\*\*\s*(.+?)(?:\n|$)", md_content)
    if proj_match:
        proj_str = proj_match.group(1)
        # Match each "Project-Name (N)"
        for m in re.finditer(r"([\w\-\.]+)\s*\(([\d,]+)\)", proj_str):
            name = m.group(1).strip()
            value = int(m.group(2).replace(",", ""))
            projects.append((name, value))
    return projects


def load_client_data(client_dir: Path) -> Optional[ClientData]:
    """Loads all data for a single client."""
    slug = client_dir.name

    summary_files = sorted(client_dir.glob("summary-*.md"), reverse=True)
    if not summary_files:
        return None

    summary_file = summary_files[0]
    summary_md = summary_file.read_text(encoding="utf-8")

    client_name, author, generated = extract_metadata(summary_md)
    if not client_name:
        client_name = format_client_name(slug)

    # Strip stats from summary since charts display this data
    content_md = strip_header_metadata(summary_md, strip_stats=True)
    summary_html = markdown_to_html(content_md)

    periods: list[Period] = []
    periods_dir = client_dir / "periods"
    if periods_dir.exists():
        for period_file in sorted(periods_dir.glob("*.md")):
            match = re.match(r"(\d{4})-(\d{2})\.md$", period_file.name)
            if not match:
                continue

            year = int(match.group(1))
            month = int(match.group(2))

            period_md = period_file.read_text(encoding="utf-8")
            period_stats = parse_stats_from_markdown(period_md)
            period_content = strip_header_metadata(period_md)
            period_html = markdown_to_html(period_content)

            periods.append(
                Period(
                    filename=period_file.name,
                    year=year,
                    month=month,
                    content_html=period_html,
                    stats=period_stats,
                )
            )

    periods.sort(key=lambda p: (p.year, p.month))

    # Parse languages and projects from summary
    languages = parse_languages_from_markdown(summary_md)
    projects = parse_projects_from_markdown(summary_md)

    return ClientData(
        name=client_name,
        slug=slug,
        summary_html=summary_html,
        author=author,
        generated=generated,
        periods=periods,
        languages=languages,
        projects=projects,
    )


def load_internal_summary(input_dir: Path) -> tuple[str, str]:
    """Loads the internal company summary if it exists.

    Args:
        input_dir: Directory containing the markdown files.

    Returns:
        Tuple of (year_label, html_content) or ("", "") if not found.
    """
    summary_files = sorted(input_dir.glob("internal-summary-*.md"), reverse=True)
    if not summary_files:
        return "", ""

    summary_file = summary_files[0]
    content = summary_file.read_text(encoding="utf-8")

    # Extract year from filename (internal-summary-2025.md -> 2025)
    year_match = re.search(r"internal-summary-(\d{4})\.md", summary_file.name)
    year_label = year_match.group(1) if year_match else ""

    # Strip header metadata (Generated, Clients, Cost lines)
    lines = content.split("\n")
    filtered_lines = []
    skip_until_section = True

    for line in lines:
        # Skip the title and metadata at the top
        if line.startswith("# Internal Company Summary"):
            continue
        if line.startswith("**Generated:**") or line.startswith("**Clients:**"):
            continue
        if line.startswith("**Cost:**"):
            continue

        # Start including from Combined Overview
        if line.startswith("## Combined Overview") or line.startswith("## Company"):
            skip_until_section = False

        # Skip Suggested Blog Posts section (internal only)
        if line.startswith("## Suggested Blog Posts"):
            break

        # Skip horizontal rules (h2 already has border-top styling)
        if line.strip() == "---":
            continue

        if not skip_until_section:
            filtered_lines.append(line)

    filtered_content = "\n".join(filtered_lines)
    html_content = markdown_to_html(filtered_content)

    return year_label, html_content


def load_public_summary(input_dir: Path) -> tuple[str, str]:
    """Loads the public-facing summary if it exists.

    Args:
        input_dir: Directory containing the markdown files.

    Returns:
        Tuple of (year_label, html_content) or ("", "") if not found.
    """
    summary_files = sorted(input_dir.glob("public-summary-*.md"), reverse=True)
    if not summary_files:
        return "", ""

    summary_file = summary_files[0]
    content = summary_file.read_text(encoding="utf-8")

    # Extract year from filename (public-summary-2025.md -> 2025)
    year_match = re.search(r"public-summary-(\d{4})\.md", summary_file.name)
    year_label = year_match.group(1) if year_match else ""

    # Skip dry run placeholder
    if content.strip().startswith("*(Dry run"):
        return "", ""

    # Convert to HTML
    html_content = markdown_to_html(content)

    return year_label, html_content


def generate_index_page(
    clients: list[ClientData],
    config: ReportConfig,
    internal_summary: tuple[str, str] = ("", ""),
    public_summary: tuple[str, str] = ("", ""),
) -> str:
    """Generates the main index page with client cards.

    Args:
        clients: List of client data to display.
        config: Report configuration.
        internal_summary: Tuple of (year_label, html_content) for internal summary.
        public_summary: Tuple of (year_label, html_content) for public-facing summary.
    """
    # Sort clients with "Other" always at the end
    sorted_clients = sorted(clients, key=lambda c: (c.slug.lower() == "other", c.name.lower()))

    nav_items = [("Overview", "index.html", True)]
    for client in sorted_clients:
        nav_items.append((client.name, f"{client.slug}/index.html", False))

    cards_html = ""
    for client in sorted_clients:
        branding = config.get_client_branding(client.slug)
        period_count = len(client.periods)
        latest = ""
        if client.periods:
            p = client.periods[-1]  # Last period (most recent) since sorted chronologically
            latest = f"{MONTH_NAMES[p.month - 1]} {p.year}"

        # Use logo if available, otherwise fall back to emoji icon
        if branding.logo:
            icon_html = f'<div class="card-logo">{branding.logo}</div>'
        else:
            icon_html = f'<div class="card-icon">{branding.icon}</div>'

        cards_html += f"""
        <a href="{client.slug}/index.html" class="card">
            {icon_html}
            <h3>{client.name}</h3>
            <div class="card-meta">
                <span>📅 {period_count} periods</span>
                {f"<span>🕐 Latest: {latest}</span>" if latest else ""}
            </div>
        </a>
"""

    year_label, internal_html = internal_summary
    public_year, public_html = public_summary

    # Determine date range from actual periods
    year_hint = year_label or public_year
    date_range, title_suffix = get_date_range_label(clients, year_hint)

    # Summary links section - show cards linking to separate pages
    summary_cards = ""
    if public_html or internal_html:
        cards = []
        if public_html:
            cards.append(f"""
            <a href="public.html" class="summary-card public">
                <div class="summary-card-icon">🌐</div>
                <div class="summary-card-content">
                    <h3>Public Report</h3>
                    <p>{title_suffix} suitable for blog posts, social media, and external sharing.</p>
                </div>
                <span class="summary-card-arrow">→</span>
            </a>""")
        if internal_html:
            cards.append("""
            <a href="internal.html" class="summary-card internal">
                <div class="summary-card-icon">🔒</div>
                <div class="summary-card-content">
                    <h3>Internal Report</h3>
                    <p>Detailed company overview with client breakdowns and metrics.</p>
                </div>
                <span class="summary-card-arrow">→</span>
            </a>""")

        summary_cards = f"""
        <div class="summary-cards">
            {"".join(cards)}
        </div>
"""

    title = f"{date_range} {title_suffix}" if date_range else "Activity Reports"

    content = f"""
        <div class="hero">
            <h1>{date_range + " " if date_range else ""}Activity <span class="gradient">Reports</span></h1>
            <p class="hero-subtitle">
                Comprehensive summaries of development work, key achievements,
                and technical progress across all projects.
            </p>
        </div>
{summary_cards}
        <h2 class="clients-heading">Client Reports</h2>
        <div class="cards-grid">
{cards_html}
        </div>
"""

    return generate_html_page(
        title=title,
        content=content,
        nav_items=nav_items,
        config=config,
        client_name=None,
    )


def generate_public_summary_page(
    public_summary: tuple[str, str],
    clients: list[ClientData],
    config: ReportConfig,
) -> str:
    """Generates the public-facing summary page.

    Args:
        public_summary: Tuple of (year_label, html_content).
        clients: List of client data for navigation.
        config: Report configuration.

    Returns:
        Complete HTML page string.
    """
    year_hint, html_content = public_summary
    if not html_content:
        return ""

    # Determine date range from actual periods
    date_range, title_suffix = get_date_range_label(clients, year_hint)

    # Sort clients with "Other" always at the end
    sorted_clients = sorted(clients, key=lambda c: (c.slug.lower() == "other", c.name.lower()))

    nav_items = [("Overview", "index.html", False)]
    for client in sorted_clients:
        nav_items.append((client.name, f"{client.slug}/index.html", False))

    content = f"""
        <div class="summary-page-header">
            <span class="badge public">Public Report</span>
            <h1>{date_range} {title_suffix}</h1>
            <p style="color: var(--text-secondary); margin-top: 0.5rem;">
                Suitable for blog posts, social media, and external sharing
            </p>
        </div>
        <div class="public-summary">
            <div class="content">
                {html_content}
            </div>
        </div>
        <div style="text-align: center; margin-top: 2rem;">
            <a href="index.html" class="summary-back-link">← Back to Overview</a>
        </div>
"""

    return generate_html_page(
        title=f"Public Report - {date_range}",
        content=content,
        nav_items=nav_items,
        config=config,
        client_name=None,
    )


def generate_internal_summary_page(
    internal_summary: tuple[str, str],
    clients: list[ClientData],
    config: ReportConfig,
) -> str:
    """Generates the internal company summary page.

    Args:
        internal_summary: Tuple of (year_label, html_content).
        clients: List of client data for navigation.
        config: Report configuration.

    Returns:
        Complete HTML page string.
    """
    year_hint, html_content = internal_summary
    if not html_content:
        return ""

    # Determine date range from actual periods
    date_range, title_suffix = get_date_range_label(clients, year_hint)

    # Sort clients with "Other" always at the end
    sorted_clients = sorted(clients, key=lambda c: (c.slug.lower() == "other", c.name.lower()))

    nav_items = [("Overview", "index.html", False)]
    for client in sorted_clients:
        nav_items.append((client.name, f"{client.slug}/index.html", False))

    content = f"""
        <div class="summary-page-header">
            <span class="badge internal">Internal Report</span>
            <h1>{date_range} Company Overview</h1>
            <p style="color: var(--text-secondary); margin-top: 0.5rem;">
                Detailed breakdown with client metrics (not for external sharing)
            </p>
        </div>
        <div class="internal-summary">
            <div class="content">
                {html_content}
            </div>
        </div>
        <div style="text-align: center; margin-top: 2rem;">
            <a href="index.html" class="summary-back-link">← Back to Overview</a>
        </div>
"""

    return generate_html_page(
        title=f"Internal Report - {date_range}",
        content=content,
        nav_items=nav_items,
        config=config,
        client_name=None,
    )


def generate_charts_html(client: ClientData, accent_primary: str, accent_secondary: str) -> str:
    """Generates the charts section HTML for a client summary page.

    Args:
        client: Client data with periods, languages, and projects.
        accent_primary: Primary accent color for charts.
        accent_secondary: Secondary accent color for charts.

    Returns:
        HTML string containing charts section.
    """
    if not client.periods:
        return ""

    # Prepare monthly data
    months_data = []
    for period in client.periods:
        months_data.append(
            {
                "label": f"{period.label[:3]} {period.year}",
                "commits": period.stats.commits,
                "added": period.stats.lines_added,
                "removed": period.stats.lines_removed,
            }
        )

    # Check if we have meaningful data
    has_commits = any(m["commits"] > 0 for m in months_data)
    has_lines = any(m["added"] > 0 or m["removed"] > 0 for m in months_data)
    has_languages = len(client.languages) > 0
    has_projects = len(client.projects) > 0

    if not (has_commits or has_lines or has_languages or has_projects):
        return ""

    import json

    months_json = json.dumps(months_data)

    # Prepare languages data (top 6)
    top_languages = client.languages[:6]
    lang_labels = json.dumps([lang[0] for lang in top_languages])
    lang_values = json.dumps([lang[1] for lang in top_languages])

    # Prepare projects data (top 6)
    top_projects = client.projects[:6]
    proj_labels = json.dumps([p[0] for p in top_projects])
    proj_values = json.dumps([p[1] for p in top_projects])

    # Generate chart colors
    chart_colors = [
        accent_primary,
        accent_secondary,
        "#10b981",  # green
        "#f59e0b",  # amber
        "#8b5cf6",  # purple
        "#ec4899",  # pink
        "#06b6d4",  # cyan
        "#f97316",  # orange
    ]
    colors_json = json.dumps(chart_colors)

    return f"""
        <div class="charts-section">
            <h2>Activity Overview</h2>
            <div class="charts-grid">
                <div class="chart-container">
                    <h3>Commits per Month</h3>
                    <canvas id="commitsChart"></canvas>
                </div>
                <div class="chart-container">
                    <h3>Lines Changed per Month</h3>
                    <canvas id="linesChart"></canvas>
                </div>
                <div class="chart-container">
                    <h3>Languages</h3>
                    <canvas id="languagesChart"></canvas>
                </div>
                <div class="chart-container">
                    <h3>Projects</h3>
                    <canvas id="projectsChart"></canvas>
                </div>
            </div>
        </div>

        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
        <script>
        (function() {{
            const monthsData = {months_json};
            const langLabels = {lang_labels};
            const langValues = {lang_values};
            const projLabels = {proj_labels};
            const projValues = {proj_values};
            const chartColors = {colors_json};

            const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            const gridColor = isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)';
            const textColor = isDark ? '#94a3b8' : '#64748b';

            Chart.defaults.color = textColor;
            Chart.defaults.borderColor = gridColor;

            // Commits line chart
            if (monthsData.some(m => m.commits > 0)) {{
                new Chart(document.getElementById('commitsChart'), {{
                    type: 'line',
                    data: {{
                        labels: monthsData.map(m => m.label),
                        datasets: [{{
                            label: 'Commits',
                            data: monthsData.map(m => m.commits),
                            borderColor: chartColors[0],
                            backgroundColor: chartColors[0] + '20',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 4,
                            pointHoverRadius: 6
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: true,
                        aspectRatio: 1.8,
                        plugins: {{ legend: {{ display: false }} }},
                        scales: {{
                            y: {{ beginAtZero: true, grid: {{ color: gridColor }} }},
                            x: {{ grid: {{ display: false }} }}
                        }}
                    }}
                }});
            }}

            // Lines changed bar chart
            if (monthsData.some(m => m.added > 0 || m.removed > 0)) {{
                new Chart(document.getElementById('linesChart'), {{
                    type: 'bar',
                    data: {{
                        labels: monthsData.map(m => m.label),
                        datasets: [
                            {{
                                label: 'Added',
                                data: monthsData.map(m => m.added),
                                backgroundColor: '#10b981',
                                borderRadius: 4
                            }},
                            {{
                                label: 'Removed',
                                data: monthsData.map(m => -m.removed),
                                backgroundColor: '#ef4444',
                                borderRadius: 4
                            }}
                        ]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: true,
                        aspectRatio: 1.8,
                        plugins: {{ legend: {{ position: 'bottom' }} }},
                        scales: {{
                            y: {{ grid: {{ color: gridColor }} }},
                            x: {{ stacked: true, grid: {{ display: false }} }}
                        }}
                    }}
                }});
            }}

            // Languages doughnut chart
            if (langLabels.length > 0) {{
                new Chart(document.getElementById('languagesChart'), {{
                    type: 'doughnut',
                    data: {{
                        labels: langLabels,
                        datasets: [{{
                            data: langValues,
                            backgroundColor: chartColors,
                            borderWidth: 0
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: true,
                        aspectRatio: 1.8,
                        plugins: {{
                            legend: {{ position: 'right', labels: {{ boxWidth: 12, padding: 10 }} }}
                        }}
                    }}
                }});
            }}

            // Projects horizontal bar chart
            if (projLabels.length > 0) {{
                new Chart(document.getElementById('projectsChart'), {{
                    type: 'bar',
                    data: {{
                        labels: projLabels,
                        datasets: [{{
                            label: 'Commits',
                            data: projValues,
                            backgroundColor: chartColors[1],
                            borderRadius: 4
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: true,
                        aspectRatio: 1.8,
                        indexAxis: 'y',
                        plugins: {{ legend: {{ display: false }} }},
                        scales: {{
                            x: {{
                                beginAtZero: true,
                                grid: {{ color: gridColor }},
                                title: {{ display: true, text: 'Commits', color: textColor }}
                            }},
                            y: {{ grid: {{ display: false }} }}
                        }}
                    }}
                }});
            }}
        }})();
        </script>
"""


def generate_client_page(client: ClientData, config: ReportConfig) -> str:
    """Generates the client summary page with centered collab header."""
    branding = config.get_client_branding(client.slug)

    # Determine the year(s) covered
    years = sorted({p.year for p in client.periods}) if client.periods else []
    if len(years) == 1:
        year_subtitle = f"{years[0]} Year in Review"
    elif len(years) > 1:
        year_subtitle = f"{years[0]}–{years[-1]} in Review"
    else:
        year_subtitle = "Activity Summary"

    periods_html = ""
    if client.periods:
        period_links = ""
        for period in client.periods:
            period_links += f"""
            <a href="{period.slug}.html" class="period-link">
                <span class="month">{period.label}</span>
                <span class="year">{period.year}</span>
            </a>
"""

        periods_html = f"""
        <div class="periods-section">
            <h2>Monthly Reports</h2>
            <div class="periods-grid">
{period_links}
            </div>
        </div>
"""

    # Generate charts section
    charts_html = generate_charts_html(client, branding.accent_primary, branding.accent_secondary)

    content = f"""
        <div class="year-subtitle">{year_subtitle}</div>

        {charts_html}

        <div class="content">
            {client.summary_html}
        </div>

        {periods_html}
"""

    return generate_html_page(
        title=f"{client.name} - {year_subtitle}",
        content=content,
        nav_items=[],
        config=config,
        client_name=client.name,
        branding=branding,
        use_standalone_header=True,
    )


def generate_period_page(
    client: ClientData,
    period: Period,
    config: ReportConfig,
) -> str:
    """Generates a period detail page with collab header."""
    branding = config.get_client_branding(client.slug)

    # Generate monthly reports navigation (same style as summary page)
    period_links = ""
    for p in client.periods:
        is_current = p.slug == period.slug
        current_class = " current" if is_current else ""
        if is_current:
            period_links += f"""
            <span class="period-link{current_class}">
                <span class="month">{p.label}</span>
                <span class="year">{p.year}</span>
            </span>
"""
        else:
            period_links += f"""
            <a href="{p.slug}.html" class="period-link">
                <span class="month">{p.label}</span>
                <span class="year">{p.year}</span>
            </a>
"""

    content = f"""
        <div class="period-nav">
            <a href="index.html" class="back-link">← Summary</a>
        </div>

        <div class="page-header">
            <h1>{period.label} {period.year}</h1>
        </div>

        <div class="content">
            {period.content_html}
        </div>

        <div class="periods-section">
            <h2>Monthly Reports</h2>
            <div class="periods-grid">
{period_links}
            </div>
        </div>
"""

    return generate_html_page(
        title=f"{period.label} {period.year} - {client.name}",
        content=content,
        nav_items=[],
        config=config,
        client_name=client.name,
        branding=branding,
        use_standalone_header=True,
    )


def generate_html_reports(
    input_dir: Path,
    output_dir: Path,
    config: ReportConfig,
    client_filter: Optional[str] = None,
    verbose: bool = False,
) -> int:
    """Generates HTML reports from markdown files."""
    clients: list[ClientData] = []

    for item in sorted(input_dir.iterdir()):
        if not item.is_dir():
            continue
        if item.name.startswith("."):
            continue
        if item.name == "html":
            continue

        # Match client filter against directory name or formatted display name
        if client_filter:
            filter_lower = client_filter.lower().replace(" ", "_").replace("-", "_")
            dir_lower = item.name.lower().replace("-", "_")
            if filter_lower != dir_lower:
                continue

        if verbose:
            print(f"Loading client: {item.name}", file=sys.stderr)

        client_data = load_client_data(item)
        if client_data:
            clients.append(client_data)
            if verbose:
                print(f"  Found {len(client_data.periods)} periods", file=sys.stderr)

    if not clients:
        print("No client data found.", file=sys.stderr)
        return 0

    pages_generated = 0

    if not client_filter:
        output_dir.mkdir(parents=True, exist_ok=True)
        internal_summary = load_internal_summary(input_dir)
        public_summary = load_public_summary(input_dir)
        index_html = generate_index_page(clients, config, internal_summary, public_summary)
        (output_dir / "index.html").write_text(index_html, encoding="utf-8")
        pages_generated += 1
        if verbose:
            if internal_summary[1]:
                print("Loaded internal summary", file=sys.stderr)
            if public_summary[1]:
                print("Loaded public summary", file=sys.stderr)
            print("Generated: index.html", file=sys.stderr)

        # Generate public summary page if available
        if public_summary[1]:
            public_html = generate_public_summary_page(public_summary, clients, config)
            (output_dir / "public.html").write_text(public_html, encoding="utf-8")
            pages_generated += 1
            if verbose:
                print("Generated: public.html", file=sys.stderr)

        # Generate internal summary page if available
        if internal_summary[1]:
            internal_html = generate_internal_summary_page(internal_summary, clients, config)
            (output_dir / "internal.html").write_text(internal_html, encoding="utf-8")
            pages_generated += 1
            if verbose:
                print("Generated: internal.html", file=sys.stderr)

    for client in clients:
        if client_filter:
            client_dir = output_dir
        else:
            client_dir = output_dir / client.slug

        client_dir.mkdir(parents=True, exist_ok=True)

        client_html = generate_client_page(client, config)
        (client_dir / "index.html").write_text(client_html, encoding="utf-8")
        pages_generated += 1
        if verbose:
            if client_filter:
                print("Generated: index.html", file=sys.stderr)
            else:
                print(f"Generated: {client.slug}/index.html", file=sys.stderr)

        for period in client.periods:
            period_html = generate_period_page(client, period, config)
            (client_dir / f"{period.slug}.html").write_text(period_html, encoding="utf-8")
            pages_generated += 1
            if verbose:
                if client_filter:
                    print(f"Generated: {period.slug}.html", file=sys.stderr)
                else:
                    print(f"Generated: {client.slug}/{period.slug}.html", file=sys.stderr)

    return pages_generated


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the HTML report generator."""
    parser = argparse.ArgumentParser(
        description="Generate HTML reports from markdown activity summaries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Generate all clients to output/html/
  %(prog)s --client acme              # Generate only specific client
  %(prog)s --open                    # Generate and open in browser

Configuration:
  Edit config/config.yaml (html_report section) to customize company
  branding and client-specific colors/logos.
        """,
    )
    add_input_dir_arg(
        parser, help_text="Input directory containing client markdown folders (default: output/)"
    )
    add_output_dir_arg(parser, help_text="Output directory for HTML files (default: output/html/)")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file (default: config/config.yaml)",
    )
    parser.add_argument(
        "--client",
        type=str,
        default=None,
        metavar="SLUG",
        help="Generate only a specific client (by folder name, e.g., 'acme').",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print progress information.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated report in the default browser.",
    )

    args = parser.parse_args(argv)

    # Use get_output_dir to get input dir (markdown summaries), and derive HTML output from it
    base_output = get_output_dir(output_dir=None)
    config_path_resolved = get_config_path(str(args.config) if args.config else None)

    # Determine input and output directories
    # --output-dir alone: use as both input base and output base (convenience shorthand)
    # --input-dir alone: read from input, write to default output location
    # Both: use as specified
    if args.output_dir and not args.input_dir:
        # Shorthand: --output-dir alone means use it as input and put HTML in html/ subdir
        input_dir = args.output_dir
        if args.client:
            output_dir = args.output_dir / "html" / args.client
        else:
            output_dir = args.output_dir / "html"
    else:
        # Normal case: input defaults to base_output, output defaults to base_output/html
        input_dir = args.input_dir or base_output
        if args.output_dir:
            output_dir = args.output_dir
        elif args.client:
            output_dir = base_output / "html" / args.client
        else:
            output_dir = base_output / "html"

    if not input_dir.exists():
        print(f"Error: Input directory does not exist: {input_dir}", file=sys.stderr)
        return 1

    # Load configuration
    config = load_config(config_path_resolved)
    if config_path_resolved.exists():
        print(f"Loaded config: {config_path_resolved}", file=sys.stderr)

    print(f"Input directory:  {input_dir}", file=sys.stderr)
    print(f"Output directory: {output_dir}", file=sys.stderr)
    if args.client:
        print(f"Client filter:    {args.client}", file=sys.stderr)

    pages = generate_html_reports(
        input_dir,
        output_dir,
        config,
        client_filter=args.client,
        verbose=args.verbose,
    )

    print(f"\nGenerated {pages} HTML pages.", file=sys.stderr)

    if args.open:
        import webbrowser

        index_path = output_dir / "index.html"
        webbrowser.open(f"file://{index_path.resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
