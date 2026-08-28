import re


class PDFCleaner:
    """
    Cleans text extracted from veterinary PDF documents.
    """

    @staticmethod
    def clean(text: str) -> str:
        """
        Main cleaning pipeline.
        """

        text = PDFCleaner.normalize_whitespace(text)
        text = PDFCleaner.remove_page_markers(text)
        text = PDFCleaner.remove_page_numbers(text)
        text = PDFCleaner.remove_empty_lines(text)

        return text.strip()


    @staticmethod
    def normalize_whitespace(text: str) -> str:

        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")

        # Multiple spaces → one space
        text = re.sub(r"[ \t]+", " ", text)

        # Too many empty lines → max two
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text


    @staticmethod
    def remove_page_markers(text: str) -> str:

        # Remove markers like:
        # <!-- PAGE 1 -->
        text = re.sub(
            r"<!-- PAGE \d+ -->",
            "",
            text
        )

        return text


    @staticmethod
    def remove_page_numbers(text: str) -> str:

        lines = []

        for line in text.splitlines():

            stripped = line.strip()

            # Remove lines containing only numbers
            if re.fullmatch(r"\d+", stripped):
                continue

            lines.append(line)

        return "\n".join(lines)


    @staticmethod
    def remove_empty_lines(text: str) -> str:

        lines = []

        previous_blank = False

        for line in text.splitlines():

            is_blank = not line.strip()

            if is_blank and previous_blank:
                continue

            lines.append(line)

            previous_blank = is_blank

        return "\n".join(lines)