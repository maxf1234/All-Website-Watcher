#!/usr/bin/env python3
"""Add or remove a watched site from an issue submitted through the website.

Reads the issue from environment variables set by the workflow and edits
docs/sites.json. Prints a one-line result, which the workflow posts back on
the issue as a comment. Exits 0 when the list changed, 2 when it did not.

  ACTION          "add" or "remove"
  ISSUE_BODY      body of the issue (from the issue form)
  ISSUE_AUTHOR    GitHub login of whoever opened the issue
  MAX_SITES       cap on how many sites can be watched (default 100)
"""
import ipaddress
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

SITES_FILE = Path(__file__).parent / "docs" / "sites.json"


def extract_url(body):
    """Pulls the URL out of the issue form's "Website URL" field."""
    match = re.search(r"###\s*Website URL(?: to remove)?\s*\n+\s*(\S+)", body or "")
    return match.group(1).strip("<>") if match else None


def normalise(raw):
    """Returns a clean http(s) URL, or raises ValueError explaining why not."""
    raw = raw.strip()
    if not re.match(r"^[a-z]+://", raw, re.I):
        raw = "https://" + raw
    if len(raw) > 500:
        raise ValueError("that URL is too long")
    parts = urlparse(raw)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("only http:// and https:// website links are accepted")
    if parts.username or parts.password:
        raise ValueError("links containing a username or password are not accepted")
    host = parts.hostname.lower()
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise ValueError(f"the domain {host} does not exist")
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not ip.is_global:
            raise ValueError(f"{host} points to a private or reserved address")
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunparse((parts.scheme.lower(), netloc, parts.path or "/",
                       "", parts.query, ""))


def main():
    action = os.environ["ACTION"]
    author = os.environ.get("ISSUE_AUTHOR", "")
    max_sites = int(os.environ.get("MAX_SITES") or 100)
    raw = extract_url(os.environ.get("ISSUE_BODY", ""))
    if not raw:
        print("I couldn't find a website link in this issue.")
        return 2
    try:
        url = normalise(raw)
    except ValueError as exc:
        print(f"Sorry, {exc}.")
        return 2

    sites = json.loads(SITES_FILE.read_text())
    existing = [s for s in sites if s["url"].rstrip("/") == url.rstrip("/")]

    if action == "add":
        if existing:
            print(f"{url} is already being watched.")
            return 2
        if len(sites) >= max_sites:
            print(f"The watch list is full ({max_sites} sites).")
            return 2
        sites.append({
            "url": url,
            "added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "by": author,
        })
        message = f"Added {url}. It will be checked every few minutes."
    elif action == "remove":
        if not existing:
            print(f"{url} is not on the watch list.")
            return 2
        sites = [s for s in sites if s not in existing]
        message = f"Removed {url} from the watch list."
    else:
        raise SystemExit(f"unknown ACTION {action!r}")

    SITES_FILE.write_text(json.dumps(sites, indent=2) + "\n")
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
