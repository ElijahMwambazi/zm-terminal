"""Page text from an already-vetted PDF through Poppler's `pdftotext -layout` (no OCR, no shell).

The caller (the source layer) supplies bytes it has already fetched and hash-checked; this module never fetches anything.
The executable is invoked with an argument list (no shell, no interpolation), under a timeout, on a private temporary file
that is always removed. stdout and stderr are drained concurrently from pipes by two reader threads in fixed-size chunks; each
retains at most its cap, and the first stream to exceed its cap kills the child immediately. The OS pipe gives backpressure, so
Python-held output is bounded by stdout cap + stderr cap + a chunk, independent of how much the child tries to write. No OS
resource limit is relied on. Errors never carry PDF text or values.
"""

import os
import subprocess
import tempfile
import threading
from pathlib import Path

EXECUTABLE = "pdftotext"
TIMEOUT_SECONDS = 60.0
MAX_OUTPUT_BYTES = 200_000  # one table page is a few kB
MAX_OUTPUT_CHARS = MAX_OUTPUT_BYTES
MAX_STDERR_BYTES = 64_000
READ_CHUNK_BYTES = 8192
JOIN_SECONDS = 5.0


class LayoutExtractionError(Exception):
    """The page could not be extracted (missing executable, timeout, failure, empty or oversized output)."""


def pdftotext_command(pdf_path: str, page: int, executable: str = EXECUTABLE) -> list[str]:
    """The exact argument vector: layout mode, one page, UTF-8, result on stdout."""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise LayoutExtractionError("PDF page must be a positive integer")
    return [executable, "-layout", "-f", str(page), "-l", str(page), "-enc", "UTF-8", pdf_path, "-"]


class _BoundedReader(threading.Thread):
    """Drains one pipe in fixed chunks, retaining at most `cap` bytes. On the first byte past the cap it sets `overflow`,
    kills the child and stops reading (so total bytes ever read <= cap + one chunk)."""

    def __init__(self, stream, cap: int, process: subprocess.Popen, on_overflow):
        super().__init__(daemon=True)
        self.fd, self.cap, self.process, self.on_overflow = stream.fileno(), cap, process, on_overflow
        self.retained = bytearray()
        self.total_seen = 0
        self.overflow = False

    def run(self) -> None:
        try:
            while True:
                chunk = os.read(self.fd, READ_CHUNK_BYTES)
                if not chunk:
                    return  # EOF
                self.total_seen += len(chunk)
                room = self.cap - len(self.retained)
                if room > 0:
                    self.retained += chunk[:room]
                if self.total_seen > self.cap:
                    self.overflow = True
                    self.on_overflow()
                    return
        except OSError:  # pipe closed underneath us after the child was stopped
            return


def _kill(process: subprocess.Popen) -> None:
    try:
        process.kill()  # no-op once reaped; safe if called from several threads
    except ProcessLookupError:
        pass


def extract_page_layout(
    pdf_bytes: bytes, page: int, *, executable: str = EXECUTABLE, timeout: float = TIMEOUT_SECONDS,
) -> str:
    """Layout-preserving text of one PDF page (1-based)."""
    with tempfile.TemporaryDirectory(prefix="zmt-pdf-") as directory:
        path = Path(directory) / "input.pdf"
        path.write_bytes(pdf_bytes)
        command = pdftotext_command(str(path), page, executable)
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argument list, shell=False
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            )
        except FileNotFoundError as exc:
            raise LayoutExtractionError(
                "pdftotext was not found; install Poppler (for example the poppler-utils package)") from exc
        except OSError as exc:
            raise LayoutExtractionError(f"pdftotext could not be run ({type(exc).__name__})") from exc
        readers: list[_BoundedReader] = []
        timed_out = False
        try:
            kill = lambda: _kill(process)  # noqa: E731
            readers = [_BoundedReader(process.stdout, MAX_OUTPUT_BYTES, process, kill),
                       _BoundedReader(process.stderr, MAX_STDERR_BYTES, process, kill)]
            for reader in readers:
                reader.start()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill(process)
                process.wait()
        finally:
            _kill(process)
            process.wait()  # always reap
            for pipe in (process.stdout, process.stderr):  # unblocks a reader if a grandchild kept a pipe open
                try:
                    pipe.close()
                except OSError:
                    pass
            for reader in readers:
                reader.join(JOIN_SECONDS)
        stdout = bytes(readers[0].retained)
        if any(r.overflow for r in readers):
            raise LayoutExtractionError("pdftotext output is larger than expected for one page")
        if timed_out:
            raise LayoutExtractionError(f"pdftotext timed out after {timeout:g} s")
        if process.returncode != 0:
            raise LayoutExtractionError(f"pdftotext failed (exit status {process.returncode})")
    text = stdout.decode("utf-8", errors="strict") if stdout else ""
    if not text.strip():
        raise LayoutExtractionError("pdftotext produced no text for the pinned page")
    return text
