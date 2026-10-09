"""Allow ``python -m scraper_health`` as an alternative to the console script."""

from scraper_health.cli import app

if __name__ == "__main__":
    app()
