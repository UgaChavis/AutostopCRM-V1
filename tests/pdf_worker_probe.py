"""Run the real worker/Qt PDF path outside the shared unittest Qt lifetime."""

from __future__ import annotations

import json
import sys
import threading
import traceback
from pathlib import Path

WORKER_HTML = """
<!doctype html>
<html>
  <head><style>body { color: red; }</style></head>
  <body><h1>Счет на оплату</h1><p>Владимир Регин</p></body>
</html>
"""


def main() -> int:
    from tests.source_path_support import ensure_source_path

    ensure_source_path()
    from minimal_kanban.printing import pdf

    result: dict[str, bytes] = {}
    errors: list[str] = []

    def render() -> None:
        try:
            # A plain-text fallback must not hide a broken native Qt path.
            result["pdf"] = pdf.render_html_to_pdf_bytes(WORKER_HTML)
        except Exception:
            errors.append(traceback.format_exc())

    worker = threading.Thread(target=render, name="native-pdf-worker")
    worker.start()
    # The production subprocess owns its 45-second deadline. The parent test
    # bounds this entire isolated probe, including startup and PDF inspection.
    worker.join()
    if errors:
        sys.stderr.write("\n".join(errors))
        return 1
    output = Path(sys.argv[1])
    output.write_bytes(result["pdf"])

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtPdf import QPdfDocument

    app = QCoreApplication.instance() or QCoreApplication([])
    document = QPdfDocument()
    document.load(str(output))
    if document.status() != QPdfDocument.Status.Ready:
        raise RuntimeError(f"Native PDF could not be loaded: {document.status()}")
    report = {
        "pages": document.pageCount(),
        "text": "\n".join(document.getAllText(page).text() for page in range(document.pageCount())),
        "source_file": str(Path(pdf.__file__).resolve()),
    }
    document.close()
    app.processEvents()
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
