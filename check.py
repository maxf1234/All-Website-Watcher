#!/usr/bin/env python3
"""Check every site in docs/sites.json and email one digest of what changed.

Each page is reduced to its visible text and links and compared with the
snapshot saved on the previous run (snapshots/<id>.txt). Newly added sites,
changed pages, and sites that go down or come back up are collected into a
single email per run, so a busy run never floods the inbox.

Stdlib only. Configuration comes from environment variables:
  SMTP_HOST       default smtp.gmail.com
  SMTP_PORT       default 465 (SSL); 587 uses STARTTLS
  SMTP_USERNAME   login for the mail server (also the From address)
  SMTP_PASSWORD   password (for Gmail: an App Password)
  EMAIL_TO        recipient(s), comma separated; defaults to SMTP_USERNAME
  DRY_RUN=1       print the email instead of sending it
"""
import difflib
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).parent
SITES_FILE = ROOT / "docs" / "sites.json"
SNAPSHOT_DIR = ROOT / "snapshots"
STATUS_FILE = SNAPSHOT_DIR / "status.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_LINES_PER_SITE = 40
MAX_BYTES = 5_000_000
FAILURES_BEFORE_DOWN = 3  # consecutive failed runs before a site is reported down
WORKERS = 10


class PageText(HTMLParser):
    """Collects visible text blocks, link targets and image alt text."""

    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {
        "p", "div", "section", "article", "header", "footer", "nav", "main",
        "li", "ul", "ol", "tr", "td", "th", "table", "h1", "h2", "h3", "h4",
        "h5", "h6", "br", "hr", "form", "button", "label", "option", "aside",
        "figure", "figcaption", "blockquote", "dd", "dt",
    }

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skip_depth = 0
        self.title = ""
        self.in_title = False
        self.blocks = []
        self.current = []
        self.links = set()

    def flush(self):
        text = re.sub(r"\s+", " ", " ".join(self.current)).strip()
        if text:
            self.blocks.append(text)
        self.current = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if tag in self.BLOCK:
            self.flush()
        if tag == "a" and attrs.get("href"):
            href = urljoin(self.base_url, attrs["href"].strip())
            if urlparse(href).scheme in ("http", "https"):
                self.links.add(href.split("#")[0])
        if tag == "img" and attrs.get("alt") and not self.skip_depth:
            self.current.append(f"[image: {attrs['alt'].strip()}]")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in self.BLOCK:
            self.flush()

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip_depth:
            self.current.append(data)


def site_id(url):
    host = re.sub(r"[^A-Za-z0-9.-]+", "_", urlparse(url).netloc)[:60]
    return f"{host}-{hashlib.sha1(url.encode()).hexdigest()[:10]}"


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), resp.read(MAX_BYTES).decode(charset, errors="replace")


def snapshot(url, raw_html):
    """Turns the page into stable, diff-friendly lines."""
    parser = PageText(url)
    parser.feed(raw_html)
    parser.flush()
    title = re.sub(r"\s+", " ", parser.title).strip()
    lines = [f"TITLE: {title}", ""]
    seen = set()
    for block in parser.blocks:
        # Repeated blocks (menus shown twice for mobile/desktop) add only noise.
        if block not in seen:
            seen.add(block)
            lines.append(block)
    lines += ["", "LINKS:"]
    lines += [f"  {link}" for link in sorted(parser.links)]
    return "\n".join(lines) + "\n"


def diff(old, new):
    """Returns (summary, [(heading, sign, lines)]) describing the change."""
    old_lines, new_lines = old.splitlines(), new.splitlines()
    added, removed = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines, autojunk=False
    ).get_opcodes():
        if op in ("replace", "delete"):
            removed += [l for l in old_lines[i1:i2] if l.strip()]
        if op in ("replace", "insert"):
            added += [l for l in new_lines[j1:j2] if l.strip()]

    def is_link(l):
        return l.startswith("  http")

    sections = [
        ("Added text", "+", [l for l in added if not is_link(l)]),
        ("Removed text", "-", [l for l in removed if not is_link(l)]),
        ("New links", "+", [l.strip() for l in added if is_link(l)]),
        ("Removed links", "-", [l.strip() for l in removed if is_link(l)]),
    ]
    labels = ["line(s) of text added", "removed", "new link(s)", "link(s) gone"]
    summary = ", ".join(
        f"{len(items)} {label}" for (_, _, items), label in zip(sections, labels) if items
    ) or "minor formatting change"
    return summary, [s for s in sections if s[2]]


def check(url):
    """Fetches one site. Returns (url, snapshot text or None, error or None)."""
    try:
        final_url, raw = fetch(url)
        return url, snapshot(final_url, raw), None
    except Exception as exc:
        return url, None, f"{type(exc).__name__}: {exc}"[:300]


class Digest:
    """Collects everything worth emailing about this run."""

    def __init__(self):
        self.text, self.html, self.subjects = [], [], []

    def add(self, subject, heading, url, paragraphs=(), sections=()):
        self.subjects.append(subject)
        self.text += [f"== {heading}: {url}", *paragraphs]
        self.html.append(
            f"<h2 style='margin-bottom:4px'>{html.escape(heading)}</h2>"
            f"<p style='margin-top:0'><a href='{html.escape(url)}'>{html.escape(url)}</a></p>"
        )
        self.html += [f"<p>{html.escape(p)}</p>" for p in paragraphs]
        for title, sign, items in sections:
            extra = len(items) - MAX_LINES_PER_SITE
            items = items[:MAX_LINES_PER_SITE]
            colour = "#1a7f37" if sign == "+" else "#cf222e"
            self.text.append(f"{title}:")
            self.text += [f"  {sign} {l.strip()}" for l in items]
            self.html.append(f"<h3>{title}</h3><ul>")
            self.html += [
                f"<li style='color:{colour}'>{html.escape(l.strip())}</li>" for l in items
            ]
            if extra > 0:
                self.text.append(f"  ... and {extra} more")
                self.html.append(f"<li><i>... and {extra} more</i></li>")
            self.html.append("</ul>")
        self.text.append("")
        self.html.append("<hr>")

    def subject(self):
        if len(self.subjects) == 1:
            return self.subjects[0]
        return f"{len(self.subjects)} website updates: " + "; ".join(self.subjects)[:150]


def send_email(subject, text, html_body=None):
    user = os.environ.get("SMTP_USERNAME", "")
    to = [e.strip() for e in (os.environ.get("EMAIL_TO") or user).split(",") if e.strip()]
    if os.environ.get("DRY_RUN") == "1" or not user:
        print(f"--- email (not sent{'' if user else ': SMTP_USERNAME unset'}) ---")
        print(f"To: {', '.join(to)}\nSubject: {subject}\n\n{text}")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.set_content(text)
    if html_body:
        msg.add_alternative(f"<html><body>{html_body}</body></html>", subtype="html")
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or "465")
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
        server.starttls(context=ctx)
    with server:
        server.login(user, os.environ["SMTP_PASSWORD"])
        server.send_message(msg)
    print(f"Emailed {', '.join(to)}: {subject}")


def main():
    sites = json.loads(SITES_FILE.read_text())
    urls = [s["url"] for s in sites]
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    status = json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else {}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    digest = Digest()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(check, urls))

    for url, new, error in results:
        sid = site_id(url)
        path = SNAPSHOT_DIR / f"{sid}.txt"
        st = status.setdefault(sid, {"url": url, "failures": 0, "down": False})
        host = urlparse(url).netloc

        if error:
            st["failures"] += 1
            print(f"{url}: failed ({st['failures']}x) {error}")
            if st["failures"] >= FAILURES_BEFORE_DOWN and not st["down"]:
                st["down"] = True
                digest.add(f"{host} is unreachable", "Unreachable", url,
                           [f"Failed {st['failures']} checks in a row. Last error: {error}"])
            continue

        if st["down"]:
            digest.add(f"{host} is back up", "Back up", url,
                       ["The site is responding again."])
        st.update(failures=0, down=False)

        if not path.exists():
            path.write_text(new)
            print(f"{url}: baseline saved")
            digest.add(f"now watching {host}", "Now watching", url,
                       ["You'll get an email whenever this page changes."])
            continue

        old = path.read_text()
        if old == new:
            print(f"{url}: no change")
            continue

        summary, sections = diff(old, new)
        path.write_text(new)
        st["last_change"] = stamp
        print(f"{url}: changed ({summary})")
        digest.add(f"{host} changed", "Changed", url, [summary], sections)

    # Forget sites that were removed from the list.
    live = {site_id(u) for u in urls}
    for sid in list(status):
        if sid not in live:
            del status[sid]
            (SNAPSHOT_DIR / f"{sid}.txt").unlink(missing_ok=True)
    STATUS_FILE.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")

    if digest.subjects:
        send_email(
            digest.subject(),
            "\n".join(digest.text) + f"\nChecked at {stamp}.\n",
            "\n".join(digest.html) + f"<p style='color:#666'>Checked at {stamp}.</p>",
        )
    print(f"{stamp}: checked {len(urls)} site(s), {len(digest.subjects)} update(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail loudly so GitHub Actions flags the run
        print(f"Check failed: {exc}", file=sys.stderr)
        raise
