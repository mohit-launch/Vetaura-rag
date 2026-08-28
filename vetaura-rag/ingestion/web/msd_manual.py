from __future__ import annotations

import csv
import gzip
import hashlib
import json
import logging
import random
import re
import time
import xml.etree.ElementTree as ET

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
import trafilatura

from bs4 import BeautifulSoup
from markdownify import markdownify


# ============================================================
# VETAURA - MSD VETERINARY MANUAL KNOWLEDGE COLLECTOR
# ============================================================
#
# Public-content collector for MSD Veterinary Manual.
#
# Features:
# - robots.txt support
# - sitemap discovery
# - sitemap index recursion
# - namespace-independent XML parsing
# - .xml.gz support
# - Retry-After support
# - 429 / 5xx retry with exponential backoff
# - persistent session
# - resume support
# - raw HTML storage
# - Markdown extraction
# - metadata JSONL
# - URL CSV
# - content deduplication
#
# IMPORTANT:
# This does not attempt to bypass authentication, CAPTCHA,
# access controls, or bot protection.
#
# ============================================================


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[2]

OUTPUT_DIR = BASE_DIR / "knowledge_base" / "raw" / "msd"

HTML_DIR = OUTPUT_DIR / "html"
MARKDOWN_DIR = OUTPUT_DIR / "markdown"
METADATA_DIR = OUTPUT_DIR / "metadata"
LOG_DIR = OUTPUT_DIR / "logs"

STATE_FILE = METADATA_DIR / "crawl_state.json"
DOCUMENTS_FILE = METADATA_DIR / "documents.jsonl"
ERRORS_FILE = METADATA_DIR / "errors.jsonl"
URLS_CSV = METADATA_DIR / "urls.csv"


# ============================================================
# MSD URLS
# ============================================================

ROOT_URL = "https://www.msdvetmanual.com"

SEED_URLS = [
    f"{ROOT_URL}/dog-owners",
    f"{ROOT_URL}/cat-owners",
    f"{ROOT_URL}/veterinary",
]


# ============================================================
# SITEMAPS
# ============================================================

SITEMAP_CANDIDATES = [
    f"{ROOT_URL}/sitemap.xml",
    f"{ROOT_URL}/sitemap_index.xml",
    f"{ROOT_URL}/sitemaps.xml",
    f"{ROOT_URL}/robots.txt",
]


# ============================================================
# DOMAIN SETTINGS
# ============================================================

ALLOWED_DOMAINS = {
    "www.msdvetmanual.com",
    "msdvetmanual.com",
}


# ============================================================
# CRAWL SETTINGS
# ============================================================

MAX_PAGES = 15000

BASE_DELAY_SECONDS = 2.0

MIN_DELAY_SECONDS = 2.0

MAX_BACKOFF_SECONDS = 300

REQUEST_TIMEOUT = 40

MAX_RETRIES = 5

SAVE_EVERY = 25


# ============================================================
# RELEVANT CONTENT
# ============================================================

RELEVANT_PREFIXES = (
    "/dog-owners",
    "/cat-owners",
    "/veterinary",
)


# ============================================================
# URL CLEANUP
# ============================================================

TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}


BLOCKED_PATHS = (
    "/login",
    "/logout",
    "/account",
    "/search",
    "/cart",
    "/checkout",
)


# ============================================================
# LOGGING
# ============================================================

def setup_logging():

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger = logging.getLogger("MSD_SCRAPER")

    logger.setLevel(logging.INFO)

    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    file_handler = logging.FileHandler(
        LOG_DIR / "msd_scraper.log",
        encoding="utf-8",
    )

    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()

    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


logger = setup_logging()


# ============================================================
# DIRECTORIES
# ============================================================

def create_directories():

    for directory in [
        OUTPUT_DIR,
        HTML_DIR,
        MARKDOWN_DIR,
        METADATA_DIR,
        LOG_DIR,
    ]:
        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(url: str) -> str:

    parsed = urlparse(url)

    clean_query = []

    for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True,
    ):
        if key.lower() not in TRACKING_PARAMETERS:
            clean_query.append((key, value))

    path = parsed.path.rstrip("/")

    if not path:
        path = "/"

    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        query=urlencode(clean_query),
        fragment="",
    )

    return urlunparse(cleaned)


def is_allowed_domain(url: str) -> bool:

    try:
        return urlparse(url).netloc.lower() in ALLOWED_DOMAINS
    except Exception:
        return False


def is_relevant_url(url: str) -> bool:

    if not is_allowed_domain(url):
        return False

    path = urlparse(url).path.lower()

    if any(blocked in path for blocked in BLOCKED_PATHS):
        return False

    return path.startswith(RELEVANT_PREFIXES)


def url_hash(value: str) -> str:

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:16]


def content_hash(content: bytes) -> str:

    return hashlib.sha256(
        content
    ).hexdigest()


def safe_filename(value: str) -> str:

    value = re.sub(
        r"^https?://",
        "",
        value,
    )

    value = re.sub(
        r"[^\w\-.]+",
        "_",
        value,
    )

    value = value.strip("._")

    return value[:180]


# ============================================================
# SESSION
# ============================================================

def create_session() -> requests.Session:

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "VetauraResearchBot/1.0 "
                "(educational veterinary knowledge collection; "
                "contact: research@vetaura.in)"
            ),
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
    )

    return session


# ============================================================
# RATE LIMITER
# ============================================================

class RateLimiter:

    def __init__(self, delay: float):

        self.delay = delay
        self.last_request = 0.0

    def wait(self):

        elapsed = (
            time.monotonic()
            - self.last_request
        )

        wait_time = self.delay - elapsed

        if wait_time > 0:

            time.sleep(wait_time)

        self.last_request = time.monotonic()


# ============================================================
# CRAWL STATE
# ============================================================

@dataclass
class CrawlState:

    visited_urls: set = field(default_factory=set)

    discovered_urls: set = field(default_factory=set)

    successful_urls: set = field(default_factory=set)

    failed_urls: set = field(default_factory=set)

    restricted_urls: set = field(default_factory=set)

    content_hashes: set = field(default_factory=set)

    def load(self):

        if not STATE_FILE.exists():
            return

        try:

            data = json.loads(
                STATE_FILE.read_text(
                    encoding="utf-8"
                )
            )

            self.visited_urls = set(
                data.get("visited_urls", [])
            )

            self.discovered_urls = set(
                data.get("discovered_urls", [])
            )

            self.successful_urls = set(
                data.get("successful_urls", [])
            )

            self.failed_urls = set(
                data.get("failed_urls", [])
            )

            self.restricted_urls = set(
                data.get("restricted_urls", [])
            )

            self.content_hashes = set(
                data.get("content_hashes", [])
            )

            logger.info(
                "STATE LOADED | "
                f"Successful: {len(self.successful_urls)} | "
                f"Visited: {len(self.visited_urls)}"
            )

        except Exception as error:

            logger.warning(
                f"Could not load state: {error}"
            )

    def save(self):

        data = {
            "visited_urls": sorted(self.visited_urls),
            "discovered_urls": sorted(self.discovered_urls),
            "successful_urls": sorted(self.successful_urls),
            "failed_urls": sorted(self.failed_urls),
            "restricted_urls": sorted(self.restricted_urls),
            "content_hashes": sorted(self.content_hashes),
        }

        STATE_FILE.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


# ============================================================
# JSONL
# ============================================================

def append_jsonl(path: Path, data: dict):

    with path.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            json.dumps(
                data,
                ensure_ascii=False,
            )
        )

        file.write("\n")


# ============================================================
# ROBOTS.TXT
# ============================================================

def load_robots(session: requests.Session):

    robots_url = f"{ROOT_URL}/robots.txt"

    response = session.get(
        robots_url,
        timeout=REQUEST_TIMEOUT,
    )

    logger.info(
        f"ROBOTS | {response.status_code} | {robots_url}"
    )

    parser = RobotFileParser()

    parser.set_url(robots_url)

    if response.status_code == 200:

        parser.parse(
            response.text.splitlines()
        )

        logger.info(
            "robots.txt loaded successfully"
        )

    else:

        raise RuntimeError(
            f"Could not load robots.txt: "
            f"{response.status_code}"
        )

    return parser, response.text


# ============================================================
# RETRY-AFTER
# ============================================================

def get_retry_after_seconds(
    response: requests.Response,
) -> Optional[float]:

    value = response.headers.get("Retry-After")

    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        return None


# ============================================================
# FETCH
# ============================================================

def fetch_url(
    session: requests.Session,
    limiter: RateLimiter,
    robots: RobotFileParser,
    url: str,
):

    if not robots.can_fetch(
        "VetauraResearchBot",
        url,
    ):
        logger.info(
            f"ROBOTS DISALLOWED | {url}"
        )

        return {
            "status": "robots_disallowed",
            "url": url,
        }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            limiter.wait()

            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            final_url = normalize_url(
                response.url
            )

            logger.info(
                f"REQUEST | "
                f"{response.status_code} | "
                f"attempt={attempt} | "
                f"{final_url}"
            )

            # ----------------------------
            # SUCCESS
            # ----------------------------

            if response.status_code == 200:

                return {
                    "status": "success",
                    "url": final_url,
                    "content": response.content,
                    "content_type": response.headers.get(
                        "Content-Type",
                        "",
                    ),
                }

            # ----------------------------
            # ACCESS RESTRICTION
            # ----------------------------

            if response.status_code in (
                401,
                403,
            ):

                return {
                    "status": "access_restricted",
                    "url": final_url,
                    "status_code": response.status_code,
                }

            # ----------------------------
            # RETRYABLE ERRORS
            # ----------------------------

            if response.status_code in (
                429,
                500,
                502,
                503,
                504,
            ):

                if attempt < MAX_RETRIES:

                    retry_after = (
                        get_retry_after_seconds(
                            response
                        )
                    )

                    if retry_after is None:

                        retry_after = min(
                            BASE_DELAY_SECONDS
                            * (2 ** (attempt - 1))
                            + random.uniform(0, 2),

                            MAX_BACKOFF_SECONDS,
                        )

                    logger.warning(
                        f"TEMPORARY ERROR | "
                        f"{response.status_code} | "
                        f"Waiting "
                        f"{retry_after:.1f}s "
                        f"before retry"
                    )

                    time.sleep(retry_after)

                    continue

                return {
                    "status": "temporary_failure",
                    "url": final_url,
                    "status_code": response.status_code,
                }

            # ----------------------------
            # OTHER HTTP ERROR
            # ----------------------------

            return {
                "status": "http_error",
                "url": final_url,
                "status_code": response.status_code,
            }

        except requests.RequestException as error:

            logger.warning(
                f"REQUEST EXCEPTION | "
                f"attempt={attempt} | "
                f"{url} | "
                f"{error}"
            )

            if attempt < MAX_RETRIES:

                backoff = min(
                    BASE_DELAY_SECONDS
                    * (2 ** (attempt - 1))
                    + random.uniform(0, 2),

                    MAX_BACKOFF_SECONDS,
                )

                time.sleep(backoff)

    return {
        "status": "failed",
        "url": url,
    }


# ============================================================
# SITEMAP PARSER
# ============================================================

def parse_sitemap_content(
    content: bytes,
    source_url: str,
) -> tuple[list[str], list[str]]:

    urls = []
    nested_sitemaps = []

    try:

        if source_url.endswith(".gz"):

            content = gzip.decompress(
                content
            )

        root = ET.fromstring(
            content
        )

        root_tag = root.tag.lower()

        for element in root.iter():

            if not element.tag.lower().endswith("loc"):
                continue

            if not element.text:
                continue

            value = normalize_url(
                element.text.strip()
            )

            # sitemap index
            if "sitemapindex" in root_tag:

                nested_sitemaps.append(
                    value
                )

            # normal urlset
            elif "urlset" in root_tag:

                urls.append(
                    value
                )

        return urls, nested_sitemaps

    except Exception as error:

        logger.warning(
            f"SITEMAP PARSE ERROR | "
            f"{source_url} | "
            f"{error}"
        )

        return [], []


# ============================================================
# SITEMAP DISCOVERY
# ============================================================

def discover_sitemap_urls(
    session,
    limiter,
    robots,
    robots_text,
):

    sitemap_urls = set(
        SITEMAP_CANDIDATES[:-1]
    )

    # Discover Sitemap: lines from robots.txt
    for line in robots_text.splitlines():

        if line.lower().startswith(
            "sitemap:"
        ):

            sitemap_url = (
                line.split(
                    ":",
                    1,
                )[1]
                .strip()
            )

            if sitemap_url:
                sitemap_urls.add(
                    normalize_url(
                        sitemap_url
                    )
                )

    logger.info(
        f"SITEMAPS DISCOVERED: "
        f"{len(sitemap_urls)}"
    )

    queue = deque(sitemap_urls)

    processed_sitemaps = set()

    discovered_content_urls = set()

    while queue:

        sitemap_url = queue.popleft()

        if sitemap_url in processed_sitemaps:
            continue

        processed_sitemaps.add(
            sitemap_url
        )

        logger.info(
            f"SITEMAP PROCESSING | "
            f"{sitemap_url}"
        )

        # Sitemap requests still use normal fetch,
        # but only if robots allows them.
        result = fetch_url(
            session,
            limiter,
            robots,
            sitemap_url,
        )

        if result["status"] != "success":

            logger.warning(
                f"SITEMAP FAILED | "
                f"{sitemap_url} | "
                f"{result['status']}"
            )

            continue

        urls, nested_sitemaps = (
            parse_sitemap_content(
                result["content"],
                sitemap_url,
            )
        )

        for nested in nested_sitemaps:

            if nested not in processed_sitemaps:

                queue.append(
                    nested
                )

        for url in urls:

            if is_relevant_url(url):

                discovered_content_urls.add(
                    url
                )

    logger.info(
        f"SITEMAP CONTENT URLS FOUND: "
        f"{len(discovered_content_urls)}"
    )

    return discovered_content_urls


# ============================================================
# LINK EXTRACTION
# ============================================================

def extract_links(
    html: str,
    current_url: str,
) -> set[str]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    links = set()

    for tag in soup.find_all(
        "a",
        href=True,
    ):

        absolute = urljoin(
            current_url,
            tag["href"],
        )

        normalized = normalize_url(
            absolute
        )

        if is_relevant_url(
            normalized
        ):
            links.add(
                normalized
            )

    return links


# ============================================================
# CONTENT EXTRACTION
# ============================================================

def extract_title(
    soup: BeautifulSoup,
) -> str:

    h1 = soup.find("h1")

    if h1:

        title = h1.get_text(
            " ",
            strip=True,
        )

        if title:
            return title

    if (
        soup.title
        and soup.title.string
    ):

        return soup.title.string.strip()

    return "MSD Veterinary Manual"


def extract_markdown(
    html: str,
) -> str:

    try:

        content = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_comments=False,
            favor_precision=True,
        )

        if content and len(content.strip()) > 150:

            return content.strip()

    except Exception as error:

        logger.warning(
            f"TRAFILATURA ERROR | {error}"
        )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "header",
            "footer",
            "aside",
            "iframe",
        ]
    ):

        tag.decompose()

    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.body
    )

    if not main_content:
        return ""

    markdown = markdownify(
        str(main_content),
        heading_style="ATX",
    )

    markdown = re.sub(
        r"\n{3,}",
        "\n\n",
        markdown,
    )

    return markdown.strip()


# ============================================================
# SAVE HTML
# ============================================================

def save_html(
    url: str,
    html: str,
) -> Path:

    filename = (
        f"{safe_filename(url)}"
        f"_{url_hash(url)}.html"
    )

    path = HTML_DIR / filename

    path.write_text(
        html,
        encoding="utf-8",
    )

    return path


# ============================================================
# SAVE MARKDOWN
# ============================================================

def save_markdown(
    url: str,
    title: str,
    content: str,
) -> Path:

    filename = (
        f"{safe_filename(url)}"
        f"_{url_hash(url)}.md"
    )

    path = MARKDOWN_DIR / filename

    safe_title = (
        title
        .replace('"', "'")
        .replace("\n", " ")
    )

    document = (
        "---\n"
        f'source: "{url}"\n'
        'organization: "MSD Veterinary Manual"\n'
        'source_type: "Veterinary medical reference"\n'
        f'title: "{safe_title}"\n'
        "---\n\n"
        f"# {title}\n\n"
        f"Source: {url}\n\n"
        "---\n\n"
        f"{content}\n"
    )

    path.write_text(
        document,
        encoding="utf-8",
    )

    return path


# ============================================================
# PROCESS PAGE
# ============================================================

def process_url(
    session,
    limiter,
    robots,
    url,
    state,
):

    result = fetch_url(
        session,
        limiter,
        robots,
        url,
    )

    status = result["status"]

    if status != "success":

        if status == "access_restricted":

            state.restricted_urls.add(
                url
            )

        elif status in (
            "temporary_failure",
            "failed",
        ):

            state.failed_urls.add(
                url
            )

        metadata = {
            "url": url,
            "status": status,
            "status_code": result.get(
                "status_code"
            ),
            "timestamp": time.time(),
        }

        append_jsonl(
            ERRORS_FILE,
            metadata,
        )

        return set(), metadata

    final_url = result["url"]

    content = result["content"]

    digest = content_hash(
        content
    )

    if digest in state.content_hashes:

        return set(), {
            "url": final_url,
            "status": "duplicate_content",
        }

    state.content_hashes.add(
        digest
    )

    html = content.decode(
        "utf-8",
        errors="ignore",
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title = extract_title(
        soup
    )

    html_path = save_html(
        final_url,
        html,
    )

    markdown_content = extract_markdown(
        html
    )

    markdown_path = save_markdown(
        final_url,
        title,
        markdown_content,
    )

    metadata = {
        "url": final_url,
        "status": "scraped",
        "title": title,
        "organization": (
            "MSD Veterinary Manual"
        ),
        "html_file": str(
            html_path
        ),
        "markdown_file": str(
            markdown_path
        ),
        "characters": len(
            markdown_content
        ),
        "sha256": digest,
        "timestamp": time.time(),
    }

    append_jsonl(
        DOCUMENTS_FILE,
        metadata,
    )

    state.successful_urls.add(
        final_url
    )

    links = extract_links(
        html,
        final_url,
    )

    return links, metadata


# ============================================================
# CSV EXPORT
# ============================================================

def write_csv(records):

    if not records:
        return

    fields = sorted(
        {
            key
            for record in records
            for key in record.keys()
        }
    )

    with URLS_CSV.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        writer.writeheader()

        writer.writerows(
            records
        )


# ============================================================
# MAIN
# ============================================================

def run():

    create_directories()

    logger.info("=" * 70)

    logger.info(
        "VETAURA MSD VETERINARY MANUAL COLLECTOR"
    )

    logger.info("=" * 70)

    session = create_session()

    limiter = RateLimiter(
        MIN_DELAY_SECONDS
    )

    robots, robots_text = load_robots(
        session
    )

    state = CrawlState()

    state.load()

    records = []

    # --------------------------------------------------------
    # SITEMAP DISCOVERY
    # --------------------------------------------------------

    sitemap_urls = discover_sitemap_urls(
        session,
        limiter,
        robots,
        robots_text,
    )

    # --------------------------------------------------------
    # BUILD QUEUE
    # --------------------------------------------------------

    queue = deque()

    # Sitemap URLs first.
    for url in sorted(sitemap_urls):

        if (
            url not in state.successful_urls
            and url not in state.restricted_urls
        ):

            queue.append(url)

            state.discovered_urls.add(
                url
            )

    # Seeds are fallback/discovery pages.
    for url in SEED_URLS:

        url = normalize_url(
            url
        )

        if (
            url not in state.successful_urls
            and url not in state.restricted_urls
            and url not in queue
        ):

            queue.append(url)

            state.discovered_urls.add(
                url
            )

    logger.info(
        f"INITIAL QUEUE SIZE: "
        f"{len(queue)}"
    )

    processed = 0

    try:

        while (
            queue
            and processed < MAX_PAGES
        ):

            url = queue.popleft()

            if url in state.successful_urls:
                continue

            if url in state.restricted_urls:
                continue

            if not is_relevant_url(url):
                continue

            processed += 1

            state.visited_urls.add(
                url
            )

            logger.info(
                f"PROCESSING "
                f"{processed}/{MAX_PAGES} | "
                f"Queue={len(queue)} | "
                f"{url}"
            )

            links, metadata = process_url(
                session,
                limiter,
                robots,
                url,
                state,
            )

            records.append(
                metadata
            )

            for link in links:

                if (
                    link not in state.discovered_urls
                    and link not in state.successful_urls
                    and link not in state.restricted_urls
                ):

                    queue.append(
                        link
                    )

                    state.discovered_urls.add(
                        link
                    )

            if processed % SAVE_EVERY == 0:

                state.save()

                write_csv(
                    records
                )

                logger.info(
                    f"PROGRESS | "
                    f"Processed={processed} | "
                    f"Queue={len(queue)} | "
                    f"Successful={len(state.successful_urls)} | "
                    f"Failed={len(state.failed_urls)} | "
                    f"Restricted={len(state.restricted_urls)}"
                )

    except KeyboardInterrupt:

        logger.warning(
            "Crawler stopped manually"
        )

    finally:

        state.save()

        write_csv(
            records
        )

        session.close()

    logger.info("=" * 70)

    logger.info(
        "MSD COLLECTION COMPLETE"
    )

    logger.info(
        f"Processed: {processed}"
    )

    logger.info(
        f"Successful: "
        f"{len(state.successful_urls)}"
    )

    logger.info(
        f"Failed: "
        f"{len(state.failed_urls)}"
    )

    logger.info(
        f"Restricted: "
        f"{len(state.restricted_urls)}"
    )

    logger.info(
        f"Output: {OUTPUT_DIR}"
    )

    logger.info("=" * 70)


if __name__ == "__main__":

    run()