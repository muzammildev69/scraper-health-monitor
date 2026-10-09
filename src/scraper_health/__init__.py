"""Scraper Health Monitor: a small CLI for checking the health of scraping targets."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("scraper-health-monitor")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
