#!/usr/bin/env python3
"""Manga Reader: paste a link, download the pages, read them locally.

Standard library only. Run it, then use the page it opens in your browser.
Everything is stored next to this file in ./library.
"""
import argparse
import gzip
import html
import io
import json
import os
import queue
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
# As an .exe, the library stays beside the .exe while bundled files unpack to a temp folder.
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
BUNDLE = Path(sys._MEIPASS) if FROZEN else ROOT
DEFAULT_LIBRARY = ROOT / "library"
LIBRARY = DEFAULT_LIBRARY
LIBRARY_WARNING = ""
INDEX_HTML = BUNDLE / "static" / "index.html"
ICON = BUNDLE / "static" / "icon.ico"
DEFAULT_PORT = 8765
WINDOW_PROFILE = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "MangaReader" / "window-profile"
SETTINGS_FILE = WINDOW_PROFILE.parent / "settings.json"
MIN_COVER_WIDTH = 100
COVER_FILENAME_RE = re.compile(r"cover|poster", re.I)
WINDOW_START_SECONDS = 20
WINDOW_BROWSERS = (
    r"Microsoft\Edge\Application\msedge.exe",
    r"Google\Chrome\Application\chrome.exe",
)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
MAX_PAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
DOWNLOAD_WORKERS = 6
CHAPTER_DELAY_SECONDS = 0.5

MIN_PAGE_WIDTH = 300
MAX_PAGE_ASPECT = 5.0  # wider than this is an ad banner, unless it matches the image before it
MIN_GROUP_SIZE = 3
MAX_CHAPTER_LINK_GAP = 4  # other links allowed between two rows of one chapter list
MAX_CHAPTER_JUMP = 25  # a bigger leap in numbering means a different list

CHAPTER_RE = re.compile(
    r"(?<![a-z])(?:chapter|chap|ch|episode|ep)s?[\s._/\-]*0*(\d+(?:\.\d+)?)", re.I)
IMAGE_URL_RE = re.compile(
    r"""https?:\\?/\\?/[^"'\s<>\\]*(?:\\/[^"'\s<>\\]*)*?\.(?:jpe?g|png|webp|avif|gif)(?:\?[^"'\s<>\\]*)?""",
    re.I)
NOT_A_PAGE_RE = re.compile(
    r"(?:^|[\W_])(?:logo|avatar|icon|banner|ads?|sprite|thumb|thumbnail|cover|favicon|"
    r"gravatar|emoji|placeholder|loading|loader|spinner|blank|pixel)(?:$|[\W_\d])", re.I)
LAZY_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-url", "data-cfsrc",
              "data-echo", "data-lazy", "data-img", "data-image", "src")
SRCSET_ATTRS = ("data-srcset", "data-lazy-srcset", "srcset")


class FetchError(Exception):
    """A failure with a message that makes sense to show the user."""


class Blocked(FetchError):
    """The site refused an automated request; the browser button still works."""


# ---------------------------------------------------------------- fetching

def normalize_url(url):
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise FetchError("That doesn't look like a web link (it should start with http:// or https://).")
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~-._")
    query = urllib.parse.quote(parts.query, safe="/%:@!$&'()*+,;=~-._?")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def fetch(url, referer=None, accept="*/*", limit=MAX_PAGE_BYTES, timeout=30):
    """Return (body, final_url, content_type)."""
    url = normalize_url(url)
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip, deflate",
               "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise FetchError(f"Response from {url} is too large.")
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                body = gzip.decompress(body)
            elif encoding == "deflate":
                body = zlib.decompress(body)
            return body, response.geturl(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        server = (error.headers.get("Server") or "").lower()
        if error.code in (401, 403, 429, 503) or "cloudflare" in server:
            raise Blocked(f"The site refused the request (HTTP {error.code}).") from error
        raise FetchError(f"The site answered HTTP {error.code}.") from error
    except urllib.error.URLError as error:
        raise FetchError(f"Couldn't reach the site: {error.reason}") from error
    except (TimeoutError, OSError, zlib.error) as error:
        raise FetchError(f"Connection problem: {error}") from error


def fetch_html(url):
    body, final_url, content_type = fetch(url, accept="text/html,application/xhtml+xml,*/*;q=0.8")
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    try:
        text = body.decode(match.group(1) if match else "utf-8", "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    if re.search(r"<title>\s*(just a moment|attention required|checking your browser)", text, re.I):
        raise Blocked("The site is showing a browser check to automated requests.")
    return text, final_url


# ---------------------------------------------------------------- page parsing

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images, self.links, self.scripts = [], [], []
        self.title, self.og_title, self.og_image, self.base = "", "", "", None
        self._capture = None
        self._text = []
        self._attrs = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img":
            url = image_url_from_attrs(a)
            if url:
                self.images.append(url)
        elif tag == "base" and a.get("href") and not self.base:
            self.base = a["href"]
        elif tag == "meta" and (a.get("property") or a.get("name")) == "og:title":
            self.og_title = a.get("content") or ""
        elif tag == "meta" and (a.get("property") or a.get("name")) in ("og:image", "twitter:image"):
            self.og_image = self.og_image or a.get("content") or ""
        elif tag in ("a", "option", "title", "script"):
            self._flush()
            self._capture, self._attrs, self._text = tag, a, []

    def handle_data(self, data):
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == self._capture:
            self._flush()

    def _flush(self):
        # A space between pieces, so "<span>Chapter 6</span><span>7 months ago</span>" stays chapter 6.
        tag, attrs, text = self._capture, self._attrs, " ".join(self._text)
        self._capture, self._attrs, self._text = None, None, []
        if tag == "title":
            self.title = self.title or " ".join(text.split())
        elif tag == "script":
            self.scripts.append(text)
        elif tag == "a" and attrs.get("href"):
            self.links.append((attrs["href"], " ".join(text.split())))
        elif tag == "option" and attrs.get("value"):
            self.links.append((attrs["value"], " ".join(text.split())))


def image_url_from_attrs(attrs):
    for name in LAZY_ATTRS:
        value = (attrs.get(name) or "").strip()
        if value and not value.startswith(("data:", "blob:", "about:", "#")):
            return value
    for name in SRCSET_ATTRS:
        value = (attrs.get(name) or "").strip()
        if value and not value.startswith("data:"):
            return value.split(",")[-1].strip().split(" ")[0]
    return None


def looks_like_page(url):
    filename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    return not NOT_A_PAGE_RE.search(filename)


def largest_group(urls):
    """Manga pages share a folder; ads and site graphics usually don't."""
    groups = {}
    for url in urls:
        folder = urllib.parse.urlsplit(url).path.rsplit("/", 1)[0]
        groups.setdefault(folder, []).append(url)
    best = max(groups.values(), key=len, default=[])
    return best if len(best) >= MIN_GROUP_SIZE else list(urls)


def unique(items):
    return list(dict.fromkeys(items))


def extract_images(parser, base_url):
    absolute = [urllib.parse.urljoin(base_url, u) for u in parser.images]
    candidates = unique(u for u in absolute if u.startswith("http") and looks_like_page(u))
    from_tags = largest_group(candidates)
    if len(from_tags) >= MIN_GROUP_SIZE:
        return from_tags
    # Many readers keep the page list in a script and build the <img> tags later.
    found = []
    for script in parser.scripts:
        script = script.replace("\\u002F", "/").replace("\\u002f", "/")
        found += [m.replace("\\/", "/") for m in IMAGE_URL_RE.findall(script)]
    from_scripts = largest_group(unique(u for u in found if looks_like_page(u)))
    return from_scripts if len(from_scripts) > len(from_tags) else from_tags


def extract_cover(parser, base_url):
    named = [u for u in parser.images if COVER_FILENAME_RE.search(urllib.parse.urlsplit(u).path)]
    cover = named[0] if named else parser.og_image
    return urllib.parse.urljoin(base_url, cover) if cover and not cover.startswith("data:") else ""


def chapter_number(text):
    match = CHAPTER_RE.search(text or "")
    return float(match.group(1)) if match else None


def number_from_url(url):
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    number = chapter_number(path)
    if number is None:
        return None
    # "chapter-12-5" at the end of a path means chapter 12.5
    decimal = re.search(r"(?:chapter|chap|ch|episode|ep)s?[\s._/\-]*\d+[-_](\d)/?$", path, re.I)
    if decimal and number == int(number):
        number += int(decimal.group(1)) / 10
    return number


def consistent_runs(entries):
    """Split links into stretches that read like one chapter list: close together, counting one way."""
    runs, run, direction = [], [], 0
    for entry in entries:
        if run:
            step = entry["number"] - run[-1]["number"]
            if (entry["position"] - run[-1]["position"] > MAX_CHAPTER_LINK_GAP
                    or abs(step) > MAX_CHAPTER_JUMP):
                runs.append(run)
                run, direction = [], 0
            elif step and direction and (step > 0) != (direction > 0):
                # The link before a turn may really be the first row of the next list.
                runs.append(run)
                run, direction = [run[-1]], step
            elif step:
                direction = step
        run.append(entry)
    return runs + [run] if run else runs


def series_slug(base_url):
    segments = [s for s in urllib.parse.unquote(urllib.parse.urlsplit(base_url).path).split("/") if s]
    while segments and CHAPTER_RE.search(segments[-1]) and CHAPTER_RE.search(segments[-1]).start() == 0:
        segments.pop()
    slug_text = CHAPTER_RE.split(segments[-1])[0].strip("-_ ").lower() if segments else ""
    return slug_text if len(slug_text) >= 4 else ""


def extract_chapters(parser, base_url):
    host = urllib.parse.urlsplit(base_url).netloc.removeprefix("www.")
    groups = {}
    for position, (href, text) in enumerate(parser.links):
        url = urllib.parse.urljoin(base_url, href)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or parts.netloc.removeprefix("www.") != host:
            continue
        path = urllib.parse.unquote(parts.path)
        match = CHAPTER_RE.search(path)
        if match:
            key, number = path[:match.start()], number_from_url(url)
        else:
            number = chapter_number(text)
            if number is None:
                continue
            key = path.rsplit("/", 1)[0]
        groups.setdefault(key, []).append({"number": number, "position": position, "url": url.split("#")[0]})
    # "Latest updates" lists link to other series, often under the same path as the real list.
    # Those jump around, while a real chapter list counts steadily and links to this series.
    slug_text = series_slug(base_url)

    def score(run):
        return (any(slug_text and slug_text in e["url"].lower() for e in run),
                len({e["number"] for e in run}))

    runs = [run for entries in groups.values() for run in consistent_runs(entries)]
    best = {}
    for entry in max(runs, key=score, default=[]):
        best.setdefault(entry["number"], entry["url"])
    return [{"number": n, "label": chapter_label(n), "url": u} for n, u in sorted(best.items())]


def chapter_label(number):
    return f"Chapter {number:g}" if number is not None else "Chapter 1"


def clean_title(title):
    title = html.unescape(title or "").strip()
    match = CHAPTER_RE.search(title)
    if match and match.start() > 0:
        title = title[:match.start()]
    title = re.split(r"\s+[|–—]\s+|\s+-\s+", title.strip(" -|:,–—"))[0]
    title = re.sub(r"^(read)\s+", "", title, flags=re.I)
    title = re.sub(r"\s+(manga|manhwa|manhua|online|free|read)\b.*$", "", title, flags=re.I)
    return title.strip(" -|:,") or "Untitled"


def analyze(url):
    text, final_url = fetch_html(url)
    parser = PageParser()
    parser.feed(text)
    parser.close()
    base =urllib.parse.urljoin(final_url, parser.base) if parser.base else final_url
    images = extract_images(parser, base)
    chapters = extract_chapters(parser, base)
    number = number_from_url(final_url)
    if number is None:  # addresses like /read/25937 only say which chapter in the page title
        number = chapter_number(parser.og_title or parser.title)
    if number is not None:
        kind = "chapter"
    elif chapters:
        kind = "series"
    elif images:
        kind = "chapter"
    else:
        kind = "none"
    return {"url": final_url, "title": clean_title(parser.og_title or parser.title), "kind": kind,
            "label": chapter_label(number), "number": number, "images": images, "chapters": chapters,
            "cover": extract_cover(parser, base)}


# ---------------------------------------------------------------- image helpers

def sniff_extension(data):
    """Only ever store real image data, whatever the server claims it sent."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis", b"mif1"):
        return ".avif"
    return None


def image_size(data):
    """Return (width, height), or (0, 0) when the format isn't understood."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:3] == b"GIF":
            return struct.unpack("<HH", data[6:10])
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                bits = struct.unpack("<I", data[21:25])[0]
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8X":
                return (int.from_bytes(data[24:27], "little") + 1,
                        int.from_bytes(data[27:30], "little") + 1)
        if data[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                if marker == 0xFF:
                    i += 1
                    continue
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        pass
    return 0, 0


def drop_non_pages(pages):
    """Remove leftovers that survived URL filtering: small graphics, banners, tiny outliers."""
    known = [p["w"] for p in pages if p["w"]]
    median = statistics.median(known) if known else 0

    def keep(page, previous):
        w, h = page["w"], page["h"]
        if not w or not h:
            return True
        if w < MIN_PAGE_WIDTH:
            return False
        # Exactly as wide as the image before it means a slice of that image, whatever its shape.
        if previous and w == previous["w"]:
            return True
        if w < median * 0.5:
            return False
        return w / h <= MAX_PAGE_ASPECT

    kept = []
    for page in pages:
        if keep(page, kept[-1] if kept else None):
            kept.append(page)
    return kept or pages


def is_slice_of(page, above):
    """Sites that cut pages leave a short remainder, exactly as wide as the piece above it."""
    w, h = page["w"], page["h"]
    # Manga pages stand taller than they are wide, so a shorter, landscape piece isn't a page.
    return bool(w and h) and w == above["w"] and h < above["h"] and h < w


def join_slices(pages):
    """Some sites cut each page in two. Attach those strips to the page they belong under."""
    joined = []
    for page in pages:
        if joined and is_slice_of(page, joined[-1]):
            joined[-1].setdefault("parts", []).append(page)
        else:
            joined.append(page)
    return joined


# ---------------------------------------------------------------- library

RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                  *(f"lpt{i}" for i in range(1, 10))}


def slug(text):
    # Keep decimals apart, or chapter 6.5 would land in the same folder as chapter 65.
    value = re.sub(r"(\d)\.(\d)", r"\1-\2", text or "")
    value = re.sub(r"[^\w\- ]+", "", value, flags=re.UNICODE).strip().replace(" ", "-")
    value = re.sub(r"-{2,}", "-", value)[:60].strip("-.")
    if not value:
        value = "item-" + uuid.uuid4().hex[:8]
    return f"x-{value}" if value.lower() in RESERVED_NAMES else value


def natural_key(text):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def safe_child(parent, name):
    if not name or name.startswith(".") or "/" in name or "\\" in name or ":" in name:
        raise KeyError(name)
    path = (parent / name).resolve()
    if path.parent != parent.resolve() or not path.exists():
        raise KeyError(name)
    return path


def read_json(path, default=None):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), "utf-8")
    os.replace(temp, path)


meta_lock = threading.Lock()


def update_manga_meta(folder, **fields):
    with meta_lock:
        meta = read_json(folder / "manga.json", {}) or {}
        meta.update(fields)
        write_json(folder / "manga.json", meta)


def ensure_manga(title, source=""):
    folder = LIBRARY / slug(title)
    folder.mkdir(parents=True, exist_ok=True)
    meta = read_json(folder / "manga.json", {}) or {}
    update_manga_meta(folder, title=meta.get("title") or title,
                      source=meta.get("source") or source, updated=time.time())
    return folder


def manga_folder(name):
    """A folder in the library that really is a manga; the library may sit among other files."""
    folder = safe_child(LIBRARY, name)
    if not (folder / "manga.json").exists():
        raise KeyError(name)
    return folder


def save_cover(folder, url, referer, replace=False):
    """Fetch the series cover. Failing is fine: the library falls back to the first page."""
    meta = read_json(folder / "manga.json", {}) or {}
    existing = meta.get("cover")
    if not url or (existing and (folder / existing).exists() and not replace):
        return
    try:
        data, _, _ = fetch(url, referer=referer, accept="image/avif,image/webp,image/*,*/*;q=0.8",
                           limit=MAX_IMAGE_BYTES)
    except FetchError:
        return
    extension = sniff_extension(data)
    width = image_size(data)[0]
    if not extension or (width and width < MIN_COVER_WIDTH):
        return
    name = "cover" + extension
    if existing and existing != name:
        (folder / existing).unlink(missing_ok=True)
    (folder / name).write_bytes(data)
    update_manga_meta(folder, cover=name, coverAdded=time.time())


def fetch_missing_covers():
    """Give manga saved without a cover one, using the page they were downloaded from."""
    for item in library_index():
        folder = LIBRARY / item["id"]
        meta = read_json(folder / "manga.json", {}) or {}
        if meta.get("cover") or not str(meta.get("source", "")).startswith("http"):
            continue
        try:
            result = analyze(meta["source"])
        except FetchError:
            continue
        save_cover(folder, result["cover"], result["url"])


def cover_url(folder, meta, chapters):
    cover = meta.get("cover")
    if cover and (folder / cover).exists():
        return f"/library/{q(folder.name)}/{q(cover)}?v={int(meta.get('coverAdded', 0))}"
    first = chapters[0]
    return f"/library/{q(folder.name)}/{q(first['id'])}/{q(first['pages'][0]['f'])}"


def load_library_setting():
    global LIBRARY, LIBRARY_WARNING
    configured = (read_json(SETTINGS_FILE, {}) or {}).get("libraryPath")
    LIBRARY = Path(configured) if configured else DEFAULT_LIBRARY
    try:
        LIBRARY.mkdir(parents=True, exist_ok=True)
    except OSError:
        LIBRARY_WARNING = f"Your download folder {configured} isn't available, so a temporary one is in use."
        LIBRARY = DEFAULT_LIBRARY
        LIBRARY.mkdir(exist_ok=True)


def manga_folders():
    return [f for f in LIBRARY.iterdir() if f.is_dir() and (f / "manga.json").exists()]


def change_library(raw_path, move):
    global LIBRARY, LIBRARY_WARNING
    with jobs_lock:
        if any(j["status"] in ("queued", "running") for j in jobs.values()):
            raise ValueError("Wait for your downloads to finish before changing the folder.")
    new = Path((raw_path or "").strip().strip('"')).expanduser()
    if not new.is_absolute():
        raise ValueError("Enter a full folder path, like D:\\Manga.")
    new = new.resolve()
    if new == LIBRARY.resolve():
        return {"path": str(LIBRARY), "moved": 0, "skipped": []}
    folders = manga_folders()
    for folder in folders:
        if new == folder.resolve() or folder.resolve() in new.parents:
            raise ValueError("That folder is inside one of your manga. Pick one outside it.")
    try:
        new.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=new):
            pass
    except OSError as error:
        raise ValueError(f"Can't save to that folder: {error.strerror or error}") from error
    moved, skipped = 0, []
    if move:
        for folder in folders:
            target = new / folder.name
            if target.exists():
                skipped.append(folder.name)
                continue
            try:
                shutil.move(str(folder), str(target))
                moved += 1
            except OSError:
                skipped.append(folder.name)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json(SETTINGS_FILE, {"libraryPath": str(new)})
    LIBRARY, LIBRARY_WARNING = new, ""
    return {"path": str(new), "moved": moved, "skipped": skipped}


def browse_for_folder():
    """Show Windows' own folder picker. Returns the chosen path, or None if cancelled."""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$owner=New-Object Windows.Forms.Form;$owner.TopMost=$true;"
        "$dialog=New-Object Windows.Forms.FolderBrowserDialog;"
        "$dialog.Description='Choose where Manga Reader saves your manga';"
        "$dialog.SelectedPath=$env:MANGA_READER_START;"
        "if($dialog.ShowDialog($owner) -eq 'OK'){$dialog.SelectedPath}")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True, encoding="utf-8",
        errors="replace", env={**os.environ, "MANGA_READER_START": str(LIBRARY)},
        creationflags=subprocess.CREATE_NO_WINDOW)
    return result.stdout.strip().lstrip("\ufeff") or None


def list_chapters(folder):
    chapters = []
    for child in folder.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            meta = read_json(child / "chapter.json")
            if meta and meta.get("pages"):
                chapters.append({**meta, "id": child.name})

    def order(chapter):
        number = chapter.get("number")
        return (number is None, number or 0, natural_key(chapter["title"]))

    return sorted(chapters, key=order)


def library_index():
    items = []
    if LIBRARY.exists():
        for folder in LIBRARY.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            chapters = list_chapters(folder)
            if not chapters:
                continue
            meta = read_json(folder / "manga.json", {}) or {}
            items.append({
                "id": folder.name,
                "title": meta.get("title") or folder.name,
                "source": meta.get("source", ""),
                "updated": meta.get("updated", 0),
                "chapterCount": len(chapters),
                "pageCount": sum(len(c["pages"]) for c in chapters),
                "cover": cover_url(folder, meta, chapters),
            })
    return sorted(items, key=lambda m: -m["updated"])


def q(text):
    return urllib.parse.quote(text, safe="")


def save_chapter(manga_folder, label, number, source, staging, pages):
    """Move a finished staging folder into place so a chapter never appears half written."""
    meta = {"title": label, "number": number, "source": source, "added": time.time(), "pages": pages}
    write_json(staging / "chapter.json", meta)
    target = manga_folder / slug(label)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)


# ---------------------------------------------------------------- download jobs

jobs = {}
jobs_lock = threading.Lock()
job_queue = queue.Queue()


def update_job(job, **fields):
    with jobs_lock:
        job.update(fields)


def download_one(url, referer, destination):
    delay = 1.0
    for attempt in range(3):
        try:
            data, _, _ = fetch(url, referer=referer, accept="image/avif,image/webp,image/*,*/*;q=0.8",
                               limit=MAX_IMAGE_BYTES, timeout=60)
            break
        except Blocked:
            raise
        except FetchError:
            if attempt == 2:
                raise
            time.sleep(delay)
            delay *= 2
    extension = sniff_extension(data)
    if not extension:
        raise FetchError("not an image")
    path = destination.with_suffix(extension)
    path.write_bytes(data)
    w, h = image_size(data)
    return {"f": path.name, "w": w, "h": h}


def download_chapter(job, manga_folder, item):
    label = item.get("label") or chapter_label(item.get("number"))
    referer = item.get("referer") or item.get("url")
    images = item.get("images")
    number = item.get("number")
    update_job(job, current=label, pagesDone=0, pagesTotal=0)
    if not images:
        result = analyze(item["url"])
        images, referer = result["images"], result["url"]
    if not images:
        raise FetchError("no pages found on this page")
    update_job(job, pagesTotal=len(images))
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=manga_folder))
    try:
        failures = []

        def work(pair):
            index, url = pair
            try:
                return download_one(url, referer, staging / f"{index:04d}")
            except FetchError as error:
                failures.append(str(error))
                return None
            finally:
                with jobs_lock:
                    job["pagesDone"] += 1

        with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
            results = list(pool.map(work, enumerate(images, 1)))
        pages = [p for p in results if p]
        if not pages:
            raise FetchError(failures[0] if failures else "no pages could be downloaded")
        kept = drop_non_pages(pages)
        for page in pages:
            if page not in kept:
                (staging / page["f"]).unlink(missing_ok=True)
        save_chapter(manga_folder, label, number, referer or "", staging, join_slices(kept))
        if failures:
            with jobs_lock:
                job["warnings"].append(f"{label}: {len(failures)} of {len(images)} pages failed")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def job_worker():
    while True:
        job, body = job_queue.get()
        update_job(job, status="running")
        folder = ensure_manga(body["title"], body.get("source", ""))
        save_cover(folder, body.get("cover"), body.get("source"), bool(body.get("replaceCover")))
        items = body["items"]
        saved = 0
        for position, item in enumerate(items):
            try:
                download_chapter(job, folder, item)
                saved += 1
            except Blocked as error:
                with jobs_lock:
                    job["warnings"].append(f"{item.get('label')}: {error}")
                    job["blocked"] = True
            except (FetchError, OSError) as error:
                with jobs_lock:
                    job["warnings"].append(f"{item.get('label')}: {error}")
            update_job(job, chaptersDone=position + 1)
            if position + 1 < len(items):
                time.sleep(CHAPTER_DELAY_SECONDS)
        update_job(job, status="done" if saved else "error", current="", mangaId=folder.name)
        job_queue.task_done()


def start_job(body):
    title = (body.get("title") or "").strip()
    items = body.get("items") or []
    if not title or not items:
        raise ValueError("A title and at least one chapter are required.")
    job = {"id": uuid.uuid4().hex[:10], "title": title, "status": "queued", "current": "",
           "chaptersDone": 0, "chaptersTotal": len(items), "pagesDone": 0, "pagesTotal": 0,
           "warnings": [], "blocked": False, "mangaId": slug(title), "created": time.time()}
    with jobs_lock:
        jobs[job["id"]] = job
    job_queue.put((job, {"title": title, "items": items, "source": body.get("source", ""),
                         "cover": body.get("cover", ""), "replaceCover": body.get("replaceCover", False)}))
    return job


# ---------------------------------------------------------------- local imports

def import_archive(path, title, label):
    pages = []
    folder = ensure_manga(title, "imported file")
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=folder))
    try:
        with zipfile.ZipFile(path) as archive:
            names = [i for i in archive.infolist()
                     if not i.is_dir() and "__MACOSX" not in i.filename and i.file_size <= MAX_IMAGE_BYTES]
            for info in sorted(names, key=lambda i: natural_key(i.filename)):
                with archive.open(info) as source:
                    data = source.read(MAX_IMAGE_BYTES + 1)
                extension = sniff_extension(data)
                if not extension or len(data) > MAX_IMAGE_BYTES:
                    continue
                name = f"{len(pages) + 1:04d}{extension}"
                (staging / name).write_bytes(data)
                w, h = image_size(data)
                pages.append({"f": name, "w": w, "h": h})
        if not pages:
            raise ValueError("No images were found in that archive.")
        save_chapter(folder, label, chapter_number(label), "imported file", staging, join_slices(pages))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return folder.name


def import_image(data, title, label, reset):
    extension = sniff_extension(data)
    if not extension:
        raise ValueError("That file isn't a supported image.")
    folder = ensure_manga(title, "imported file")
    chapter = folder / slug(label)
    if reset and chapter.exists():
        shutil.rmtree(chapter)
    chapter.mkdir(exist_ok=True)
    meta = read_json(chapter / "chapter.json") or {
        "title": label, "number": chapter_number(label), "source": "imported file",
        "added": time.time(), "pages": []}
    name = f"{len(meta['pages']) + 1:04d}{extension}"
    (chapter / name).write_bytes(data)
    w, h = image_size(data)
    meta["pages"].append({"f": name, "w": w, "h": h})
    write_json(chapter / "chapter.json", meta)
    return folder.name


# ---------------------------------------------------------------- web server

MIME = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".avif": "image/avif"}


class Handler(BaseHTTPRequestHandler):
    server_version = "MangaReader"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # Other websites can send requests to localhost. The Host check stops DNS rebinding,
    # and the custom header can't be sent cross-site without a CORS approval we never give.
    def host_allowed(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def write_allowed(self):
        return self.host_allowed() and self.headers.get("X-Manga-Reader") == "1"

    def send_body(self, body, content_type, status=200, cache=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", cache or "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.send_body(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json", status)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("That file is too large.")
        return self.rfile.read(length)

    def read_json_body(self):
        value = json.loads(self.read_body() or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def do_GET(self):
        if not self.host_allowed():
            return self.send_json({"error": "forbidden"}, 403)
        path = urllib.parse.urlsplit(self.path).path
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        try:
            if not parts:
                return self.send_body(INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            if parts == ["favicon.ico"]:
                return self.send_body(ICON.read_bytes(), "image/x-icon", cache="private, max-age=3600")
            if parts == ["api", "library"]:
                return self.send_json(library_index())
            if parts == ["api", "jobs"]:
                with jobs_lock:
                    return self.send_json(sorted(jobs.values(), key=lambda j: -j["created"]))
            if parts == ["api", "settings"]:
                return self.send_json({"libraryPath": str(LIBRARY), "defaultPath": str(DEFAULT_LIBRARY),
                                       "warning": LIBRARY_WARNING, "mangaCount": len(manga_folders())})
            if len(parts) == 3 and parts[:2] == ["api", "manga"]:
                folder = manga_folder(parts[2])
                meta = read_json(folder / "manga.json", {}) or {}
                chapters = list_chapters(folder)
                return self.send_json({"id": folder.name, "title": meta.get("title") or folder.name,
                                       "source": meta.get("source", ""), "chapters": chapters, "path": str(folder),
                                       "cover": cover_url(folder, meta, chapters) if chapters else ""})
            if len(parts) == 3 and parts[0] == "library":
                folder = manga_folder(parts[1])
                if parts[2] != (read_json(folder / "manga.json", {}) or {}).get("cover"):
                    raise KeyError(parts[2])
                image = safe_child(folder, parts[2])
                return self.send_body(image.read_bytes(), MIME[image.suffix.lower()], cache="private, max-age=3600")
            if len(parts) == 4 and parts[0] == "library":
                chapter = safe_child(manga_folder(parts[1]), parts[2])
                image = safe_child(chapter, parts[3])
                if image.suffix.lower() not in MIME:
                    raise KeyError(parts[3])
                return self.send_body(image.read_bytes(), MIME[image.suffix.lower()], cache="private, max-age=3600")
        except KeyError:
            pass
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.write_allowed():
            self.close_connection = True
            return self.send_json({"error": "forbidden"}, 403)
        url = urllib.parse.urlsplit(self.path)
        params = dict(urllib.parse.parse_qsl(url.query))
        try:
            if url.path == "/api/analyze":
                return self.send_json(analyze(self.read_json_body().get("url", "")))
            if url.path == "/api/describe":
                body = self.read_json_body()
                number = number_from_url(body.get("url", ""))
                if number is None:
                    number = chapter_number(body.get("title", ""))
                return self.send_json({"title": clean_title(body.get("title", "")),
                                       "number": number, "label": chapter_label(number)})
            if url.path == "/api/download":
                return self.send_json(start_job(self.read_json_body()))
            if url.path == "/api/upload":
                return self.handle_upload(params)
            if url.path == "/api/settings/library":
                body = self.read_json_body()
                return self.send_json(change_library(body.get("path"), bool(body.get("move"))))
            if url.path == "/api/settings/browse":
                return self.send_json({"path": browse_for_folder()})
            if url.path == "/api/settings/open":
                os.startfile(LIBRARY)
                return self.send_json({"ok": True})
        except Blocked as error:
            return self.send_json({"error": str(error), "blocked": True}, 502)
        except FetchError as error:
            return self.send_json({"error": str(error)}, 502)
        except (ValueError, zipfile.BadZipFile) as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json({"error": "not found"}, 404)

    def handle_upload(self, params):
        title = params.get("title", "").strip()
        label = params.get("chapter", "").strip() or "Chapter 1"
        if not title:
            raise ValueError("A title is required.")
        data = self.read_body()
        if data[:2] == b"PK":
            manga_id = import_archive(io.BytesIO(data), title, label)
        else:
            manga_id = import_image(data, title, label, params.get("reset") == "1")
        self.send_json({"mangaId": manga_id})

    def do_DELETE(self):
        if not self.write_allowed():
            return self.send_json({"error": "forbidden"}, 403)
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlsplit(self.path).path.split("/") if p]
        try:
            if len(parts) == 3 and parts[:2] == ["api", "manga"]:
                shutil.rmtree(manga_folder(parts[2]))
                return self.send_json({"ok": True})
            if len(parts) == 5 and parts[:2] == ["api", "manga"] and parts[3] == "chapter":
                folder = manga_folder(parts[2])
                chapter = safe_child(folder, parts[4])
                if not (chapter / "chapter.json").exists():
                    raise KeyError(parts[4])
                shutil.rmtree(chapter)
                if not list_chapters(folder):
                    shutil.rmtree(folder)
                return self.send_json({"ok": True})
        except KeyError:
            pass
        self.send_json({"error": "not found"}, 404)


class AppServer(ThreadingHTTPServer):
    # With this on, Windows lets a second copy of the app bind the same port instead of failing.
    allow_reuse_address = False


def open_window(address):
    """Open the app in its own browserless window. Returns the process to wait on, or None."""
    roots = [os.environ.get(name) for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for relative in WINDOW_BROWSERS:
        for root in filter(None, roots):
            executable = Path(root) / relative
            if executable.exists():
                WINDOW_PROFILE.mkdir(parents=True, exist_ok=True)
                return subprocess.Popen([
                    str(executable), f"--app={address}", f"--user-data-dir={WINDOW_PROFILE}",
                    "--window-size=1280,900", "--no-first-run", "--no-default-browser-check"])
    webbrowser.open(address)
    return None


def window_is_open():
    """Edge keeps a lock file in its profile for exactly as long as any of its windows are open."""
    try:
        with open(WINDOW_PROFILE / "lockfile", "rb"):
            return False
    except FileNotFoundError:
        return False
    except PermissionError:
        return True


def wait_for_window_to_close(window):
    # The process we started can't be trusted to stay: Edge may relaunch itself, or pass
    # the window to an Edge already using this profile, and exit while the window lives on.
    deadline = time.time() + WINDOW_START_SECONDS
    seen = False
    while True:
        time.sleep(1)
        seen = window_is_open() or seen
        if window.poll() is not None and not window_is_open() and (seen or time.time() > deadline):
            return


def remove_interrupted_downloads():
    for leftover in LIBRARY.glob("*/.staging-*"):
        shutil.rmtree(leftover, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    address = f"http://127.0.0.1:{args.port}/"
    load_library_setting()
    if FROZEN:  # a rebuild made while the app was open leaves the previous .exe beside this one
        try:
            Path(sys.executable).with_suffix(".old").unlink(missing_ok=True)
        except OSError:
            pass
    try:
        server = AppServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"Port {args.port} is busy, so Manga Reader is probably already running: {address}")
        if not args.no_browser:
            open_window(address)
        return
    remove_interrupted_downloads()
    threading.Thread(target=job_worker, daemon=True).start()
    threading.Thread(target=fetch_missing_covers, daemon=True).start()
    print(f"Manga Reader is running at {address}")
    window = None if args.no_browser else open_window(address)
    try:
        if window:
            # The app's lifetime is its window: closing it stops everything.
            threading.Thread(target=server.serve_forever, daemon=True).start()
            wait_for_window_to_close(window)
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
