"""Sandboxed PDF worker: every PDF the tutor opens is parsed here, in a separate
process under a macOS sandbox profile (no network, no reading your home folder
except the PDF itself and the tutor's own code, writes only to a scratch folder),
with CPU and wall-clock limits. A malformed or malicious PDF that exploited a
parser bug would be stuck in here instead of inside the backend.

  python -m magnus_tutor.ingest.pdfworker extract  <pdf> <outdir> <markdown 0|1>
  python -m magnus_tutor.ingest.pdfworker render   <pdf> <outdir> <zoom> <i,j,...> [highlight text]
  python -m magnus_tutor.ingest.pdfworker sections <pdf> <outdir>
  python -m magnus_tutor.ingest.pdfworker text     <pdf> <outdir>

Results go to files in <outdir> (JSON or PNG); progress lines go to stdout.
"""

from __future__ import annotations

import json
import resource
import sys
from dataclasses import asdict
from pathlib import Path


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main(argv: list[str]) -> int:
    _limits()
    from . import pdf

    cmd, src, out = argv[0], Path(argv[1]), Path(argv[2])
    if cmd == "extract":
        def progress(x: float) -> None:
            print(f"PROGRESS {x:.3f}", flush=True)

        pages, secs, title = pdf._extract_inprocess(src, progress, argv[3] == "1")
        data = {
            "title": title,
            "sections": [asdict(s) for s in secs],
            "pages": [{"index": p.index, "printed": p.printed, "text": p.text, "has_text": p.has_text, "gaps": p.gaps,
                       "plain": p.extra.get("plain", "")} for p in pages],
        }
        (out / "result.json").write_text(json.dumps(data))
    elif cmd == "render":
        zoom = float(argv[3])
        indices = [int(i) for i in argv[4].split(",") if i != ""]
        highlight = argv[5] if len(argv) > 5 else None
        with pdf.open_pdf(src) as doc:
            for i in indices:
                (out / f"{i}.png").write_bytes(pdf._render(doc[i], zoom, highlight))
    elif cmd == "sections":
        with pdf.open_pdf(src) as doc:
            (out / "result.json").write_text(json.dumps({"sections": [asdict(s) for s in pdf.sections(doc)], "pages": doc.page_count}))
    elif cmd == "text":
        with pdf.open_pdf(src) as doc:
            (out / "result.json").write_text(json.dumps({"pages": [pg.get_text() for pg in doc]}))
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
