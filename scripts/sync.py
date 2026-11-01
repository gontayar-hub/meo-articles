"""Pull articles from Google Drive and build the static site.

Rules:
- Month folders are named "<Hebrew month> <year> ..." (e.g. "נובמבר 2026 - לאישור").
- Only folders up to and including the current month are read.
- Inside them, only Google Docs named "DD.MM - <title>" are articles.
- An article is published only when its date is today or earlier (Israel time).
- Drive is read-only. Nothing is moved, edited or deleted there.
"""
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown

ROOT_FOLDER = os.environ.get("DRIVE_ROOT_FOLDER", "1bIny3m4zuMF5KWgPABDI72-ytnOr1FtY")
TZ = ZoneInfo("Asia/Jerusalem")
SITE_NAME = "MEO"
MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
          "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
DOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"
ARTICLE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\s*[-–—]\s*(.+?)\s*$")
FOLDER_RE = re.compile(r"^\s*(\S+)\s+(\d{4})")


def parse_month_folder(title):
    m = FOLDER_RE.match(title)
    if not m or m.group(1) not in MONTHS:
        return None
    return int(m.group(2)), MONTHS.index(m.group(1)) + 1


def parse_article_title(title, year):
    m = ARTICLE_RE.match(title)
    if not m:
        return None
    try:
        date = dt.date(year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return date, m.group(3)


def select_articles(folders, today):
    """folders: list of (folder_title, [(file_id, title, mime), ...]). Returns published articles."""
    out = []
    for folder_title, files in folders:
        ym = parse_month_folder(folder_title)
        if not ym or ym > (today.year, today.month):
            continue
        for file_id, title, mime in files:
            if mime != DOC_MIME:
                continue
            parsed = parse_article_title(title, ym[0])
            if not parsed:
                continue
            date, clean = parsed
            if date <= today:
                out.append({"id": file_id, "date": date, "title": clean})
    out.sort(key=lambda a: (a["date"], a["title"]), reverse=True)
    return out


def split_heading(md_text, fallback):
    lines = md_text.lstrip("﻿").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            return line[2:].strip() or fallback, "\n".join(lines[i + 1:])
        break
    return fallback, "\n".join(lines)


PAGE = """<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --muted:#6b6b70; --line:#e6e6e9; --accent:#0a6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --muted:#9a9aa0; --line:#2a2a2e; --accent:#3c9; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:18px/1.75 system-ui,-apple-system,"Segoe UI",Arial,sans-serif; }}
main {{ max-width:720px; margin:0 auto; padding:32px 16px 64px; }}
header.site {{ border-bottom:1px solid var(--line); }}
header.site a {{ color:var(--fg); text-decoration:none; font-weight:700; display:block; max-width:720px; margin:0 auto; padding:16px; }}
h1 {{ font-size:2rem; line-height:1.3; }}
a {{ color:var(--accent); }}
time, .muted {{ color:var(--muted); font-size:.9rem; }}
ul.list {{ list-style:none; padding:0; }}
ul.list li {{ padding:14px 0; border-bottom:1px solid var(--line); }}
ul.list a {{ color:var(--fg); text-decoration:none; font-weight:600; }}
img {{ max-width:100%; height:auto; }}
table {{ border-collapse:collapse; width:100%; }}
td, th {{ border:1px solid var(--line); padding:6px 10px; }}
</style>
</head>
<body>
<header class="site"><a href="{root}">{site}</a></header>
<main>
{body}
</main>
</body>
</html>
"""


def render_page(title, body, root, description=""):
    return PAGE.format(title=html.escape(title), body=body, root=root, site=SITE_NAME,
                       description=html.escape(description))


def build_site(articles, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("")
    items = []
    for a in articles:
        slug = f"{a['date'].isoformat()}-{a['id'][:6].lower()}"
        page_dir = out / "articles" / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        body = (f"<article><h1>{html.escape(a['title'])}</h1>"
                f"<time datetime=\"{a['date'].isoformat()}\">{a['date'].strftime('%d.%m.%Y')}</time>"
                f"{a['html']}</article>")
        (page_dir / "index.html").write_text(
            render_page(f"{a['title']} | {SITE_NAME}", body, "../../", a["title"]), encoding="utf-8")
        items.append(f"<li><a href=\"articles/{slug}/\">{html.escape(a['title'])}</a><br>"
                     f"<time datetime=\"{a['date'].isoformat()}\">{a['date'].strftime('%d.%m.%Y')}</time></li>")
    listing = "<h1>מאמרים</h1><ul class=\"list\">" + "".join(items) + "</ul>" if items else \
        "<h1>מאמרים</h1><p class=\"muted\">המאמרים הראשונים יעלו בקרוב.</p>"
    (out / "index.html").write_text(render_page(SITE_NAME, listing, "./"), encoding="utf-8")


# ---------- Google Drive (read-only) ----------

def drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ["GDRIVE_KEY"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_children(svc, folder_id):
    items, token = [], None
    while True:
        resp = svc.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=200, pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        items += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            return items


def export_doc_markdown(svc, file_id):
    from googleapiclient.errors import HttpError
    try:
        data = svc.files().export(fileId=file_id, mimeType="text/markdown").execute()
        return data.decode("utf-8") if isinstance(data, bytes) else data
    except HttpError:
        data = svc.files().export(fileId=file_id, mimeType="text/plain").execute()
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        return "\n\n".join(p.strip() for p in text.splitlines() if p.strip())


def main():
    today = dt.datetime.now(TZ).date()
    svc = drive_service()
    folders = []
    for f in list_children(svc, ROOT_FOLDER):
        if f["mimeType"] == FOLDER_MIME and parse_month_folder(f["name"]):
            files = [(c["id"], c["name"], c["mimeType"]) for c in list_children(svc, f["id"])]
            folders.append((f["name"], files))
    articles = select_articles(folders, today)
    for a in articles:
        md_text = export_doc_markdown(svc, a["id"])
        title, body_md = split_heading(md_text, a["title"])
        a["title"] = title
        a["html"] = markdown.markdown(body_md, extensions=["tables", "sane_lists"])
    build_site(articles, "site")
    print(f"{today}: published {len(articles)} article(s)")
    for a in articles:
        print(f"  {a['date']}  {a['title']}")


if __name__ == "__main__":
    sys.exit(main())
