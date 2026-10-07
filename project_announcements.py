"""SQLite persistence and same-origin scraping for Gra-VT projects."""
import html
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE_URL = "https://gra-vt.my"
PROJECTS_URL = f"{BASE_URL}/projects"
DB_PATH = Path(__file__).resolve().parent / "data" / "projects.sqlite3"
_DB_LOCK = threading.RLock()
_FETCH_LOCK = threading.Lock()
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _SameSiteRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.netloc.lower() != "gra-vt.my":
            raise ValueError("Gra-VT 页面重定向到了站外地址，已停止抓取。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_project_db():
    with _DB_LOCK, _connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          url TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          event_date TEXT NOT NULL DEFAULT '',
          image_url TEXT NOT NULL DEFAULT '',
          draft_title TEXT NOT NULL DEFAULT '',
          draft_body TEXT NOT NULL DEFAULT '',
          draft_date TEXT NOT NULL DEFAULT '',
          draft_image_url TEXT NOT NULL DEFAULT '',
          draft_source_url TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft',
          fetch_error TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL,
          fetched_at TEXT NOT NULL DEFAULT '',
          published_at TEXT NOT NULL DEFAULT '',
          published_guild_id TEXT NOT NULL DEFAULT '',
          published_channel_id TEXT NOT NULL DEFAULT '',
          discord_message_id TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS project_announcement_settings (
          guild_id TEXT PRIMARY KEY,
          channel_id TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_publications (
          project_id INTEGER NOT NULL REFERENCES projects(id),
          guild_id TEXT NOT NULL,
          channel_id TEXT NOT NULL,
          discord_message_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'publishing',
          published_at TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(project_id, guild_id)
        );
        CREATE TABLE IF NOT EXISTS project_scrape_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          found INTEGER NOT NULL DEFAULT 0,
          added INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT ''
        );
        """)


def _safe_project_url(href):
    url = urljoin(BASE_URL, str(href or "").strip())
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "gra-vt.my":
        return ""
    if not parsed.path.startswith("/projects/"):
        return ""
    return f"{BASE_URL}{parsed.path.rstrip('/') or '/'}"


def _decode_js_string(value):
    """Decode the JSON-compatible string escapes used in the site's Vite bundle."""
    value = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", value)
    try:
        return json.loads('"' + value.replace("/", r"\/") + '"')
    except (json.JSONDecodeError, ValueError):
        return value.replace(r"\'", "'")


def _balanced_array(source, start):
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in ('"', "'", "`"):
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return ""


def _bundle_objects(array_source):
    objects = []
    depth = 0
    quote = ""
    escaped = False
    object_start = None
    for index, char in enumerate(array_source):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in ('"', "'", "`"):
            quote = char
        elif char == "{":
            if depth == 0:
                object_start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and object_start is not None:
                objects.append(array_source[object_start:index + 1])
                object_start = None
    return objects


def _bundle_projects(bundle):
    """Read the public project index embedded in the Gra-VT SPA bundle."""
    marker = re.search(r"\b[A-Za-z_$][\w$]*=\[\{slug:", bundle)
    if not marker:
        raise ValueError("Gra-VT 项目 bundle 中没有找到项目清单。")
    array_start = bundle.find("[", marker.start())
    objects = _bundle_objects(_balanced_array(bundle, array_start))
    projects = []
    for item in objects:
        def string_field(name):
            match = re.search(rf'\b{name}:"((?:\\.|[^"\\])*)"', item)
            return _decode_js_string(match.group(1)) if match else ""

        title = string_field("title")
        link = _safe_project_url(string_field("link"))
        if not title or not link:
            continue
        start = re.search(r"\bstartDate:xi\((\d+),(\d+),(\d+)\)", item)
        end = re.search(r"\bendDate:xi\((\d+),(\d+),(\d+)\)", item)
        date = ""
        if start:
            date = "-".join(part.zfill(2) if i else part for i, part in enumerate(start.groups()))
            if end:
                date += " – " + "-".join(part.zfill(2) if i else part for i, part in enumerate(end.groups()))
        image = string_field("image")
        projects.append({"url": link, "detail_slug": string_field("slug"), "title": title,
                         "description": "", "event_date": date,
                         "image_url": urljoin(BASE_URL, image) if image else ""})
    if not projects:
        raise ValueError("Gra-VT bundle 已更新，但无法读取项目清单格式。")
    return projects


def _bundle_detail_text(bundle, project_url, detail_slug=""):
    """Load text from a project's lazy-loaded route chunk, if the bundle maps one."""
    route = urlparse(project_url).path.rsplit("/", 1)[-1]
    chunk = None
    for name in dict.fromkeys(filter(None, (detail_slug, route))):
        chunk = re.search(r'assets/([^"\']*' + re.escape(name) + r'[^"\']*\.js)', bundle)
        if chunk:
            break
    if not chunk:
        return ""
    source = _fetch_html(urljoin(BASE_URL, "/" + chunk.group(0)))
    texts = []
    for match in re.finditer(r'children:"((?:\\.|[^"\\])*)"', source):
        value = _plain_text(_decode_js_string(match.group(1)))
        if len(value) >= 24 and value not in texts:
            texts.append(value)
    return "\n\n".join(texts)[:3900]


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.meta = {}
        self.title = ""
        self.in_title = False
        self.text_parts = []
        self._card_depth = 0
        self._card_parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key and attrs.get("content"):
                self.meta[key] = attrs["content"].strip()
        if tag == "title":
            self.in_title = True
        if tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
        if self._card_depth:
            if tag not in _VOID_TAGS:
                self._card_depth += 1
            # The project-card class may be on a wrapping div while the
            # actual project URL lives on an anchor somewhere inside it.
            if tag == "a" and not self._card_href and attrs.get("href"):
                self._card_href = attrs["href"]
                self.cards.append((self._card_href, ""))
        elif "project-card" in classes:
            self._card_depth = 1
            # Also support pages where the card itself is the anchor.
            self._card_href = attrs.get("href", "") if tag == "a" else ""
            self._card_parts = []
            if self._card_href:
                # Record the target as soon as its opening tag is parsed. The
                # link must not depend on every nested card tag being balanced.
                self.cards.append((self._card_href, ""))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in ("script", "style", "noscript", "svg") and self._skip_depth:
            self._skip_depth -= 1
        if self._card_depth and tag not in _VOID_TAGS:
            self._card_depth -= 1
            if self._card_depth == 0:
                self._card_href = ""

    def handle_data(self, data):
        value = " ".join(str(data).split())
        if not value:
            return
        if self.in_title:
            self.title += value
        if not self._skip_depth:
            self.text_parts.append(value)
            if self._card_depth:
                self._card_parts.append(value)


def _fetch_html(url):
    request = Request(url, headers={"User-Agent": "GraVT-ProjectAnnouncements/1.0 (+personal dashboard)", "Accept": "text/html"})
    try:
        with build_opener(_SameSiteRedirects()).open(request, timeout=15) as response:
            if response.status < 200 or response.status >= 300:
                raise ValueError(f"网站返回 HTTP {response.status}")
            raw = response.read(2_500_001)
            if len(raw) > 2_500_000:
                raise ValueError("页面超过 2.5 MB，已停止读取")
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except HTTPError as exc:
        raise ValueError(f"网站返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"无法读取 Gra-VT 页面：{exc}") from exc


def _plain_text(value):
    value = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", value).strip()


def _extract_date(parser):
    for key in ("article:published_time", "og:updated_time", "date", "publish_date"):
        value = parser.meta.get(key, "")
        if value:
            return value[:120]
    match = re.search(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[\w ,.-]{2,32}\b|\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " ".join(parser.text_parts), re.I)
    return match.group(0).strip() if match else ""


def _read_project(url):
    parser = _PageParser()
    parser.feed(_fetch_html(url))
    title = parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title
    title = _plain_text(title).removesuffix(" | Gra-VT").strip()
    description = parser.meta.get("og:description") or parser.meta.get("description") or parser.meta.get("twitter:description")
    if not description:
        description = " ".join(parser.text_parts)
    description = _plain_text(description)
    if len(description) > 4000:
        description = description[:3997].rstrip() + "..."
    image = parser.meta.get("og:image") or parser.meta.get("twitter:image") or ""
    image = urljoin(url, image) if image else ""
    if image and urlparse(image).scheme not in ("http", "https"):
        image = ""
    return {"title": title or url.rstrip("/").split("/")[-1].replace("-", " ").title(),
            "description": description, "event_date": _extract_date(parser), "image_url": image}


def _row(row):
    return dict(row) if row else None


def list_projects():
    with _DB_LOCK, _connect() as db:
        projects = [_row(r) for r in db.execute("SELECT * FROM projects ORDER BY first_seen_at DESC, id DESC")]
        settings = {r["guild_id"]: r["channel_id"] for r in db.execute("SELECT guild_id, channel_id FROM project_announcement_settings")}
        run = db.execute("SELECT * FROM project_scrape_runs ORDER BY id DESC LIMIT 1").fetchone()
        publication_rows = db.execute("SELECT project_id,guild_id,channel_id,discord_message_id,status,published_at FROM project_publications WHERE status='published'").fetchall()
    publications = {}
    for row in publication_rows:
        publications.setdefault(str(row["project_id"]), []).append(dict(row))
    for project in projects:
        project["publications"] = publications.get(str(project["id"]), [])
        project["published_guilds"] = [entry["guild_id"] for entry in project["publications"]]
        if project["publications"] and project["status"] == "draft":
            project["status"] = "published_somewhere"
    return {"projects": projects, "channels": settings, "last_run": _row(run)}


def scrape_new_projects():
    if not _FETCH_LOCK.acquire(blocking=False):
        raise RuntimeError("抓取任务正在运行，请稍后再试。")
    started = _now()
    found = added = failed = 0
    run_error = ""
    try:
        listing_html = _fetch_html(PROJECTS_URL)
        listing = _PageParser()
        listing.feed(listing_html)
        bundle = ""
        if listing.cards:
            discovered = [{"url": url, "title": "", "description": "", "event_date": "", "image_url": ""}
                          for url in dict.fromkeys(filter(None, (_safe_project_url(href) for href, _ in listing.cards)))]
        else:
            # Gra-VT is a client-rendered SPA: the web server serves an empty
            # #root shell, while the card data lives in its same-origin Vite bundle.
            module_script = re.search(
                r'<script\b(?=[^>]*\btype=["\']module["\'])(?=[^>]*\bsrc=["\']([^"\']+)["\'])[^>]*>',
                listing_html, re.I,
            )
            if not module_script:
                raise ValueError("项目页没有卡片 HTML，也没有可读取的 module script。")
            bundle = _fetch_html(urljoin(PROJECTS_URL, module_script.group(1)))
            discovered = _bundle_projects(bundle)
        # Canonical URL is the deduplication key even if the page repeats an item.
        by_url = {item["url"]: item for item in discovered}
        discovered = list(by_url.values())
        found = len(discovered)
        for item in discovered:
            url = item["url"]
            with _DB_LOCK, _connect() as db:
                existing = db.execute("SELECT id, status FROM projects WHERE url=?", (url,)).fetchone()
            if existing and existing["status"] != "fetch_failed":
                continue
            try:
                details = item
                if bundle:
                    details = dict(item)
                    details["description"] = _bundle_detail_text(bundle, url, details.get("detail_slug", ""))
                    if not details["description"]:
                        # Some projects share a route chunk name that differs
                        # from their public URL; the listing data remains useful.
                        fallback_description = _read_project(url)["description"]
                        if fallback_description.lower() != "grá-vt":
                            details["description"] = fallback_description
                else:
                    details = _read_project(url)
                now = _now()
                with _DB_LOCK, _connect() as db:
                    if existing:
                        db.execute("""UPDATE projects SET title=?,description=?,event_date=?,image_url=?,
                          draft_title=CASE WHEN draft_title='' THEN ? ELSE draft_title END,
                          draft_body=CASE WHEN draft_body='' THEN ? ELSE draft_body END,
                          draft_date=CASE WHEN draft_date='' THEN ? ELSE draft_date END,
                          draft_image_url=CASE WHEN draft_image_url='' THEN ? ELSE draft_image_url END,
                          status='draft',fetch_error='',fetched_at=? WHERE url=?""",
                          (details["title"], details["description"], details["event_date"], details["image_url"],
                           details["title"], details["description"], details["event_date"], details["image_url"], now, url))
                    else:
                        db.execute("""INSERT OR IGNORE INTO projects
                          (url,title,description,event_date,image_url,draft_title,draft_body,draft_date,draft_image_url,
                           draft_source_url,status,first_seen_at,fetched_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?, 'draft',?,?)""",
                          (url, details["title"], details["description"], details["event_date"], details["image_url"],
                           details["title"], details["description"], details["event_date"], details["image_url"], url, now, now))
                        added += db.execute("SELECT changes()").fetchone()[0]
            except Exception as exc:
                failed += 1
                message = str(exc)[:1000]
                now = _now()
                with _DB_LOCK, _connect() as db:
                    if existing:
                        db.execute("UPDATE projects SET status='fetch_failed',fetch_error=? WHERE url=?", (message, url))
                    else:
                        db.execute("""INSERT OR IGNORE INTO projects(url,draft_source_url,status,fetch_error,first_seen_at)
                          VALUES(?,?,'fetch_failed',?,?)""", (url, url, message, now))
        return {"found": found, "added": added, "failed": failed}
    except Exception as exc:
        run_error = str(exc)[:1000]
        raise
    finally:
        try:
            with _DB_LOCK, _connect() as db:
                db.execute("INSERT INTO project_scrape_runs(started_at,finished_at,found,added,failed,error) VALUES(?,?,?,?,?,?)",
                           (started, _now(), found, added, failed, run_error))
        finally:
            _FETCH_LOCK.release()


def update_draft(project_id, payload):
    allowed = {"draft_title", "draft_body", "draft_date", "draft_image_url", "draft_source_url"}
    values = {key: str(payload.get(key) or "").strip() for key in allowed}
    if len(values["draft_title"]) > 256 or len(values["draft_body"]) > 4000:
        raise ValueError("标题最多 256 个字符，正文最多 4000 个字符。")
    for key in ("draft_image_url", "draft_source_url"):
        if values[key] and (urlparse(values[key]).scheme not in ("http", "https") or not urlparse(values[key]).netloc):
            raise ValueError("图片和来源链接必须使用有效的 HTTP 或 HTTPS 地址。")
    with _DB_LOCK, _connect() as db:
        row = db.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        if row["status"] == "published":
            raise ValueError("已发布项目不能编辑；请在 Discord 中修改已发布消息。")
        db.execute("UPDATE projects SET draft_title=?,draft_body=?,draft_date=?,draft_image_url=?,draft_source_url=?,status='draft' WHERE id=?",
                   (values["draft_title"], values["draft_body"], values["draft_date"], values["draft_image_url"], values["draft_source_url"], project_id))
        return _row(db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())


def set_announcement_channel(guild_id, channel_id):
    with _DB_LOCK, _connect() as db:
        db.execute("""INSERT INTO project_announcement_settings(guild_id,channel_id,updated_at) VALUES(?,?,?)
          ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,updated_at=excluded.updated_at""",
          (str(guild_id), str(channel_id), _now()))


def publish_project(project_id, guild_id, channel_id):
    with _DB_LOCK, _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        expected = db.execute("SELECT channel_id FROM project_announcement_settings WHERE guild_id=?", (str(guild_id),)).fetchone()
        if not expected or expected["channel_id"] != str(channel_id):
            raise ValueError("请先为所选服务器配置公告频道，并使用该频道发布。")
        for key in ("draft_title", "draft_body"):
            if not row[key].strip():
                raise ValueError("发布前请填写公告标题和正文。")
        try:
            db.execute("INSERT INTO project_publications(project_id,guild_id,channel_id,status) VALUES(?,?,?,'publishing')",
                       (project_id, str(guild_id), str(channel_id)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("此项目已发布或正在发布到所选服务器，不能重复发送。") from exc
        return _row(row)


def release_project_publish(project_id, guild_id):
    with _DB_LOCK, _connect() as db:
        db.execute("DELETE FROM project_publications WHERE project_id=? AND guild_id=? AND status='publishing'",
                   (project_id, str(guild_id)))


def mark_published(project_id, guild_id, channel_id, message_id):
    with _DB_LOCK, _connect() as db:
        db.execute("""UPDATE project_publications SET status='published',published_at=?,
          channel_id=?,discord_message_id=? WHERE project_id=? AND guild_id=? AND status='publishing'""",
          (_now(), str(channel_id), str(message_id), project_id, str(guild_id)))
        return db.execute("SELECT changes()").fetchone()[0] == 1
