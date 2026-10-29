#!/usr/bin/env python3
"""
Moodle Material Sync
====================
Downloads every lecture / file / folder / assignment attachment from ALL the
courses you are enrolled in on the college Moodle portal, into one folder per
course. Links (e.g. Google Drive) are saved as shortcuts, not downloaded.
Run it again any time: it only downloads what is missing.

    python moodle_sync.py            # sync everything
    python moodle_sync.py --login    # force a fresh login
    python moodle_sync.py --course 1326 1325   # only these course IDs

Nothing is ever uploaded or submitted to the portal. It only reads.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
import time
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

DEFAULT_BASE_URL = "https://learn3.sha.edu.eg/"
APP_DIR = Path(__file__).resolve().parent
SESSION_FILE = APP_DIR / ".session" / "portal-state.json"   # your login. NEVER share it.
DEFAULT_OUT = APP_DIR / "Course Materials"
STATE_NAME = ".sync_state.json"                              # remembers what was downloaded


class SessionExpired(Exception):
    """The portal sent us to the login page."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def clean_name(text: str, fallback: str, maxlen: int = 100, is_file: bool = False) -> str:
    """Make a string safe to use as a file/folder name (Windows + Mac + Linux)."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .") or fallback
    if len(text) > maxlen:
        stem, dot, ext = text.rpartition(".")
        if is_file and dot and 0 < len(ext) <= 8:
            text = stem[: maxlen - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            text = text[:maxlen].rstrip(" .")
    return text


def activity_label(text: str) -> str:
    """Moodle adds hidden words like ' File' or ' Folder' after names. Remove them."""
    return re.sub(r"\s+(File|Folder|Assignment|URL|Page|Forum|Quiz)$", "", text.strip())


def file_key(url: str) -> str:
    """A stable ID for a file URL (ignores ?query and the changing revision number)."""
    path = unquote(urlparse(url).path)
    return re.sub(r"(mod_resource/content/)\d+/", r"\1_/", path)


class PageParser(HTMLParser):
    """Collects <title>, every link (href, text) and embedded file URLs from a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[tuple[str, str]] = []
        self.embeds: list[str] = []
        self._link = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._link = [a["href"], []]
        elif tag == "title":
            self._in_title = True
        elif tag in ("iframe", "embed", "source") and a.get("src"):
            self.embeds.append(a["src"])
        elif tag == "object" and a.get("data"):
            self.embeds.append(a["data"])

    def handle_endtag(self, tag):
        if tag == "a" and self._link:
            text = " ".join("".join(self._link[1]).split())
            self.links.append((self._link[0], text))
            self._link = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._link is not None:
            self._link[1].append(data)
        if self._in_title:
            self.title += data


def parse_page(html: str) -> PageParser:
    parser = PageParser()
    parser.feed(html)
    return parser


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------
def launch_chromium(pw, headless: bool):
    """Use the Chrome or Edge already on this computer; only download a browser if neither exists."""
    for channel in ("chrome", "msedge"):
        try:
            return pw.chromium.launch(channel=channel, headless=headless)
        except Exception:
            pass
    try:
        return pw.chromium.launch(headless=headless)
    except Exception as error:
        if "Executable doesn't exist" not in str(error):
            raise
        print("Chrome/Edge not found. Downloading a browser (one time, ~150 MB)...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        return pw.chromium.launch(headless=headless)


def interactive_login(pw, base_url: str) -> None:
    print("\nA browser window will open. Log in to the portal there.")
    browser = launch_chromium(pw, headless=False)
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.goto(urljoin(base_url, "my/"), timeout=60000)
    input("When you can see your dashboard, come back here and press Enter... ")
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(SESSION_FILE))
    browser.close()
    print("Login saved.")


def new_http(pw):
    """An HTTP client that carries your saved login cookies."""
    return pw.request.new_context(storage_state=str(SESSION_FILE), ignore_https_errors=True)


def session_is_valid(pw, base_url: str) -> bool:
    if not SESSION_FILE.is_file():
        return False
    http = new_http(pw)
    try:
        response = http.get(urljoin(base_url, "my/"), timeout=60000)
        return response.ok and "/login/" not in urlparse(response.url).path
    except Exception:
        return False
    finally:
        http.dispose()


def ensure_login(pw, base_url: str, force: bool) -> None:
    if not force and session_is_valid(pw, base_url):
        return
    interactive_login(pw, base_url)
    if not session_is_valid(pw, base_url):
        sys.exit("Login did not work. Run again and make sure you reach your dashboard.")


# --------------------------------------------------------------------------
# Finding your courses
# --------------------------------------------------------------------------
def discover_courses(pw, base_url: str) -> dict[str, str]:
    """Open the dashboard in a hidden browser and read the course links."""
    browser = launch_chromium(pw, headless=True)
    context = browser.new_context(storage_state=str(SESSION_FILE), ignore_https_errors=True)
    page = context.new_page()
    found: dict[str, str] = {}
    try:
        for path in ("my/courses.php", "my/"):
            page.goto(urljoin(base_url, path), wait_until="domcontentloaded", timeout=60000)
            if "/login/" in urlparse(page.url).path:
                raise SessionExpired()
            try:
                page.wait_for_selector('a[href*="/course/view.php?id="]', timeout=15000)
            except Exception:
                pass
            links = page.locator('a[href*="/course/view.php?id="]')
            for i in range(links.count()):
                href = links.nth(i).get_attribute("href") or ""
                course_id = (parse_qs(urlparse(href).query).get("id") or [""])[0]
                if course_id.isdigit() and course_id != "1":   # id=1 is the site home page
                    text = " ".join(links.nth(i).inner_text().split())
                    found.setdefault(course_id, text)
            if found:
                break
    finally:
        browser.close()
    return found


# --------------------------------------------------------------------------
# The syncer
# --------------------------------------------------------------------------
class Syncer:
    def __init__(self, http, base_url: str, out_dir: Path, force: bool):
        self.http = http
        self.base_url = base_url
        self.host = urlparse(base_url).netloc
        self.out = out_dir
        self.force = force
        self.state_path = out_dir / STATE_NAME
        self.state = {"courses": {}, "files": {}, "resources": {}, "urls": {}}
        if self.state_path.is_file():
            try:
                self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except Exception:
                print("Warning: could not read the sync history; starting a fresh one.")
        self.new = self.existing = self.failed = self.links = 0

    # ---- bookkeeping ------------------------------------------------------
    def save_state(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.state_path)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.out).as_posix()

    # ---- network ----------------------------------------------------------
    def get(self, url: str, follow: bool = True):
        """GET with retries. Raises SessionExpired if we land on the login page."""
        last_error = None
        options = {} if follow else {"max_redirects": 0}
        for attempt in range(3):
            try:
                response = self.http.get(url, timeout=90000, **options)
            except Exception as error:
                last_error = error
                time.sleep(2 * (attempt + 1))
                continue
            if "/login/" in urlparse(response.url).path:
                raise SessionExpired()
            if response.status >= 500:
                last_error = RuntimeError(f"HTTP {response.status}")
                time.sleep(2 * (attempt + 1))
                continue
            return response
        raise RuntimeError(f"could not load {url}: {last_error}")

    # ---- saving files -----------------------------------------------------
    def save_file(self, key: str, url: str, folder: Path, body: bytes | None = None) -> str | None:
        """Save one file unless we already have it. Returns the saved relative path."""
        known = self.state["files"].get(key)
        if known and (self.out / known).is_file() and not self.force:
            self.existing += 1
            return known

        filename = clean_name(unquote(Path(urlparse(url).path).name), "downloaded_file", 120, True)
        destination = folder / filename

        if destination.exists() and not self.force:
            owner = {v: k for k, v in self.state["files"].items()}.get(self.rel(destination))
            if owner in (None, key):
                # Already on disk (maybe you downloaded it by hand). Keep it.
                self.state["files"][key] = self.rel(destination)
                self.existing += 1
                return self.rel(destination)
            # Same name but a different file: keep both.
            stem, suffix = destination.stem, destination.suffix
            n = 2
            while destination.exists():
                destination = folder / f"{stem} ({n}){suffix}"
                n += 1

        if body is None:
            response = self.get(url)
            if not response.ok:
                print(f"    FAILED  {filename}  (HTTP {response.status})")
                self.failed += 1
                return None
            body = response.body()

        if filename.lower().endswith(".pdf") and not body.startswith(b"%PDF-"):
            print(f"    FAILED  {filename}  (portal returned a web page, not a PDF)")
            self.failed += 1
            return None

        folder.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        part.write_bytes(body)
        part.replace(destination)          # a half-downloaded file never counts as "have it"
        self.state["files"][key] = self.rel(destination)
        self.new += 1
        print(f"    NEW     {self.rel(destination)}  ({len(body) / 1024:,.0f} KB)")
        return self.rel(destination)

    # ---- one activity at a time ------------------------------------------
    def do_resource(self, course_id: str, act_id: str, url: str, course_dir: Path) -> None:
        """A single lecture/file. Skipped with NO network call if we already have it."""
        memo = f"{course_id}:{act_id}"
        done = self.state["resources"].get(memo)
        if done and all((self.out / p).is_file() for p in done) and not self.force:
            self.existing += len(done)
            return

        saved = []
        response = self.get(url)
        if not response.ok:
            print(f"    FAILED  resource {act_id}  (HTTP {response.status})")
            self.failed += 1
            return

        if "/pluginfile.php/" in urlparse(response.url).path:
            # The portal sent us straight to the file.
            path = self.save_file(file_key(response.url), response.url, course_dir, response.body())
            saved.append(path)
        else:
            # A web page that embeds / links the file.
            page = parse_page(response.text())
            candidates = [urljoin(response.url, u) for u in page.embeds + [h for h, _ in page.links]]
            candidates = [u for u in candidates if "/pluginfile.php/" in u and "mod_resource/content" in u]
            for file_url in dict.fromkeys(candidates):
                saved.append(self.save_file(file_key(file_url), file_url, course_dir))
        saved = [p for p in saved if p]
        if saved:
            self.state["resources"][memo] = saved

    def do_folder(self, name: str, url: str, course_dir: Path) -> None:
        response = self.get(url)
        if not response.ok:
            print(f"    FAILED  folder '{name}'  (HTTP {response.status})")
            self.failed += 1
            return
        page = parse_page(response.text())
        urls = [urljoin(response.url, h) for h, _ in page.links]
        urls = [u for u in urls if "/pluginfile.php/" in u and "mod_folder/content" in u]
        for file_url in dict.fromkeys(urls):
            # Keep the folder's own sub-folders: .../mod_folder/content/0/Week 1/file.pdf
            inner = unquote(urlparse(file_url).path).split("mod_folder/content/", 1)[1]
            parts = inner.split("/")[1:-1]
            target = course_dir / clean_name(name, "Folder", 60)
            for part in parts:
                target = target / clean_name(part, "folder", 60)
            self.save_file(file_key(file_url), file_url, target)

    def do_assignment(self, url: str, course_dir: Path) -> None:
        """Only the teacher's instruction files, never your own submissions."""
        response = self.get(url)
        if not response.ok:
            return
        page = parse_page(response.text())
        urls = [urljoin(response.url, h) for h, _ in page.links]
        urls = [u for u in urls if "/pluginfile.php/" in u and "mod_assign/introattachment" in u]
        for file_url in dict.fromkeys(urls):
            self.save_file(file_key(file_url), file_url, course_dir / "Assignments")

    # ---- link activities (Moodle "URL" items, e.g. Google Drive) -----------
    def resolve_url(self, url: str) -> str | None:
        """Find where a Moodle 'URL' activity points to."""
        response = self.get(url, follow=False)
        if 300 <= response.status < 400:
            target = urljoin(url, response.headers.get("location", ""))
            if "/login/" in urlparse(target).path:
                raise SessionExpired()
            return target
        if response.ok:
            body = response.text()
            found = re.search(r'urlworkaround.*?href="([^"]+)"', body, re.S)
            if found:
                return urljoin(url, unescape(found.group(1)))
            for src in parse_page(body).embeds:
                src = urljoin(url, src)
                if urlparse(src).netloc != self.host:
                    return src
        return None

    def save_link(self, course_dir: Path, name: str, target: str) -> None:
        """Save a clickable shortcut (.url) into the course's URLs folder."""
        folder = course_dir / "URLs"
        content = f"[InternetShortcut]\nURL={target}\n"
        path = folder / (clean_name(name, "link", 100) + ".url")
        n = 2
        while path.exists():
            if path.read_text(encoding="utf-8", errors="ignore") == content:
                return                                   # already saved
            path = folder / f"{clean_name(name, 'link', 90)} ({n}).url"
            n += 1
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.links += 1
        print(f"    LINK    {self.rel(path)}")

    def write_links_index(self, course_id: str, course_dir: Path) -> None:
        """URLs/Links.md: one readable list of every link in the course."""
        items = [v for k, v in self.state["urls"].items() if k.startswith(course_id + ":")]
        if not items:
            return
        text = "# Links\n\n" + "\n".join(f"- [{i['name']}]({i['url']})" for i in items) + "\n"
        path = course_dir / "URLs" / "Links.md"
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def do_url(self, course_id: str, act_id: str, name: str, url: str, course_dir: Path) -> None:
        memo = f"{course_id}:{act_id}"
        info = self.state["urls"].get(memo)
        if not info:
            target = self.resolve_url(url)
            if not target:
                print(f"    FAILED  link '{name or act_id}'  (could not read where it points)")
                self.failed += 1
                return
            info = self.state["urls"][memo] = {"name": name or f"Link {act_id}", "url": target}

        target = info["url"]
        if urlparse(target).netloc == self.host and "/pluginfile.php/" in target:
            self.save_file(file_key(target), target, course_dir)    # link to a file on Moodle itself
            return
        self.save_link(course_dir, info["name"], target)      # we only save the link; you open/download it yourself

    # ---- one course -------------------------------------------------------
    def course_folder(self, course_id: str, title: str) -> Path:
        info = self.state["courses"].get(course_id)
        if not info:
            folder = clean_name(title, f"Course {course_id}", 60)
            taken = {c["folder"] for c in self.state["courses"].values()}
            if folder in taken:
                folder = f"{folder} ({course_id})"
            info = self.state["courses"][course_id] = {"folder": folder, "name": title}
        return self.out / info["folder"]

    def sync_course(self, course_id: str, hint: str = "") -> None:
        course_url = urljoin(self.base_url, f"course/view.php?id={course_id}")
        response = self.get(course_url)
        if not response.ok:
            print(f"\nCourse {course_id}: could not open (HTTP {response.status}). Skipped.")
            self.failed += 1
            return

        page = parse_page(response.text())
        title = re.sub(r"^Course:\s*", "", " ".join(page.title.split())).strip() or hint
        course_dir = self.course_folder(course_id, title)
        print(f"\n=== {title or course_id}  ->  {course_dir.name}/")

        activities, seen = [], set()
        for href, text in page.links:
            url = urljoin(response.url, href)
            parsed = urlparse(url)
            if parsed.netloc != self.host:
                continue
            act_id = (parse_qs(parsed.query).get("id") or [""])[0]
            for kind in ("resource", "folder", "assign", "url"):
                if f"/mod/{kind}/view.php" in parsed.path and act_id and (kind, act_id) not in seen:
                    seen.add((kind, act_id))
                    activities.append((kind, act_id, activity_label(text), url))

        if not activities:
            print("    (no files found - is this course empty, or are you not enrolled?)")
        before, links_before = self.new, self.links
        for kind, act_id, name, url in activities:
            try:
                if kind == "resource":
                    self.do_resource(course_id, act_id, url, course_dir)
                elif kind == "folder":
                    self.do_folder(name, url, course_dir)
                elif kind == "url":
                    self.do_url(course_id, act_id, name, url, course_dir)
                else:
                    self.do_assignment(url, course_dir)
            except SessionExpired:
                raise
            except Exception as error:
                print(f"    FAILED  {name or act_id}: {error}")
                self.failed += 1
            self.save_state()
        self.write_links_index(course_id, course_dir)
        if self.new == before and self.links == links_before:
            print("    Up to date.")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def load_playwright():
    """Import Playwright. If it is missing, offer to install it and carry on."""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        pass

    print("\nPlaywright is not installed (the tool needs it to log in and read the portal).")
    if sys.stdin and sys.stdin.isatty():
        answer = input("Install it now? [Y/n] ").strip().lower()
    else:
        answer = "y"
    if answer in ("n", "no"):
        sys.exit("Okay. Install it yourself with:  pip install playwright   then run this again.")

    print("Installing Playwright...\n")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "playwright>=1.50.0"])
    if result.returncode != 0:
        sys.exit("\nThe install failed. Check your internet, then try:  pip install playwright")
    importlib.invalidate_caches()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("\nInstalled, but Python still cannot find it. Close this window and run again.")
    print("\nPlaywright installed. Continuing...\n")
    return sync_playwright


def run(args) -> None:
    sync_playwright = load_playwright()

    base_url = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
    out_dir = Path(args.out).expanduser().resolve()

    with sync_playwright() as pw:
        ensure_login(pw, base_url, args.login)

        for attempt in range(2):
            try:
                if args.course:
                    courses = {c: "" for c in args.course}
                else:
                    print("Looking for your courses...")
                    courses = discover_courses(pw, base_url)
                    if not courses:
                        sys.exit(
                            "Could not find your courses on the dashboard.\n"
                            "Pass the course IDs yourself, e.g.:  python moodle_sync.py --course 1326 1325\n"
                            "(the ID is the number at the end of the course page address: .../course/view.php?id=1326)"
                        )
                print(f"Found {len(courses)} course(s). Saving into: {out_dir}")

                http = new_http(pw)
                syncer = Syncer(http, base_url, out_dir, args.force)
                try:
                    for course_id, hint in courses.items():
                        syncer.sync_course(course_id, hint)
                finally:
                    syncer.save_state()
                    http.dispose()

                print("\n" + "=" * 50)
                print(f"New files downloaded : {syncer.new}")
                print(f"Already had          : {syncer.existing}")
                print(f"Link shortcuts saved : {syncer.links}")
                print(f"Problems             : {syncer.failed}")
                print(f"Folder               : {out_dir}")
                return
            except SessionExpired:
                if attempt == 1:
                    sys.exit("Your login keeps expiring. Run again with --login.")
                print("\nYour login expired. Please log in again.")
                interactive_login(pw, base_url)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):        # avoid crashes on Arabic file names in Windows consoles
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Download all course materials from Moodle.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to save (default: 'Course Materials' next to this script)")
    parser.add_argument("--course", nargs="+", metavar="ID", help="only sync these course IDs")
    parser.add_argument("--login", action="store_true", help="force a fresh login")
    parser.add_argument("--force", action="store_true", help="re-download everything, overwriting files")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="portal address")
    parser.add_argument("--no-pause", action="store_true", help="do not wait for Enter at the end")
    args = parser.parse_args()

    try:
        run(args)
    except KeyboardInterrupt:
        print("\nStopped. Run again any time; it continues where it left off.")
    if not args.no_pause and sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close... ")


if __name__ == "__main__":
    main()
