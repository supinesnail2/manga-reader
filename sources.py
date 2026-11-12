"""Browsing and searching comic and manga sites.

Each source turns a site's search or list page into results, and a result's page into the
links it offers. Fetching stays in manga_reader.py; everything here works on HTML text,
so a site changing its layout only means changing its parser below.
"""
import base64
import html
import re
import urllib.parse
from html.parser import HTMLParser

LIST_PAGE_SIZE = 48
ARCHIVE_EXTENSIONS = (".cbz", ".zip", ".cbr", ".rar")


# ---------------------------------------------------------------- a small DOM

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
             "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, attrs or {}, [], parent

    def get(self, name, default=""):
        return self.attrs.get(name) or default

    @property
    def classes(self):
        return self.get("class").split()

    def has_class(self, name):
        return name in self.classes

    def iter(self):
        """Every element below this one, in document order."""
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter()

    def find_all(self, tag=None, cls=None, cls_prefix=None):
        for node in self.iter():
            if tag and node.tag != tag:
                continue
            if cls and not node.has_class(cls):
                continue
            if cls_prefix and not any(c.startswith(cls_prefix) for c in node.classes):
                continue
            yield node

    def find(self, tag=None, cls=None, cls_prefix=None):
        return next(self.find_all(tag, cls, cls_prefix), None)

    def ancestor(self, tag=None, cls=None):
        node = self.parent
        while node is not None:
            if (not tag or node.tag == tag) and (not cls or node.has_class(cls)):
                return node
            node = node.parent
        return None

    @property
    def text(self):
        parts = []

        def walk(node):
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in ("script", "style"):
                    walk(child)
                    if child.tag in ("br", "p", "div", "li", "td", "tr", "h1", "h2", "h3", "h4"):
                        parts.append(" ")
        walk(self)
        return " ".join(" ".join(parts).split())


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.current))

    def handle_endtag(self, tag):
        # Close up to the matching tag; a stray end tag with nothing open to match is ignored.
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse(text):
    builder = TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


# ---------------------------------------------------------------- helpers

def absolute(base, href):
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "data:")):
        return ""
    return urllib.parse.urljoin(base, href)


def host_of(url):
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def image_of(node, base):
    if node is None:
        return ""
    for name in ("data-src", "data-lazy-src", "src"):
        value = node.get(name)
        if value and not value.startswith("data:"):
            return absolute(base, value)
    return ""


def meta_content(root, name):
    for node in root.find_all("meta"):
        if name in (node.get("property"), node.get("name")):
            return node.get("content")
    return ""


def page_title(root):
    title = meta_content(root, "og:title")
    if not title:
        node = root.find("title")
        title = node.text if node else ""
    return re.split(r"\s+[|»–—]\s+|\s+-\s+", html.unescape(title).strip())[0].strip()


NUMBER_RE = re.compile(r"(?:#|\bvol(?:ume)?\.?\s*|\bissue\s*|\bchapter\s*|\bch\.?\s*|\bv)0*(\d+(?:\.\d+)?)", re.I)
SERIES_TAIL_RE = re.compile(
    r"\s*(?:[-–—:]\s*)?(?:#\s*\d.*|\bvol(?:ume)?\.?\s*\d.*|\bch(?:apter)?\.?\s*\d.*|\bv\d+\b.*|\(\d{4}\).*|\[[^\]]*\].*)$", re.I)


ISSUE_RE = re.compile(r"#\s*0*(\d+(?:\.\d+)?)")


def number_in(text):
    """Comics count issues (#15, even inside Vol. 4); manga counts volumes or chapters."""
    match = ISSUE_RE.search(text or "") or NUMBER_RE.search(text or "")
    return float(match.group(1)) if match else None


def series_name(title):
    """'Batman Of Two Worlds #1 (2026)' -> 'Batman Of Two Worlds'."""
    title = re.sub(r"^\s*\[[^\]]*\]\s*", "", title or "")  # "[MANGA][CBZ] Name"
    title = re.sub(r"^\s*\[[^\]]*\]\s*", "", title)
    if "#" in title:  # "Batman Vol. 4 #15": the volume belongs to the series
        cut = re.sub(r"\s*\(\d{4}\)\s*$", "", title.split("#")[0]).strip(" -–—:")
    else:
        cut = SERIES_TAIL_RE.sub("", title).strip(" -–—:")
    return cut or title.strip()


def is_archive_url(url):
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).lower()
    return path.endswith(ARCHIVE_EXTENSIONS) and "mediafire.com" not in url and "datanodes" not in url


def link_kind(url, text=""):
    """How the app can use a link: 'file' it can download itself, 'page' it can read with the
    normal link reader, or 'external' to open in the person's own browser."""
    host = host_of(url)
    if re.search(r"read\s*online", text, re.I):
        return "page"
    if host == "getcomics.org" and "/dls/" in url:
        return "file"
    if host.endswith("pixeldrain.com") and re.search(r"/(u|api/file)/\w+", url):
        return "file"
    if host.endswith("mediafire.com") and "/file/" in url:
        return "file"
    if is_archive_url(url):
        return "file"
    return "external"


def friendly_host(url):
    host = host_of(url)
    return host.split(":")[0] if host else ""


def section_labels(container, keep):
    """Walk a page in order and remember the last heading or table cell before each kept link,
    so a button reading only 'DOWNLOAD' can be shown as 'Volume 3 · DOWNLOAD'."""
    label, found = "", []
    for node in container.iter():
        if node.tag in ("h1", "h2", "h3", "h4", "h5", "strong", "b", "td", "th") and not node.find("a"):
            text = node.text
            if 0 < len(text) <= 120 and not re.fullmatch(r"[#:|\s\d.]*", text) or re.fullmatch(r"(vol|volume|chapter|ch)\.?\s*\d+.*", text, re.I):
                label = text
        if node.tag == "a" and keep(node):
            found.append((node, label))
    return found


# ---------------------------------------------------------------- sources

class Source:
    id = ""
    name = ""
    home = ""
    kind = "search"  # "search": the site searches; "list": one big list filtered here; "external"
    note = ""

    def describe(self):
        return {"id": self.id, "name": self.name, "home": self.home, "kind": self.kind, "note": self.note}


class GetComics(Source):
    id, name, home = "getcomics", "GetComics", "https://getcomics.org/"
    note = "Western comics. Leave the box empty to see the latest posts."

    def search_url(self, query, page):
        prefix = f"page/{page}/" if page > 1 else ""
        return f"{self.home}{prefix}?s={urllib.parse.quote_plus(query)}" if query else f"{self.home}{prefix}"

    def parse_results(self, text, base, page):
        root = parse(text)
        results = []
        for article in root.find_all("article"):
            heading = article.find(cls="post-title")
            link = heading.find("a") if heading else None
            if not link:
                continue
            info = article.find(cls="post-info")
            details = []
            if info:
                details = [p.text for p in info.find_all("p") if re.search(r"Year|Size", p.text)]
            results.append({"title": link.text, "url": absolute(base, link.get("href")),
                            "cover": image_of(article.find("img"), base),
                            "info": details[0] if details else ""})
        has_next = any(a.has_class("pagination-older") or re.search(rf"/page/{page + 1}/", a.get("href"))
                       for a in root.find_all("a"))
        return results, has_next

    def parse_detail(self, text, base):
        root = parse(text)
        content = root.find(cls="post-contents") or root.find("article") or root
        links = []
        for node, label in section_labels(content, lambda a: any(c.startswith("aio-") for c in a.classes)):
            url = absolute(base, node.get("href"))
            if url:
                button = node.get("title") or node.text
                links.append({"label": button.title(), "section": label, "url": url})
        return {"title": page_title(root), "cover": meta_content(root, "og:image"), "links": links}


class ComicsAll(Source):
    id, name, home = "comics-all", "Comics-All", "https://comics-all.com/"
    note = "Western comics. Downloads are on file hosts that open in your browser."
    per_page = 150

    def search_url(self, query, page):
        if not query:
            return f"{self.home}page/{page}/" if page > 1 else self.home
        params = {"do": "search", "subaction": "search", "story": query}
        if page > 1:
            params.update(search_start=page, result_from=(page - 1) * self.per_page + 1)
        return f"{self.home}index.php?{urllib.parse.urlencode(params)}"

    def parse_results(self, text, base, page):
        root = parse(text)
        results = []
        for box in root.find_all(cls="vidos"):
            link = box.find("a", cls="preview-title") or box.find("a")
            if not link:
                continue
            meta = box.find(cls="preview-time")
            results.append({"title": link.text, "url": absolute(base, link.get("href")),
                            "cover": image_of(box.find("img"), base),
                            "info": " · ".join(meta.text.split("  ")) if meta else ""})
        counter = root.find(cls="berrors")
        if counter:
            match = re.search(r"Found\s+(\d+).*?-\s*(\d+)", counter.text)
            has_next = bool(match) and int(match.group(2)) < int(match.group(1))
        else:
            has_next = any(re.search(rf"/page/{page + 1}/", a.get("href")) for a in root.find_all("a"))
        return results, has_next

    def parse_detail(self, text, base):
        root = parse(text)
        home = host_of(self.home)
        content = root.find(cls="full-text") or root.find(cls="fstory") or root.find(cls="full") or root

        def keep(a):
            url = absolute(base, a.get("href"))
            return (url.startswith("http") and host_of(url) != home and a.ancestor(cls="vidos") is None
                    and (a.has_class("button") or re.search(r"download", a.text, re.I)))

        links = [{"label": node.text.title() or "Download", "section": label, "url": absolute(base, node.get("href"))}
                 for node, label in section_labels(content, keep)]
        return {"title": page_title(root), "cover": meta_content(root, "og:image") or image_of(content.find("img"), base),
                "links": links}


class ListSource(Source):
    """Sites that publish one long A to Z list instead of a search box."""
    kind = "list"

    def parse_list(self, text, base):
        raise NotImplementedError

    def search_url(self, query, page):
        return self.home

    def parse_detail_links(self, root, base):
        raise NotImplementedError

    def parse_detail(self, text, base):
        root = parse(text)
        return {"title": page_title(root), "cover": meta_content(root, "og:image"),
                "links": self.parse_detail_links(root, base)}


class ComicsCodes(ListSource):
    note = "Volumes on MediaFire download straight into the app. MEGA links open in your browser."

    def __init__(self, id, name, home):
        self.id, self.name, self.home = id, name, home

    def parse_list(self, text, base):
        root = parse(text)
        items = []
        for ul in root.find_all("ul", cls="lcp_catlist"):
            for a in ul.find_all("a"):
                url = absolute(base, a.get("href"))
                if url and a.text:
                    items.append({"title": a.text, "url": url, "cover": "", "info": ""})
        return items

    def parse_detail_links(self, root, base):
        links = []
        for table in root.find_all("table"):
            for row in table.find_all("tr"):
                cells = [c for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]
                if not cells:
                    continue
                label = cells[0].text
                for a in row.find_all("a"):
                    url = absolute(base, a.get("href"))
                    if url.startswith("http"):
                        links.append({"label": a.text.title() or friendly_host(url), "section": label, "url": url})
        return links


class JNovels(ListSource):
    id, name, home = "jnovels", "JNovels manga", "https://jnovels.com/manga-cbz-cbr-pdf-jp/"
    note = "Manga volumes. Its download links go through ad pages, so they open in your browser."

    def parse_list(self, text, base):
        root = parse(text)
        content = root.find("article") or root
        home = host_of(self.home)
        items = []
        for ol in content.find_all("ol"):
            for li in ol.find_all("li"):
                a = li.find("a")
                url = absolute(base, a.get("href")) if a else ""
                if url and host_of(url) == home and a.text:
                    items.append({"title": a.text, "url": url, "cover": "", "info": ""})
        return items

    def parse_detail_links(self, root, base):
        content = root.find(cls="entry-content") or root.find("article") or root
        home = host_of(self.home)
        links = []
        for a in content.find_all("a"):
            url = absolute(base, a.get("href"))
            text = a.text
            if not url.startswith("http") or host_of(url) == home or "adjust.com" in url or not text:
                continue
            if a.ancestor(cls="sharedaddy") is not None or a.ancestor("footer") is not None:
                continue
            if re.search(r"vol|chapter|ch\.|download|part|\d", text, re.I):
                links.append({"label": text.title(), "section": "", "url": url})
        return links


class ComicBookPlus(Source):
    id, name, home = "comicbookplus", "Comic Book Plus", "https://comicbookplus.com/"
    kind = "external"
    note = ("Golden Age, public domain comics. The site checks for a real browser, so search opens there. "
            "Use Send pages from your browser on a reader page, or import the files you download.")

    def search_url(self, query, page):
        if not query:
            return self.home
        return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(f"site:comicbookplus.com {query}")


SOURCES = {s.id: s for s in (
    GetComics(),
    ComicsAll(),
    ComicsCodes("comics-codes-manga", "ComicsCodes manga", "https://comics.codes/all-manga-list/"),
    ComicsCodes("comics-codes-comics", "ComicsCodes comics", "https://comics.codes/all-comics-list/"),
    JNovels(),
    ComicBookPlus(),
)}


def filter_list(items, query, page):
    words = query.lower().split()
    matches = [i for i in items if all(w in i["title"].lower() for w in words)] if words else items
    start = (page - 1) * LIST_PAGE_SIZE
    return matches[start:start + LIST_PAGE_SIZE], start + LIST_PAGE_SIZE < len(matches), len(matches)


def finish_detail(detail, result_title=""):
    """Add what the app needs to show a result's links: how each can be used, and suggested names."""
    title = detail.get("title") or result_title
    detail["title"] = title
    detail["series"] = series_name(result_title or title)
    seen = set()
    links = []
    for link in detail.get("links", []):
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        link["kind"] = link_kind(link["url"], link["label"])
        link["host"] = friendly_host(link["url"])
        guess = link.get("section") if number_in(link.get("section", "")) is not None else ""
        guess = guess or (link["label"] if number_in(link["label"]) is not None else "")
        guess = guess or (result_title or title)[len(detail["series"]):].strip(" -–—:") or result_title or title
        if guess.lower().startswith(detail["series"].lower()) and len(guess) > len(detail["series"]):
            guess = guess[len(detail["series"]):].strip(" -–—:")
        link["chapter"] = guess
        link["number"] = number_in(guess)
        links.append(link)
    detail["links"] = links
    return detail


# ---------------------------------------------------------------- file hosts

def mediafire_direct(text):
    """A MediaFire file page holds the real download link in its download button."""
    root = parse(text)
    for a in root.find_all("a"):
        if a.get("id") == "downloadButton" or a.has_class("input") and "download" in a.get("aria-label").lower():
            scrambled = a.get("data-scrambled-url")
            if scrambled:
                try:
                    return base64.b64decode(scrambled).decode("utf-8")
                except ValueError:
                    pass
            href = a.get("href")
            if href.startswith("http"):
                return href
    match = re.search(r"https?://download\d*\.mediafire\.com/[^\"'\s<>]+", text)
    return match.group(0) if match else ""


def pixeldrain_direct(url):
    match = re.search(r"pixeldrain\.com/(?:u|api/file)/(\w+)", url)
    return f"https://pixeldrain.com/api/file/{match.group(1)}?download" if match else ""
