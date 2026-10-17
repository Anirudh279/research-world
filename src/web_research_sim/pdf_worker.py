"""Bounded PDF text worker. Reads stdin bytes; never resolves external resources."""
import io
import json
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    # Darwin does not support enforcing this virtual-address limit reliably.
    # Production memory isolation is provided by the Linux container path.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    from pypdf import PdfReader
    body = sys.stdin.buffer.read(16 * 1024**2 + 1)
    if len(body) > 16 * 1024**2:
        raise ValueError("input_limit")
    reader = PdfReader(io.BytesIO(body), strict=True)
    if reader.is_encrypted:
        raise ValueError("encrypted_pdf")
    if len(reader.pages) > 200:
        raise ValueError("page_limit")
    pages, size = [], 0
    for page in reader.pages:
        text = page.extract_text() or ""
        size += len(text.encode("utf-8"))
        if size > 4 * 1024**2:
            raise ValueError("text_limit")
        pages.append(text)
    text = "\n\n".join(pages)
    if not text.strip():
        raise ValueError("no_extractable_text")
    sys.stdout.write(json.dumps({"text": text, "title": str((reader.metadata or {}).get("/Title", ""))[:1000]}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Parser messages can include document bytes. Do not print them to logs.
        sys.exit(2)
