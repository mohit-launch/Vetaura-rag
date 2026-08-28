from __future__ import annotations

import csv
import hashlib
import json
import logging
import mimetypes
import random
import re
import time
import xml.etree.ElementTree as ET

from collections import deque
from pathlib import Path
from typing import Optional
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)
from urllib.robotparser import RobotFileParser

import requests
import trafilatura

from bs4 import BeautifulSoup
from markdownify import markdownify


# ============================================================
# VETAURA AAHA KNOWLEDGE COLLECTOR
# ============================================================
#
# PURPOSE:
# Collect publicly accessible AAHA guideline/resource content for
# local research and ingestion into a Vetaura knowledge pipeline.
#
# IMPORTANT:
# - Respects robots.txt
# - Does not bypass login or email gates
# - Does not bypass CAPTCHA/WAF challenges
# - Saves restricted URLs as metadata and continues
# - Downloads only publicly returned documents
#
# ============================================================


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[2]

OUTPUT_DIR = (
    BASE_DIR
    / "knowledge_base"
    / "raw"
    / "aaha"
)

HTML_DIR = OUTPUT_DIR / "html"

MARKDOWN_DIR = OUTPUT_DIR / "markdown"

DOWNLOADS_DIR = OUTPUT_DIR / "downloads"

METADATA_DIR = OUTPUT_DIR / "metadata"

LOG_DIR = OUTPUT_DIR / "logs"

STATE_FILE = METADATA_DIR / "crawl_state.json"

DOCUMENTS_FILE = METADATA_DIR / "documents.jsonl"

ERRORS_FILE = METADATA_DIR / "errors.jsonl"

URLS_CSV = METADATA_DIR / "urls.csv"


# ============================================================
# AAHA STARTING POINTS
# ============================================================

SEED_URLS = [
    "https://www.aaha.org/for-veterinary-professionals/aaha-guidelines/",
    "https://www.aaha.org/for-veterinary-professionals/resources/",
    "https://www.aaha.org/site-map/",
]


# ============================================================
# SITEMAP LOCATIONS
# ============================================================

SITEMAP_CANDIDATES = [
    "https://www.aaha.org/sitemap.xml",
    "https://www.aaha.org/sitemap_index.xml",
    "https://www.aaha.org/wp-sitemap.xml",
]


# ============================================================
# DOMAIN SETTINGS
# ============================================================

ALLOWED_DOMAINS = {
    "aaha.org",
    "www.aaha.org",
}


# ============================================================
# CRAWL SETTINGS
# ============================================================

MAX_PAGES = 5000

REQUEST_DELAY_SECONDS = 1.5

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

VERIFY_SSL = True


# ============================================================
# RELEVANT PATHS
# ============================================================

RELEVANT_PATH_PREFIXES = (
    "/resources/",
    "/for-veterinary-professionals/aaha-guidelines/",
    "/for-veterinary-professionals/resources/",
)


# ============================================================
# KEYWORDS FOR RELEVANT CONTENT
# ============================================================

RELEVANT_KEYWORDS = (
    "guideline",
    "guidelines",
    "veterinary",
    "dog",
    "dogs",
    "cat",
    "cats",
    "canine",
    "feline",
    "pet",
    "animal",
    "vaccination",
    "nutrition",
    "pain",
    "diabetes",
    "dental",
    "anesthesia",
    "monitoring",
    "behavior",
    "behaviour",
    "infection",
    "biosecurity",
    "endocrine",
    "senior",
    "life-stage",
    "life stage",
    "allergic",
    "skin",
    "oncology",
    "fluid",
    "referral",
    "community care",
    "one health",
    "preventive",
    "healthcare",
    "antimicrobial",
    "telehealth",
    "therapy dog",
    "working dog",
    "toolkit",
)


# ============================================================
# FILE EXTENSIONS
# ============================================================

DOWNLOAD_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".zip",
    ".txt",
    ".ppt",
    ".pptx",
}


# ============================================================
# TRACKING PARAMETERS
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


# ============================================================
# BLOCKED PATHS
# ============================================================

BLOCKED_PATH_KEYWORDS = (
    "/login",
    "/logout",
    "/account",
    "/cart",
    "/checkout",
    "/my-account",
    "/wp-admin",
)


# ============================================================
# LOGGING
# ============================================================

def setup_logging():

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_file = (
        LOG_DIR
        / "aaha_scraper.log"
    )

    logger = logging.getLogger(
        "AAHA_SCRAPER"
    )

    logger.setLevel(
        logging.INFO
    )

    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )

    file_handler = logging.FileHandler(
        log_file,
        encoding="utf-8",
    )

    file_handler.setFormatter(
        formatter
    )

    console_handler = logging.StreamHandler()

    console_handler.setFormatter(
        formatter
    )

    logger.addHandler(
        file_handler
    )

    logger.addHandler(
        console_handler
    )

    return logger


logger = setup_logging()


# ============================================================
# HTTP SESSION
# ============================================================

def create_session():

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Vetaura Knowledge Collector/1.0 "
                "(public research collection)"
            ),

            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "application/pdf;q=0.9,"
                "*/*;q=0.8"
            ),

            "Accept-Language": (
                "en-US,en;q=0.9"
            ),
        }
    )

    return session


# ============================================================
# DIRECTORIES
# ============================================================

def create_directories():

    directories = [
        OUTPUT_DIR,
        HTML_DIR,
        MARKDOWN_DIR,
        DOWNLOADS_DIR,
        METADATA_DIR,
        LOG_DIR,
    ]

    for directory in directories:

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )


# ============================================================
# URL UTILITIES
# ============================================================

def normalize_url(
    url: str,
) -> str:

    parsed = urlparse(
        url
    )

    query_parameters = parse_qsl(
        parsed.query,
        keep_blank_values=True,
    )

    clean_query = []

    for key, value in query_parameters:

        if (
            key.lower()
            not in TRACKING_PARAMETERS
        ):

            clean_query.append(
                (key, value)
            )

    path = parsed.path

    if (
        path != "/"
        and path.endswith("/")
    ):

        path = path[:-1]

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        query=urlencode(
            clean_query
        ),
        fragment="",
    )

    return urlunparse(
        normalized
    )


def is_allowed_domain(
    url: str,
) -> bool:

    try:

        domain = (
            urlparse(url)
            .netloc
            .lower()
        )

        return (
            domain
            in ALLOWED_DOMAINS
        )

    except Exception:

        return False


def is_download_url(
    url: str,
) -> bool:

    path = (
        urlparse(url)
        .path
        .lower()
    )

    return any(
        path.endswith(extension)
        for extension
        in DOWNLOAD_EXTENSIONS
    )


def is_blocked_url(
    url: str,
) -> bool:

    path = (
        urlparse(url)
        .path
        .lower()
    )

    return any(
        keyword in path
        for keyword
        in BLOCKED_PATH_KEYWORDS
    )


def is_relevant_url(
    url: str,
) -> bool:

    if not is_allowed_domain(
        url
    ):

        return False

    if is_blocked_url(
        url
    ):

        return False

    if is_download_url(
        url
    ):

        return True

    path = (
        urlparse(url)
        .path
        .lower()
    )

    if path.startswith(
        RELEVANT_PATH_PREFIXES
    ):

        return True

    combined = url.lower()

    return any(
        keyword in combined
        for keyword
        in RELEVANT_KEYWORDS
    )


def safe_filename(
    value: str,
    max_length: int = 140,
) -> str:

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

    value = value.strip(
        "._"
    )

    if not value:

        value = "document"

    return value[
        :max_length
    ]


def short_hash(
    value: str,
) -> str:

    return (
        hashlib
        .sha256(
            value.encode(
                "utf-8"
            )
        )
        .hexdigest()[:16]
    )


def content_hash(
    content: bytes,
) -> str:

    return (
        hashlib
        .sha256(
            content
        )
        .hexdigest()
    )


# ============================================================
# ROBOTS.TXT
# ============================================================

def load_robots(
    session: requests.Session,
) -> RobotFileParser:

    robots_url = (
        "https://www.aaha.org/robots.txt"
    )

    parser = RobotFileParser()

    try:

        response = session.get(
            robots_url,
            timeout=REQUEST_TIMEOUT,
            verify=VERIFY_SSL,
        )

        logger.info(
            "ROBOTS | "
            f"{response.status_code} | "
            f"{robots_url}"
        )

        if response.status_code != 200:

            raise RuntimeError(
                "robots.txt unavailable"
            )

        parser.set_url(
            robots_url
        )

        parser.parse(
            response.text.splitlines()
        )

        logger.info(
            "robots.txt loaded successfully"
        )

        return parser

    except Exception as error:

        logger.error(
            f"Could not load robots.txt: "
            f"{error}"
        )

        raise


# ============================================================
# RATE LIMITING
# ============================================================

class RateLimiter:

    def __init__(
        self,
        delay: float,
    ):

        self.delay = delay

        self.last_request_time = 0.0

    def wait(self):

        now = time.monotonic()

        elapsed = (
            now -
            self.last_request_time
        )

        wait_time = (
            self.delay -
            elapsed
        )

        if wait_time > 0:

            time.sleep(
                wait_time
            )

        self.last_request_time = (
            time.monotonic()
        )


# ============================================================
# CRAWL STATE
# ============================================================

class CrawlState:

    def __init__(self):

        self.visited_urls = set()

        self.discovered_urls = set()

        self.content_hashes = set()

        self.restricted_urls = set()

        self.failed_urls = set()

        self.load()

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
                data.get(
                    "visited_urls",
                    [],
                )
            )

            self.discovered_urls = set(
                data.get(
                    "discovered_urls",
                    [],
                )
            )

            self.content_hashes = set(
                data.get(
                    "content_hashes",
                    [],
                )
            )

            self.restricted_urls = set(
                data.get(
                    "restricted_urls",
                    [],
                )
            )

            self.failed_urls = set(
                data.get(
                    "failed_urls",
                    [],
                )
            )

            logger.info(
                "Previous state loaded | "
                f"Visited: "
                f"{len(self.visited_urls)} | "
                f"Restricted: "
                f"{len(self.restricted_urls)}"
            )

        except Exception as error:

            logger.warning(
                f"Could not load "
                f"crawl state: {error}"
            )

    def save(self):

        data = {
            "visited_urls": sorted(
                self.visited_urls
            ),

            "discovered_urls": sorted(
                self.discovered_urls
            ),

            "content_hashes": sorted(
                self.content_hashes
            ),

            "restricted_urls": sorted(
                self.restricted_urls
            ),

            "failed_urls": sorted(
                self.failed_urls
            ),
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
# METADATA
# ============================================================

def append_jsonl(
    path: Path,
    data: dict,
):

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

        file.write(
            "\n"
        )


def append_document_metadata(
    metadata: dict,
):

    append_jsonl(
        DOCUMENTS_FILE,
        metadata,
    )


def append_error_metadata(
    metadata: dict,
):

    append_jsonl(
        ERRORS_FILE,
        metadata,
    )


# ============================================================
# HTTP FETCHING
# ============================================================

def fetch_url(
    session: requests.Session,
    limiter: RateLimiter,
    robots: RobotFileParser,
    url: str,
):

    if not robots.can_fetch(
        "*",
        url,
    ):

        logger.info(
            f"ROBOTS SKIP | {url}"
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
                verify=VERIFY_SSL,
            )

            final_url = normalize_url(
                response.url
            )

            logger.info(
                "REQUEST | "
                f"{response.status_code} | "
                f"{final_url}"
            )

            if (
                response.status_code
                in (
                    401,
                    403,
                )
            ):

                preview = (
                    response.text[:1000]
                    if "text"
                    in response.headers.get(
                        "Content-Type",
                        ""
                    ).lower()
                    else ""
                )

                logger.warning(
                    "ACCESS RESTRICTED | "
                    f"{response.status_code} | "
                    f"{final_url}"
                )

                append_error_metadata(
                    {
                        "url": final_url,
                        "status_code": (
                            response.status_code
                        ),
                        "reason": (
                            "access_restricted"
                        ),
                        "response_preview": (
                            preview
                        ),
                        "timestamp": (
                            time.time()
                        ),
                    }
                )

                return {
                    "status": (
                        "access_restricted"
                    ),

                    "status_code": (
                        response.status_code
                    ),

                    "url": final_url,

                    "response_headers": (
                        dict(
                            response.headers
                        )
                    ),
                }

            if (
                response.status_code == 404
            ):

                return {
                    "status": "not_found",
                    "status_code": 404,
                    "url": final_url,
                }

            if (
                response.status_code
                in (
                    429,
                    500,
                    502,
                    503,
                    504,
                )
            ):

                if (
                    attempt
                    < MAX_RETRIES
                ):

                    wait_time = (
                        (2 ** attempt)
                        +
                        random.uniform(
                            0,
                            1,
                        )
                    )

                    logger.warning(
                        "TEMPORARY ERROR | "
                        f"{response.status_code} | "
                        f"Retrying in "
                        f"{wait_time:.1f}s"
                    )

                    time.sleep(
                        wait_time
                    )

                    continue

            if (
                response.status_code
                != 200
            ):

                return {
                    "status": (
                        "http_error"
                    ),

                    "status_code": (
                        response.status_code
                    ),

                    "url": final_url,
                }

            return {
                "status": "success",

                "status_code": (
                    response.status_code
                ),

                "url": final_url,

                "content": (
                    response.content
                ),

                "content_type": (
                    response.headers.get(
                        "Content-Type",
                        "",
                    )
                ),

                "headers": dict(
                    response.headers
                ),
            }

        except requests.RequestException as error:

            logger.warning(
                "REQUEST ERROR | "
                f"Attempt "
                f"{attempt}/"
                f"{MAX_RETRIES} | "
                f"{url} | "
                f"{error}"
            )

            if (
                attempt
                < MAX_RETRIES
            ):

                time.sleep(
                    (2 ** attempt)
                    +
                    random.uniform(
                        0,
                        1,
                    )
                )

    return {
        "status": "failed",
        "url": url,
    }


# ============================================================
# SITEMAP DISCOVERY
# ============================================================

def parse_sitemap(
    xml_content: bytes,
) -> list[str]:

    discovered = []

    try:

        root = ET.fromstring(
            xml_content
        )

        namespace = (
            "{http://www.sitemaps.org/"
            "schemas/sitemap/0.9}"
        )

        for loc in root.findall(
            f".//{namespace}loc"
        ):

            if loc.text:

                discovered.append(
                    normalize_url(
                        loc.text.strip()
                    )
                )

    except Exception as error:

        logger.warning(
            f"Sitemap parsing error: "
            f"{error}"
        )

    return discovered


def discover_from_sitemaps(
    session: requests.Session,
    limiter: RateLimiter,
    robots: RobotFileParser,
) -> set[str]:

    logger.info(
        "Starting sitemap discovery"
    )

    sitemap_queue = deque(
        SITEMAP_CANDIDATES
    )

    processed_sitemaps = set()

    discovered_urls = set()

    while sitemap_queue:

        sitemap_url = (
            sitemap_queue.popleft()
        )

        if (
            sitemap_url
            in processed_sitemaps
        ):

            continue

        processed_sitemaps.add(
            sitemap_url
        )

        result = fetch_url(
            session,
            limiter,
            robots,
            sitemap_url,
        )

        if (
            result["status"]
            != "success"
        ):

            continue

        content_type = (
            result
            .get(
                "content_type",
                "",
            )
            .lower()
        )

        if (
            "xml"
            not in content_type
            and not sitemap_url.endswith(
                ".xml"
            )
        ):

            continue

        urls = parse_sitemap(
            result["content"]
        )

        for discovered_url in urls:

            if (
                discovered_url.endswith(
                    ".xml"
                )
            ):

                sitemap_queue.append(
                    discovered_url
                )

                continue

            if is_relevant_url(
                discovered_url
            ):

                discovered_urls.add(
                    discovered_url
                )

    logger.info(
        "Sitemap discovery complete | "
        f"URLs found: "
        f"{len(discovered_urls)}"
    )

    return discovered_urls


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

    discovered = set()

    for tag in soup.find_all(
        ["a", "iframe"]
    ):

        attribute = (
            "href"
            if tag.name == "a"
            else "src"
        )

        link = tag.get(
            attribute
        )

        if not link:

            continue

        absolute_url = urljoin(
            current_url,
            link,
        )

        normalized = normalize_url(
            absolute_url
        )

        if (
            is_relevant_url(
                normalized
            )
        ):

            discovered.add(
                normalized
            )

    return discovered


# ============================================================
# GATE DETECTION
# ============================================================

def detect_gated_page(
    html: str,
) -> bool:

    text = html.lower()

    gate_signals = [
        "members only",
        "member exclusive",
        "login to access",
        "log in to access",
        "sign in to access",
        "enter your work email",
        "provide a work email",
    ]

    return any(
        signal in text
        for signal
        in gate_signals
    )


# ============================================================
# CONTENT EXTRACTION
# ============================================================

def extract_title(
    soup: BeautifulSoup,
) -> str:

    if (
        soup.title
        and soup.title.string
    ):

        return (
            soup.title.string
            .strip()
        )

    h1 = soup.find("h1")

    if h1:

        return h1.get_text(
            " ",
            strip=True,
        )

    return "Untitled AAHA Document"


def clean_html_for_fallback(
    soup: BeautifulSoup,
):

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "iframe",
            "nav",
            "footer",
            "header",
            "aside",
        ]
    ):

        tag.decompose()

    for element in soup.find_all(
        string=re.compile(
            r"Advertisement|Cookie|Subscribe",
            re.I,
        )
    ):

        parent = element.parent

        if parent:

            parent.decompose()

    return soup


def extract_markdown(
    html: str,
) -> str:

    try:

        extracted = (
            trafilatura.extract(
                html,
                output_format="markdown",
                include_links=True,
                include_tables=True,
                favor_precision=True,
                include_comments=False,
            )
        )

        if (
            extracted
            and len(
                extracted.strip()
            ) > 150
        ):

            return (
                extracted.strip()
            )

    except Exception as error:

        logger.warning(
            f"Trafilatura failed: "
            f"{error}"
        )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    soup = clean_html_for_fallback(
        soup
    )

    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(
            class_=re.compile(
                r"content|article|resource",
                re.I,
            )
        )
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
# FILE SAVING
# ============================================================

def save_html(
    url: str,
    html: str,
) -> Path:

    filename = (
        f"{safe_filename(url)}"
        f"_{short_hash(url)}.html"
    )

    path = (
        HTML_DIR
        / filename
    )

    path.write_text(
        html,
        encoding="utf-8",
    )

    return path


def save_markdown(
    url: str,
    title: str,
    markdown_content: str,
) -> Path:

    filename = (
        f"{safe_filename(url)}"
        f"_{short_hash(url)}.md"
    )

    path = (
        MARKDOWN_DIR
        / filename
    )

    safe_title = (
        title
        .replace(
            '"',
            "'"
        )
        .replace(
            "\n",
            " "
        )
    )

    document = (
        "---\n"
        f'source: "{url}"\n'
        'organization: "American Animal Hospital Association"\n'
        'source_type: "AAHA public website"\n'
        f'title: "{safe_title}"\n'
        "---\n\n"
        f"# {title}\n\n"
        f"Source: {url}\n\n"
        "---\n\n"
        f"{markdown_content}\n"
    )

    path.write_text(
        document,
        encoding="utf-8",
    )

    return path


def determine_extension(
    url: str,
    content_type: str,
) -> str:

    path_extension = (
        Path(
            urlparse(url).path
        )
        .suffix
        .lower()
    )

    if path_extension:

        return path_extension

    mime_type = (
        content_type
        .split(";")[0]
        .strip()
    )

    guessed = (
        mimetypes.guess_extension(
            mime_type
        )
    )

    if guessed:

        return guessed

    return ".bin"


def save_binary(
    url: str,
    content: bytes,
    content_type: str,
) -> Path:

    extension = (
        determine_extension(
            url,
            content_type,
        )
    )

    file_hash = content_hash(
        content
    )

    filename = (
        f"{safe_filename(url)}"
        f"_{file_hash[:16]}"
        f"{extension}"
    )

    path = (
        DOWNLOADS_DIR
        / filename
    )

    if not path.exists():

        path.write_bytes(
            content
        )

    return path


# ============================================================
# CSV URL EXPORT
# ============================================================

def write_url_csv(
    records: list[dict],
):

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

        for record in records:

            writer.writerow(
                record
            )


# ============================================================
# PAGE PROCESSING
# ============================================================

def process_url(
    session: requests.Session,
    limiter: RateLimiter,
    robots: RobotFileParser,
    url: str,
    state: CrawlState,
):

    result = fetch_url(
        session,
        limiter,
        robots,
        url,
    )

    if (
        result["status"]
        != "success"
    ):

        if (
            result["status"]
            == "access_restricted"
        ):

            state.restricted_urls.add(
                url
            )

        elif (
            result["status"]
            not in (
                "robots_disallowed",
                "not_found",
            )
        ):

            state.failed_urls.add(
                url
            )

        return set(), {
            "url": url,
            "status": (
                result["status"]
            ),
        }

    final_url = (
        result["url"]
    )

    content = (
        result["content"]
    )

    content_type = (
        result
        .get(
            "content_type",
            "",
        )
        .lower()
    )

    file_content_hash = (
        content_hash(
            content
        )
    )

    if (
        file_content_hash
        in state.content_hashes
    ):

        logger.info(
            f"DUPLICATE CONTENT | "
            f"{final_url}"
        )

        return set(), {
            "url": final_url,
            "status": (
                "duplicate_content"
            ),
        }

    state.content_hashes.add(
        file_content_hash
    )

    is_binary = (
        is_download_url(
            final_url
        )
        or "application/pdf"
        in content_type
        or (
            "application/"
            in content_type
            and "json"
            not in content_type
        )
    )

    if is_binary:

        file_path = save_binary(
            final_url,
            content,
            content_type,
        )

        logger.info(
            f"DOWNLOADED | "
            f"{file_path.name}"
        )

        metadata = {
            "url": final_url,
            "status": "downloaded",
            "type": "binary",
            "content_type": content_type,
            "file": str(
                file_path
            ),
            "sha256": (
                file_content_hash
            ),
            "timestamp": time.time(),
        }

        append_document_metadata(
            metadata
        )

        return set(), metadata

    html = content.decode(
        "utf-8",
        errors="ignore",
    )

    if detect_gated_page(
        html
    ):

        logger.info(
            f"GATED CONTENT | "
            f"{final_url}"
        )

        state.restricted_urls.add(
            final_url
        )

        metadata = {
            "url": final_url,
            "status": "gated",
            "type": "html",
            "timestamp": time.time(),
        }

        append_document_metadata(
            metadata
        )

        return set(), metadata

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

    markdown_content = (
        extract_markdown(
            html
        )
    )

    markdown_path = (
        save_markdown(
            final_url,
            title,
            markdown_content,
        )
    )

    logger.info(
        f"SCRAPED | "
        f"{title}"
    )

    metadata = {
        "url": final_url,
        "status": "scraped",
        "type": "html",
        "title": title,
        "content_type": content_type,
        "html_file": str(
            html_path
        ),
        "markdown_file": str(
            markdown_path
        ),
        "characters": len(
            markdown_content
        ),
        "sha256": (
            file_content_hash
        ),
        "timestamp": time.time(),
    }

    append_document_metadata(
        metadata
    )

    discovered_links = (
        extract_links(
            html,
            final_url,
        )
    )

    return (
        discovered_links,
        metadata,
    )


# ============================================================
# MAIN CRAWLER
# ============================================================

def run():

    create_directories()

    logger.info(
        "=" * 70
    )

    logger.info(
        "VETAURA AAHA "
        "KNOWLEDGE COLLECTOR"
    )

    logger.info(
        "=" * 70
    )

    session = create_session()

    limiter = RateLimiter(
        REQUEST_DELAY_SECONDS
    )

    robots = load_robots(
        session
    )

    state = CrawlState()

    records = []

    # --------------------------------------------------------
    # DISCOVER SITEMAP URLS
    # --------------------------------------------------------

    sitemap_urls = (
        discover_from_sitemaps(
            session,
            limiter,
            robots,
        )
    )

    # --------------------------------------------------------
    # BUILD INITIAL QUEUE
    # --------------------------------------------------------

    queue = deque()

    for url in SEED_URLS:

        normalized = (
            normalize_url(
                url
            )
        )

        queue.append(
            normalized
        )

        state.discovered_urls.add(
            normalized
        )

    for url in sitemap_urls:

        if (
            url
            not in state.discovered_urls
        ):

            queue.append(
                url
            )

            state.discovered_urls.add(
                url
            )

    logger.info(
        "Initial URLs queued: "
        f"{len(queue)}"
    )

    # --------------------------------------------------------
    # CRAWL
    # --------------------------------------------------------

    processed_count = 0

    try:

        while (
            queue
            and processed_count
            < MAX_PAGES
        ):

            url = queue.popleft()

            url = normalize_url(
                url
            )

            if (
                url
                in state.visited_urls
            ):

                continue

            if not is_relevant_url(
                url
            ):

                continue

            state.visited_urls.add(
                url
            )

            processed_count += 1

            logger.info(
                f"PROCESSING "
                f"{processed_count}/"
                f"{MAX_PAGES} | "
                f"{url}"
            )

            discovered_links, metadata = (
                process_url(
                    session,
                    limiter,
                    robots,
                    url,
                    state,
                )
            )

            records.append(
                metadata
            )

            for new_url in discovered_links:

                normalized = (
                    normalize_url(
                        new_url
                    )
                )

                if (
                    normalized
                    not in state.discovered_urls
                    and normalized
                    not in state.visited_urls
                ):

                    if is_relevant_url(
                        normalized
                    ):

                        queue.append(
                            normalized
                        )

                        state.discovered_urls.add(
                            normalized
                        )

            # Save progress every 10 pages
            if (
                processed_count
                % 10
                == 0
            ):

                state.save()

                write_url_csv(
                    records
                )

                logger.info(
                    "PROGRESS | "
                    f"Processed: "
                    f"{processed_count} | "
                    f"Queue: "
                    f"{len(queue)} | "
                    f"Visited: "
                    f"{len(state.visited_urls)} | "
                    f"Restricted: "
                    f"{len(state.restricted_urls)}"
                )

    except KeyboardInterrupt:

        logger.warning(
            "Crawler stopped manually"
        )

    finally:

        state.save()

        write_url_csv(
            records
        )

        session.close()

    # --------------------------------------------------------
    # FINAL SUMMARY
    # --------------------------------------------------------

    logger.info(
        "=" * 70
    )

    logger.info(
        "AAHA COLLECTION COMPLETE"
    )

    logger.info(
        f"Processed URLs: "
        f"{processed_count}"
    )

    logger.info(
        f"Visited URLs: "
        f"{len(state.visited_urls)}"
    )

    logger.info(
        f"Discovered URLs: "
        f"{len(state.discovered_urls)}"
    )

    logger.info(
        f"Restricted URLs: "
        f"{len(state.restricted_urls)}"
    )

    logger.info(
        f"Failed URLs: "
        f"{len(state.failed_urls)}"
    )

    logger.info(
        f"Output directory: "
        f"{OUTPUT_DIR}"
    )

    logger.info(
        "=" * 70
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    run()