import io
from pathlib import Path

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.documents.extraction import ExtractionError, chunk_pages, extract_text

FIXTURES = Path(__file__).parent / "fixtures"


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_one_entry_per_page() -> None:
    pages = extract_text("a.pdf", (FIXTURES / "hello.pdf").read_bytes())
    assert [p.strip() for p in pages] == ["Hello PDF world"]


def test_blank_pdf_is_rejected_as_scanned() -> None:
    with pytest.raises(ExtractionError, match="scanned PDFs"):
        extract_text("blank.pdf", _blank_pdf())


def test_garbage_pdf_cannot_be_read() -> None:
    with pytest.raises(ExtractionError, match="could not be read as PDF"):
        extract_text("x.pdf", b"\x00\x01 definitely not a pdf")


def test_docx_paragraphs_joined() -> None:
    doc = DocxDocument()
    doc.add_paragraph("First paragraph")
    doc.add_paragraph("Second paragraph")
    buffer = io.BytesIO()
    doc.save(buffer)
    assert extract_text("a.docx", buffer.getvalue()) == ["First paragraph\nSecond paragraph"]


def test_garbage_docx_cannot_be_read() -> None:
    with pytest.raises(ExtractionError, match="could not be read as DOCX"):
        extract_text("x.docx", b"not a zip")


def test_text_strips_bom() -> None:
    assert extract_text("a.txt", b"\xef\xbb\xbfhello") == ["hello"]


def test_markdown_is_plain_text() -> None:
    assert extract_text("a.md", b"# Title\n\nbody") == ["# Title\n\nbody"]


def test_empty_text_raises() -> None:
    with pytest.raises(ExtractionError, match="No text could be extracted"):
        extract_text("a.txt", b"  \n ")


def test_chunking_splits_with_overlap() -> None:
    text = " ".join(f"word{i}" for i in range(600))[:3500]
    chunks = chunk_pages([text], size=1000, overlap=150)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    assert any(a[-20:] in b for a, b in zip(chunks, chunks[1:], strict=False))


def test_chunking_drops_blank_chunks() -> None:
    assert chunk_pages(["", "   "], size=1000, overlap=150) == []
