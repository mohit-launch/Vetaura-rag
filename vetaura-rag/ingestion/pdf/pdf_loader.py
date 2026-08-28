from pathlib import Path
import re
import fitz


class PDFLoader:
    """
    Loads text-based PDF documents and extracts text page by page.
    """

    def __init__(self, pdf_path: Path):
        self.pdf_path = Path(pdf_path)

        if not self.pdf_path.exists():
            raise FileNotFoundError(
                f"PDF not found: {self.pdf_path}"
            )

        if self.pdf_path.suffix.lower() != ".pdf":
            raise ValueError(
                f"Expected a PDF file, got: {self.pdf_path.suffix}"
            )

    def extract_text(self) -> str:
        """
        Extract all text from the PDF.
        """

        pages = []

        with fitz.open(self.pdf_path) as document:

            for page_number, page in enumerate(document, start=1):

                text = page.get_text("text")

                if text:
                    pages.append(
                        f"\n\n<!-- PAGE {page_number} -->\n\n{text}"
                    )

        return "".join(pages)

    @staticmethod
    def clean_text(text: str) -> str:
        """
        Perform basic cleaning while preserving document structure.
        """

        # Normalize line endings
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")

        # Remove excessive spaces
        text = re.sub(r"[ \t]+", " ", text)

        # Reduce excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    def load(self) -> str:
        """
        Extract and clean the PDF text.
        """

        raw_text = self.extract_text()

        if not raw_text.strip():
            raise ValueError(
                f"No extractable text found in: {self.pdf_path.name}"
            )

        return self.clean_text(raw_text)