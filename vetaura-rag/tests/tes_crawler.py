from pathlib import Path
import sys

print("Current file:", __file__)
print("Current working directory:", Path.cwd())

PROJECT_ROOT = Path(__file__).resolve().parents[1]
print("Project root:", PROJECT_ROOT)

sys.path.insert(0, str(PROJECT_ROOT))

from ingestion.crawler import WebCrawler

url = "https://www.msdvetmanual.com"

crawler = WebCrawler()

html, title = crawler.fetch_html(url)

print(title)

path = crawler.save_html(
    html,
    source="msd",
    filename="homepage"
)

print(path)

crawler.close()