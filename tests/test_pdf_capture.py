from io import BytesIO
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from web_research_sim.capture import _extract, fetch_page
from web_research_sim.core import Store
from test_capture import _response_fixture


def pdf(text="Water level is 42 cm."):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_text_worker_extracts_real_pdf_bytes():
    text, title, links = _extract(pdf(), "application/pdf", "https://example.org/file.pdf")
    assert "Water level is 42 cm." in text
    assert links == []


def test_pdf_original_and_version_preserved(monkeypatch, tmp_path):
    body = pdf()
    _response_fixture(monkeypatch, body, headers={"content-type": "application/pdf"})
    page = fetch_page("https://example.org/file.pdf")
    assert page.extractor.startswith("pypdf-") and page.extraction_status == "completed"
    store = Store(tmp_path)
    world = store.create_world("PDF fixture")
    record = store.add_capture(world["id"], **page.__dict__)
    assert store.objects.get(record["body_sha256"]) == body


def test_failed_extraction_preserves_original(monkeypatch):
    body = b"%PDF invalid fixture"
    _response_fixture(monkeypatch, body, headers={"content-type": "application/pdf"})
    page = fetch_page("https://example.org/file.pdf")
    assert page.body == body and page.text == ""
    assert page.extraction_status == "failed"
    assert page.extraction_error == "pdf_extraction_failed_or_no_text"


def test_unsupported_type_is_archived_with_explicit_gap(monkeypatch):
    body = b"uninterpreted fixture bytes"
    _response_fixture(monkeypatch, body, headers={"content-type": "application/octet-stream"})
    page = fetch_page("https://example.org/asset")
    assert page.body == body and page.extraction_status == "failed"
