"""Page text from an already-vetted PDF through Poppler's `pdftotext -layout` (no OCR, no shell).

The caller (the source layer) supplies bytes it has already fetched and hash-checked; this module never fetches anything.
The executable is invoked with an argument list (no shell, no interpolation), under a timeout, on a private temporary file
that is always removed. stdout and stderr go to temporary files (never in-memory pipes) that are size-monitored while the child runs
(and hard-capped by RLIMIT_FSIZE where available); the child is killed and reaped on overflow or timeout. Only the single pinned page is extracted. Errors never carry PDF text or values.
"""

import subprocess
import tempfile
import time
from pathlib import Path

try:  # POSIX only; the polling monitor below is the portable bound
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]

EXECUTABLE = "pdftotext"
TIMEOUT_SECONDS = 60.0
MAX_OUTPUT_BYTES = 200_000  # one table page is a few kB
MAX_OUTPUT_CHARS = MAX_OUTPUT_BYTES
MAX_STDERR_BYTES = 64_000
POLL_SECONDS = 0.02


class LayoutExtractionError(Exception):
    """The page could not be extracted (missing executable, timeout, failure, empty or oversized output)."""


def pdftotext_command(pdf_path: str, page: int, executable: str = EXECUTABLE) -> list[str]:
    """The exact argument vector: layout mode, one page, UTF-8, result on stdout."""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise LayoutExtractionError("PDF page must be a positive integer")
    return [executable, "-layout", "-f", str(page), "-l", str(page), "-enc", "UTF-8", pdf_path, "-"]


def _limit_file_sizes() -> None:  # runs in the child between fork and exec: no file it writes may exceed the cap + 1 byte
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES + 1, MAX_OUTPUT_BYTES + 1))


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _stop(process: subprocess.Popen) -> None:
    process.kill()
    process.wait()  # always reap


def extract_page_layout(
    pdf_bytes: bytes, page: int, *, executable: str = EXECUTABLE, timeout: float = TIMEOUT_SECONDS,
) -> str:
    """Layout-preserving text of one PDF page (1-based)."""
    with tempfile.TemporaryDirectory(prefix="zmt-pdf-") as directory:
        base = Path(directory)
        path, out_path, err_path = base / "input.pdf", base / "stdout.txt", base / "stderr.txt"
        path.write_bytes(pdf_bytes)
        command = pdftotext_command(str(path), page, executable)
        try:
            with out_path.open("wb") as out, err_path.open("wb") as err:
                process = subprocess.Popen(  # noqa: S603 - fixed argument list, shell=False
                    command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, shell=False,
                    preexec_fn=_limit_file_sizes if resource is not None else None,
                )
                deadline = time.monotonic() + timeout
                try:
                    while process.poll() is None:
                        if _size(out_path) > MAX_OUTPUT_BYTES or _size(err_path) > MAX_STDERR_BYTES:
                            _stop(process)
                            raise LayoutExtractionError("pdftotext output is larger than expected for one page")
                        if time.monotonic() >= deadline:
                            _stop(process)
                            raise LayoutExtractionError(f"pdftotext timed out after {timeout:g} s")
                        time.sleep(POLL_SECONDS)
                except BaseException:
                    if process.poll() is None:
                        _stop(process)
                    raise
        except FileNotFoundError as exc:
            raise LayoutExtractionError(
                "pdftotext was not found; install Poppler (for example the poppler-utils package)") from exc
        except OSError as exc:
            raise LayoutExtractionError(f"pdftotext could not be run ({type(exc).__name__})") from exc
        if _size(out_path) > MAX_OUTPUT_BYTES or _size(err_path) > MAX_STDERR_BYTES:  # fast writers between polls
            raise LayoutExtractionError("pdftotext output is larger than expected for one page")
        if process.returncode != 0:
            raise LayoutExtractionError(f"pdftotext failed (exit status {process.returncode})")
        data = out_path.read_bytes()  # size already confirmed within the cap
    text = data.decode("utf-8", errors="strict") if data else ""
    if not text.strip():
        raise LayoutExtractionError("pdftotext produced no text for the pinned page")
    return text
