from io import BytesIO

import pdfplumber
from pypdf import PdfReader


def extract_text(pdf_bytes: bytes) -> str:
    """Extract text from a resume PDF. Tries pdfplumber first, falls back to pypdf."""
    text = _extract_with_pdfplumber(pdf_bytes)
    if text.strip():
        return text
    return _extract_with_pypdf(pdf_bytes)


def _extract_with_pdfplumber(pdf_bytes: bytes) -> str:
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _extract_with_pypdf(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
