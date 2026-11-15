"""Page text from an already-vetted PDF through Poppler's `pdftotext -layout` (no OCR, no shell).

The caller (the source layer) supplies bytes it has already fetched and hash-checked; this module never fetches anything.
The executable is invoked with an argument list (no shell, no interpolation), under a timeout, on a private temporary file
that is always removed. Only the single pinned page is extracted. Errors never carry PDF text or values.
"""

import subprocess
import tempfile
from pathlib import Path

EXECUTABLE = "pdftotext"
TIMEOUT_SECONDS = 60.0
MAX_OUTPUT_CHARS = 200_000  # one table page is a few kB


class LayoutExtractionError(Exception):
    """The page could not be extracted (missing executable, timeout, failure, empty or oversized output)."""


def pdftotext_command(pdf_path: str, page: int, executable: str = EXECUTABLE) -> list[str]:
    """The exact argument vector: layout mode, one page, UTF-8, result on stdout."""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise LayoutExtractionError("PDF page must be a positive integer")
    return [executable, "-layout", "-f", str(page), "-l", str(page), "-enc", "UTF-8", pdf_path, "-"]


def extract_page_layout(
    pdf_bytes: bytes, page: int, *, executable: str = EXECUTABLE, timeout: float = TIMEOUT_SECONDS,
) -> str:
    """Layout-preserving text of one PDF page (1-based)."""
    with tempfile.TemporaryDirectory(prefix="zmt-pdf-") as directory:
        path = Path(directory) / "input.pdf"
        path.write_bytes(pdf_bytes)
        command = pdftotext_command(str(path), page, executable)
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argument list, shell=False
                command, capture_output=True, timeout=timeout, shell=False, check=False,
            )
        except FileNotFoundError as exc:
            raise LayoutExtractionError(
                "pdftotext was not found; install Poppler (for example the poppler-utils package)") from exc
        except subprocess.TimeoutExpired as exc:
            raise LayoutExtractionError(f"pdftotext timed out after {timeout:g} s") from exc
        except OSError as exc:
            raise LayoutExtractionError(f"pdftotext could not be run ({type(exc).__name__})") from exc
    if completed.returncode != 0:
        raise LayoutExtractionError(f"pdftotext failed (exit status {completed.returncode})")
    text = completed.stdout.decode("utf-8", errors="strict") if completed.stdout else ""
    if not text.strip():
        raise LayoutExtractionError("pdftotext produced no text for the pinned page")
    if len(text) > MAX_OUTPUT_CHARS:
        raise LayoutExtractionError("pdftotext output is larger than expected for one page")
    return text
