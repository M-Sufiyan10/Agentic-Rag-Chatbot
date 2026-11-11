"""Text extraction from uploaded files, and chunking for embedding."""

import io
from pathlib import PurePath

from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class ExtractionError(ValueError):
    """The file's text could not be extracted; the message is safe to show the user."""


def _pdf_pages(data: bytes) -> list[str]:
    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]


def _docx_text(data: bytes) -> list[str]:
    return ["\n".join(p.text for p in DocxDocument(io.BytesIO(data)).paragraphs)]


def _plain_text(data: bytes) -> list[str]:
    return [data.decode("utf-8", errors="replace").removeprefix("﻿")]


def extract_text(filename: str, data: bytes) -> list[str]:
    """Return the file's text: one entry per page for PDFs, otherwise a single entry."""
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(f"Unsupported file type {ext or filename!r}.")
    try:
        if ext == ".pdf":
            pages = _pdf_pages(data)
        elif ext == ".docx":
            pages = _docx_text(data)
        else:
            pages = _plain_text(data)
    except Exception as exc:
        raise ExtractionError(f"The file could not be read as {ext[1:].upper()}.") from exc
    if not "".join(pages).strip():
        raise ExtractionError("No text could be extracted (scanned PDFs are not supported).")
    return pages


def chunk_pages(pages: list[str], size: int, overlap: int) -> list[str]:
    """Split the pages' text into overlapping chunks, dropping blank ones."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    return [c for c in splitter.split_text("\n\n".join(pages)) if c.strip()]
