from pathlib import Path
import json

from ingestion.pdf.pdf_loader import PDFLoader
from ingestion.pdf.cleaner import PDFCleaner
from ingestion.pdf.metadata import MetadataGenerator


# Project root
BASE_DIR = Path(__file__).resolve().parents[2]

# Input PDFs
RAW_DIR = BASE_DIR / "knowledge_base" / "raw"

# Output Markdown
MARKDOWN_DIR = BASE_DIR / "knowledge_base" / "markdown"

# Output Metadata
METADATA_DIR = BASE_DIR / "knowledge_base" / "metadata"


def process_pdf(pdf_path: Path) -> bool:
    """
    Process one PDF:
    1. Extract text
    2. Clean text
    3. Save as Markdown
    4. Generate metadata
    5. Save metadata as JSON

    The original folder structure is preserved.
    """

    try:
        # Example:
        # raw/aaha/vaccinations/file.pdf
        #
        # becomes:
        # markdown/aaha/vaccinations/file.md
        #
        # and:
        # metadata/aaha/vaccinations/file.json

        relative_path = pdf_path.relative_to(RAW_DIR)

        # Markdown output path
        markdown_path = (
            MARKDOWN_DIR / relative_path
        ).with_suffix(".md")

        markdown_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # -------------------------
        # Extract PDF text
        # -------------------------

        loader = PDFLoader(pdf_path)

        raw_text = loader.load()

        # -------------------------
        # Clean extracted text
        # -------------------------

        text = PDFCleaner.clean(raw_text)

        if not text.strip():
            raise ValueError(
                "No usable text after cleaning"
            )

        # -------------------------
        # Save Markdown
        # -------------------------

        markdown_path.write_text(
            text,
            encoding="utf-8"
        )

        # -------------------------
        # Generate metadata
        # -------------------------

        source = relative_path.parts[0]

        metadata = MetadataGenerator.generate(
            pdf_path=pdf_path,
            source=source,
            text=text
        )

        # -------------------------
        # Metadata output path
        # -------------------------

        metadata_relative_path = (
            relative_path.with_suffix(".json")
        )

        metadata_path = (
            METADATA_DIR / metadata_relative_path
        )

        metadata_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # -------------------------
        # Save metadata
        # -------------------------

        metadata_path.write_text(
            json.dumps(
                metadata,
                indent=4,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        # -------------------------
        # Success output
        # -------------------------

        print(f"✓ Processed: {pdf_path.name}")

        print(
            f"  Input: "
            f"{relative_path}"
        )

        print(
            f"  Markdown: "
            f"{markdown_path.relative_to(BASE_DIR)}"
        )

        print(
            f"  Metadata: "
            f"{metadata_path.relative_to(BASE_DIR)}"
        )

        print(
            f"  Characters: "
            f"{len(text):,}\n"
        )

        return True

    except Exception as e:

        print(f"✗ FAILED: {pdf_path.name}")

        print(
            f"  Error: {type(e).__name__}: {e}\n"
        )

        return False


def process_source(source: str):
    """
    Process all PDFs inside one source directory.
    """

    source_dir = RAW_DIR / source

    if not source_dir.exists():

        print(
            f"\nDirectory not found: "
            f"{source_dir}"
        )

        return 0, 0

    # Find all PDFs recursively
    pdf_files = sorted(
        source_dir.rglob("*.pdf")
    )

    print("\n" + "=" * 60)

    print(
        f"PROCESSING SOURCE: "
        f"{source.upper()}"
    )

    print(
        f"PDFs found: {len(pdf_files)}"
    )

    print("=" * 60 + "\n")

    successful = 0
    failed = 0

    for pdf_path in pdf_files:

        success = process_pdf(
            pdf_path
        )

        if success:

            successful += 1

        else:

            failed += 1

    return successful, failed


def main():

    sources = [
        "aaha",
        "wsava",
    ]

    total_successful = 0
    total_failed = 0

    for source in sources:

        successful, failed = process_source(
            source
        )

        total_successful += successful
        total_failed += failed

    print("\n" + "=" * 60)

    print("PDF PROCESSING COMPLETE")

    print("=" * 60)

    print(
        f"Successful: "
        f"{total_successful}"
    )

    print(
        f"Failed:     "
        f"{total_failed}"
    )


if __name__ == "__main__":
    main()