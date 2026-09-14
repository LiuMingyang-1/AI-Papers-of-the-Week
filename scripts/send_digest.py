#!/usr/bin/env python3
"""Fetch the latest AI Papers of the Week issue and email it if it is new."""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import ssl
import smtplib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

UPSTREAM = os.environ.get("DIGEST_UPSTREAM", "dair-ai/AI-Papers-of-the-Week")
RAW_BASE = f"https://raw.githubusercontent.com/{UPSTREAM}/main"
STATE_PATH = Path(__file__).resolve().parent.parent / ".github" / "digest-state.json"
UA = "ai-papers-digest/1.0 (personal)"

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
PAPER_RE = re.compile(r"^(\d+)\)\s*\*\*(.+?)\*\*\s*[-—–]\s*(.*)$", re.S)
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
RANGE_RE = re.compile(
    r"Top AI Papers of the Week\s*\((.+?)\)(?:\s*[-—–]\s*(\d{4}))?", re.I
)


class DigestError(RuntimeError):
    pass


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise DigestError(f"GET {url} failed: HTTP {exc.code}") from exc


def fetch_year_markdown(year: int | None = None, path: str | None = None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    now = datetime.now(timezone.utc)
    years = [year] if year else [now.year, now.year - 1]
    last_error: Exception | None = None
    for y in years:
        url = f"{RAW_BASE}/years/{y}.md"
        try:
            return http_get(url)
        except DigestError as exc:
            last_error = exc
            continue
    raise DigestError(f"could not fetch year markdown: {last_error}")


def extract_latest_issue(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    start: int | None = None
    heading = ""
    for i, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        if start is None:
            heading = match.group(1).strip()
            start = i
            continue
        body = "\n".join(lines[start:i]).strip()
        return heading, body
    if start is None:
        raise DigestError("no weekly heading found")
    return heading, "\n".join(lines[start:]).strip()


def parse_links(cell: str) -> list[tuple[str, str]]:
    return [(label.strip(), url.strip()) for label, url in LINK_RE.findall(cell)]


def parse_paper_cell(cell: str) -> dict[str, Any]:
    match = PAPER_RE.match(cell.strip())
    if not match:
        return {"rank": None, "title": cell.strip(), "lead": "", "bullets": []}
    lead, *bullet_parts = re.split(r"<br>\s*●\s*", match.group(3))
    return {
        "rank": int(match.group(1)),
        "title": match.group(2).strip(),
        "lead": re.sub(r"<br\s*/?>", " ", lead).strip(),
        "bullets": [re.sub(r"<br\s*/?>", " ", part).strip() for part in bullet_parts if part.strip()],
    }


def parse_papers(body: str) -> list[dict[str, Any]]:
    papers: list[dict[str, Any]] = []
    for line in body.splitlines():
        row = ROW_RE.match(line)
        if not row:
            continue
        left, right = row.group(1), row.group(2)
        if left.strip().startswith("**Paper**") or set(left.strip()) <= {"-", " "}:
            continue
        paper = parse_paper_cell(left)
        paper["links"] = parse_links(right)
        papers.append(paper)
    if not papers:
        raise DigestError("no papers parsed from latest issue")
    return papers


def issue_dates(heading: str) -> str:
    match = RANGE_RE.search(heading)
    if not match:
        return heading
    span, year = match.group(1), match.group(2)
    return f"{span} · {year}" if year else span


def render_text(heading: str, papers: list[dict[str, Any]]) -> str:
    lines = [heading, "", f"{len(papers)} papers from {UPSTREAM}", ""]
    for paper in papers:
        rank = f"{paper['rank']}. " if paper["rank"] else ""
        lines.append(f"{rank}{paper['title']}")
        if paper["lead"]:
            lines.append(paper["lead"])
        for bullet in paper["bullets"]:
            lines.append(f"- {bullet}")
        for label, url in paper["links"]:
            lines.append(f"{label}: {url}")
        lines.append("")
    lines.append(f"Source: https://github.com/{UPSTREAM}")
    return "\n".join(lines).strip() + "\n"


def render_html(heading: str, papers: list[dict[str, Any]]) -> str:
    items: list[str] = []
    for paper in papers:
        rank = html_lib.escape(str(paper["rank"] or ""))
        title = html_lib.escape(paper["title"])
        lead = html_lib.escape(paper["lead"])
        bullets = "".join(
            f"<li style=\"margin:0 0 6px;\">{html_lib.escape(b)}</li>" for b in paper["bullets"]
        )
        bullet_html = (
            f'<ul style="margin:8px 0 0 18px;padding:0;color:#333;">{bullets}</ul>'
            if bullets
            else ""
        )
        links = " · ".join(
            f'<a href="{html_lib.escape(url, quote=True)}" style="color:#0b57d0;text-decoration:none;">{html_lib.escape(label)}</a>'
            for label, url in paper["links"]
        )
        items.append(
            f"""
<tr>
  <td style="padding:18px 0;border-bottom:1px solid #ececec;vertical-align:top;">
    <div style="font-size:12px;color:#888;margin-bottom:4px;">{rank}</div>
    <div style="font-size:18px;font-weight:700;line-height:1.3;">{title}</div>
    <p style="margin:8px 0 0;line-height:1.55;color:#222;">{lead}</p>
    {bullet_html}
    <div style="margin-top:10px;font-size:13px;">{links}</div>
  </td>
</tr>"""
        )
    dates = html_lib.escape(issue_dates(heading))
    source = html_lib.escape(f"https://github.com/{UPSTREAM}")
    return f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f4f4f1;color:#111;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f1;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:680px;max-width:680px;background:#fff;border:1px solid #e6e6e1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;">
          <tr>
            <td style="padding:22px 24px;background:#111;color:#fff;">
              <div style="font-size:11px;letter-spacing:.14em;">AI PAPERS OF THE WEEK</div>
              <div style="font-size:22px;font-weight:700;margin-top:6px;">{dates}</div>
              <div style="font-size:13px;opacity:.75;margin-top:6px;">{len(papers)} papers · LLM / NLP / Agent</div>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 24px 12px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {''.join(items)}
              </table>
              <p style="margin:18px 0 8px;font-size:12px;color:#888;">
                Source: <a href="{source}" style="color:#888;">{html_lib.escape(UPSTREAM)}</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(heading: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "heading": heading,
        "sha256": hashlib.sha256(heading.encode("utf-8")).hexdigest(),
        "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "upstream": UPSTREAM,
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def send_resend(subject: str, text: str, html: str) -> None:
    api_key = env("RESEND_API_KEY")
    if not api_key:
        raise DigestError("RESEND_API_KEY is missing")
    payload = json.dumps(
        {
            "from": env("FROM_EMAIL"),
            "to": [env("MAIL_TO")],
            "subject": subject,
            "text": text,
            "html": html,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DigestError(f"Resend failed: HTTP {exc.code} {detail}") from exc
    print(f"sent via Resend: {body}")


def send_smtp(subject: str, text: str, html: str) -> None:
    host = env("SMTP_HOST")
    user = env("SMTP_USER")
    password = env("SMTP_PASSWORD")
    from_email = env("FROM_EMAIL") or user
    to_email = env("MAIL_TO")
    if not all([host, user, password, from_email, to_email]):
        raise DigestError(
            "SMTP_HOST, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL, MAIL_TO are required"
        )
    port = int(env("SMTP_PORT") or "465")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)
    print(f"sent via SMTP {host}:{port} -> {to_email}")


def send_email(subject: str, text: str, html: str) -> None:
    if env("RESEND_API_KEY"):
        send_resend(subject, text, html)
        return
    send_smtp(subject, text, html)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="Parse a local markdown file instead of fetching GitHub")
    parser.add_argument("--year", type=int, help="Year file to fetch")
    parser.add_argument("--dry-run", action="store_true", help="Print the digest, do not send")
    parser.add_argument("--force", action="store_true", help="Send even if this issue was already sent")
    parser.add_argument("--write-html", metavar="PATH", help="Write rendered HTML to a file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    force = args.force or env("DIGEST_FORCE").lower() in {"1", "true", "yes"}
    markdown = fetch_year_markdown(year=args.year, path=args.file)
    heading, body = extract_latest_issue(markdown)
    papers = parse_papers(body)
    text = render_text(heading, papers)
    html = render_html(heading, papers)

    if args.write_html:
        Path(args.write_html).write_text(html, encoding="utf-8")
        print(f"wrote {args.write_html}")

    state = load_state()
    already = state.get("heading") == heading
    print(f"latest: {heading}")
    print(f"papers: {len(papers)}")
    print(f"already sent: {already}")

    if args.dry_run:
        print("---")
        print(text)
        return 0

    if already and not force:
        print("no new issue; skipping")
        return 0

    if not env("MAIL_TO"):
        raise DigestError("MAIL_TO is missing")
    send_email(heading, text, html)
    save_state(heading)
    print(f"updated {STATE_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DigestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
