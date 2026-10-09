# Scraper Health Monitor

A small command-line tool that checks a list of URLs and tells you whether the endpoints your scrapers depend on look healthy: status codes, response times, redirects, errors, and a simple, documented health score.

## Why this project exists

Before debugging a scraper, it helps to know whether the target is even answering. Is the site slow today? Is one section returning 403 or 429? Did a DNS or TLS problem appear? This tool answers those questions quickly, in one report, without any setup.

It is a diagnostic utility, not a monitoring platform. It deliberately stays small.

## Features

- Reads URLs from a text file (blank lines and `#` comments are ignored)
- Sends one `GET` request per URL using `httpx`, with limited concurrency
- Classifies each result: `SUCCESS`, `REDIRECT`, `CLIENT_ERROR`, `BLOCKED`, `RATE_LIMITED`, `SERVER_ERROR`, `TIMEOUT`, `CONNECTION_ERROR`, `SSL_ERROR`, `UNKNOWN`
- Reports average, fastest and slowest response time (only from requests that returned a response)
- Computes a deterministic 0-100 health score with documented rules
- Clean terminal report (Rich) or stable JSON output
- Optional run history in a local SQLite file, with a `history` command
- Reads at most 64 KiB of each response body

## Installation

Requires Python 3.11 or newer.

```bash
git clone https://github.com/<your-account>/scraper-health-monitor.git
cd scraper-health-monitor
pip install -e .
```

Directly from GitHub:

```bash
pip install "git+https://github.com/<your-account>/scraper-health-monitor.git"
```

Check the install:

```bash
scraper-health --help
```

## Quick start

```bash
scraper-health examples/urls.txt
```

## CLI commands

`scraper-health` has two commands:

| Command | Purpose |
| --- | --- |
| `scraper-health check URLS_FILE` | Check the URLs in a file and print a report |
| `scraper-health history` | Show recent checks stored with `--save-history` |

For convenience, `scraper-health urls.txt` is shorthand for `scraper-health check urls.txt`. If your file is literally named `check` or `history`, use `scraper-health check ./history`.

### `check` options

| Option | Default | Description |
| --- | --- | --- |
| `--timeout`, `-t` | `10` | Per-request timeout in seconds. httpx applies it to each phase (connect, read, write, pool), so it is not a hard cap on total time |
| `--concurrency`, `-c` | `5` | Maximum simultaneous requests (1-50) |
| `--delay` | `0` | Seconds each worker pauses between its requests |
| `--user-agent` | `ScraperHealthMonitor/<version>` | User-Agent header to send |
| `--json` | off | Print the report as JSON instead of the terminal report |
| `--output`, `-o` | none | Also write the JSON report to this file (parent directories are created) |
| `--save-history` | off | Store this run in the SQLite history database |
| `--db` | `.scraper-health/history.db` | History database path (or set `SCRAPER_HEALTH_DB`) |
| `--full-urls` | off | Show complete URLs instead of shortening long ones |

### `history` options

| Option | Default | Description |
| --- | --- | --- |
| `--limit`, `-n` | `20` | Number of recent checks to show |
| `--db` | `.scraper-health/history.db` | History database path (or set `SCRAPER_HEALTH_DB`) |
| `--full-urls` | off | Show complete URLs |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The check completed (even if some URLs were unhealthy) |
| `1` | A runtime error, such as an unwritable report or history file |
| `2` | Usage or input error, such as a missing file or no valid URLs |
| `130` | Interrupted with Ctrl-C |

The tool reports health; it does not fail the run because URLs are unhealthy.

## Example input

```text
# examples/urls.txt
https://example.com
https://example.org/products
https://example.net
```

Lines that are not absolute `http://` or `https://` URLs are skipped with a warning that includes the line number.

## Example output

Illustrative output (the numbers will differ on your machine):

```text
╭──────────────────────────────────────────────────────────────╮
│                    Scraper Health Monitor                    │
╰──────────────────────────────────────────────────────────────╯
URLs Checked         6
Successful           3
Failed               3

Success Rate     50.0%
Average Response 0.412s
Fastest          0.184s
Slowest          0.903s

Health Score     55/100

Status Codes
  200  3
  403  1
  429  1
  Timeout  1

  URL                              Status      Time  Result
 ──────────────────────────────────────────────────────────────
  example.com                         200    0.184s  SUCCESS
  example.com/products                200    0.301s  SUCCESS
  example.org                         200    0.267s  SUCCESS
  example.com/admin                   403    0.312s  BLOCKED
  example.com/api                     429    0.903s  RATE LIMITED
  example.net/slow                      —       n/a  TIMEOUT

Details
  example.com/admin: 403 Forbidden / possible access restriction
  example.com/api: 429 Too Many Requests / possible rate limiting
  example.net/slow: Request timed out (ReadTimeout: ...)
```

## JSON output

```bash
scraper-health urls.txt --json
scraper-health urls.txt --output report.json
```

`--json` prints only JSON on stdout (progress and warnings go to stderr). `--output` writes the same JSON to a file; combine both to do both.

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-08T12:20:00+00:00",
  "summary": {
    "total": 3,
    "successful": 1,
    "failed": 2,
    "success_rate": 33.3,
    "average_response_time": 0.26,
    "fastest_response_time": 0.21,
    "slowest_response_time": 0.31,
    "health_score": 47,
    "status_counts": { "200": 1, "403": 1, "Timeout": 1 },
    "category_counts": { "SUCCESS": 1, "BLOCKED": 1, "TIMEOUT": 1 }
  },
  "results": [
    {
      "url": "https://example.com",
      "status_code": 200,
      "response_time": 0.21,
      "result": "SUCCESS",
      "description": "200 OK",
      "error": null,
      "redirect_location": null,
      "content_type": "text/html",
      "bytes_read": 1256
    },
    {
      "url": "https://example.com/admin",
      "status_code": 403,
      "response_time": 0.31,
      "result": "BLOCKED",
      "description": "403 Forbidden / possible access restriction",
      "error": null,
      "redirect_location": null,
      "content_type": "text/html",
      "bytes_read": 312
    },
    {
      "url": "https://example.com/slow",
      "status_code": null,
      "response_time": null,
      "result": "TIMEOUT",
      "description": "Request timed out",
      "error": "ReadTimeout: timed out",
      "redirect_location": null,
      "content_type": null,
      "bytes_read": null
    }
  ]
}
```

Schema notes:

- `schema_version` changes only when the layout changes incompatibly.
- Times are in seconds, rounded to milliseconds. `response_time` is `null` when no response was received.
- `result` is one of the category names listed above.
- `bytes_read` is the number of raw (undecoded) body bytes read, capped at roughly 64 KiB.
- `status_counts` lists numeric status codes first, then labels such as `Timeout`, `Connection error`, `SSL error`.

## Result classification

| Condition | Category |
| --- | --- |
| 2xx | `SUCCESS` |
| 3xx (redirects are reported, not followed) | `REDIRECT` |
| 403 | `BLOCKED` ("403 Forbidden / possible access restriction") |
| 429 | `RATE_LIMITED` |
| Other 4xx (including 401, 404, 408, 409, 425) | `CLIENT_ERROR` |
| 5xx | `SERVER_ERROR` |
| Timeout | `TIMEOUT` |
| Connection failure, including DNS errors | `CONNECTION_ERROR` |
| TLS/certificate failure | `SSL_ERROR` |
| Anything else | `UNKNOWN` |

A 403 can mean many things: an access rule, a WAF, a missing cookie, a geographic restriction, or a misconfigured server. `BLOCKED` is a label for "this response may indicate an access restriction", not a diagnosis. The same caution applies to 429.

"Successful" in the summary means `SUCCESS` or `REDIRECT`.

## Health score calculation

The health score is the average of per-URL points, rounded half up to a whole number.

| Result | Points |
| --- | --- |
| `SUCCESS` | 100 |
| `REDIRECT` | 90 |
| `RATE_LIMITED` | 30 |
| `CLIENT_ERROR` | 20 |
| `BLOCKED` | 20 |
| `SERVER_ERROR` | 10 |
| `TIMEOUT`, `CONNECTION_ERROR`, `SSL_ERROR`, `UNKNOWN` | 0 |

A `SUCCESS` or `REDIRECT` that took longer than 5 seconds keeps 75% of its points.

Example: 7 successes, one 403, one 429 and one timeout give (7 x 100 + 20 + 30 + 0) / 10 = **75**.

The score is a quick summary of one run, not a scientific measure. The weights are judgement calls, and they are defined in `src/scraper_health/scoring.py` if you want to change them. An empty run scores 0.

## Historical results

```bash
scraper-health urls.txt --save-history
scraper-health history
scraper-health history --limit 50
```

With `--save-history`, each run appends one row per URL to a SQLite database (Python standard library, no server needed). The default location is `.scraper-health/history.db` in the current directory; change it with `--db PATH` or the `SCRAPER_HEALTH_DB` environment variable.

Each row stores: UTC timestamp, URL, status code, response time, result category, error text, and the health score of the run it belongs to. All rows from one run share the same timestamp.

```text
Recent Health Checks
 Time (UTC)        URL                  Status      Time  Result        Score
 ──────────────────────────────────────────────────────────────────────────
 2026-10-08 12:20  example.com             200    0.184s  SUCCESS          55
 2026-10-08 12:20  example.com/admin       403    0.312s  BLOCKED          55
 2026-10-08 12:20  example.com/api         429    0.903s  RATE LIMITED     55
```

`history` shows the most recent rows, newest run first. It does not create a database if none exists.

## Project structure

```text
scraper-health-monitor/
├── src/scraper_health/
│   ├── cli.py        # Typer commands: check, history
│   ├── checker.py    # URL loading, request logic, classification, concurrency
│   ├── models.py     # Result/summary/report dataclasses and exceptions
│   ├── scoring.py    # Health score and summary statistics
│   ├── history.py    # SQLite history store
│   └── reporter.py   # Rich output, JSON, report files
├── tests/            # pytest suite (fully offline)
├── examples/urls.txt
└── .github/workflows/tests.yml
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

The suite uses `httpx.MockTransport` and temporary directories; it never touches the network.

## GitHub Actions

`.github/workflows/tests.yml` runs on every push and pull request. It installs the package on Python 3.11, 3.12 and 3.13, smoke-tests `scraper-health --help`, and runs `pytest`.

## Responsible usage

This is a health and diagnostic tool. Use it on URLs you own or are allowed to request.

- It sends one plain `GET` per URL, with no retries.
- Defaults are conservative: 5 concurrent requests, a 10 second timeout, and at most 64 KiB of each body.
- It identifies itself with a descriptive `User-Agent`. If you override it, keep it truthful and contactable.
- It does **not** bypass CAPTCHAs or authentication, rotate proxies, spoof browser fingerprints, evade anti-bot systems, or ignore `robots.txt` rules on your behalf. It also does not fetch `robots.txt`; you remain responsible for respecting a site's terms and crawl rules.
- Do not point it at large lists of third-party sites. Use `--concurrency` and `--delay` to stay gentle.

## Limitations

- It checks a single `GET` per URL. It does not render JavaScript, solve challenges, or execute pages, so a page that looks healthy here can still fail in a real browser.
- Status codes are hints. A `200` can still be a challenge page or an empty shell; a `403` or `429` may or may not be anti-bot related.
- Redirects are reported but not followed, so the final destination's status is not checked.
- The timeout applies per phase, not as an overall deadline.
- Response time includes reading up to the first 64 KiB of the body, so it reflects the network and server together.
- The health score is a heuristic.
- Results from a single run are a snapshot; use history to look at trends.

## Roadmap

- Optional `--follow-redirects`
- `--fail-under SCORE` exit code for CI use
- CSV export
- Simple history filters (by URL or date)

## Contributing

Issues and pull requests are welcome. Please keep the project small and in scope: no proxy rotation, CAPTCHA handling, stealth or evasion features. Before opening a pull request:

1. Add or update tests for your change.
2. Run `pytest`.
3. Update `README.md` and `CHANGELOG.md` if behaviour changes.

## License

MIT. See [LICENSE](LICENSE).
