from pathlib import Path
from datetime import datetime


class MetadataGenerator:

    @staticmethod
    def generate(
        pdf_path: Path,
        source: str,
        text: str
    ) -> dict:

        return {
            "source": source,
            "filename": pdf_path.name,
            "document_id": pdf_path.stem.lower().replace(" ", "_"),
            "file_type": "pdf",
            "processed_at": datetime.now().isoformat(),
            "character_count": len(text),
            "word_count": len(text.split()),
        }