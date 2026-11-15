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
import time
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


_read = os.read  # indirection so tests can inject read failures and delays


class _BoundedReader(threading.Thread):
    """Drains one pipe in fixed chunks, retaining at most `cap` bytes. Terminal `state`: 'eof' (normal), 'overflow' (first
    byte past the cap: stops reading, so total bytes read <= cap + one chunk) or 'failed' (unexpected exception, kept in
    `error` for diagnosis only). Overflow and failure kill the child through `on_alert`. A read error is benign only when the
    coordinator has announced an intentional termination (`terminating`)."""

    def __init__(self, stream, cap: int, on_alert, terminating: threading.Event):
        super().__init__(daemon=True)
        self.fd, self.cap, self.on_alert, self.terminating = stream.fileno(), cap, on_alert, terminating
        self.retained = bytearray()
        self.total_seen = 0
        self.state: str | None = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            while True:
                chunk = _read(self.fd, READ_CHUNK_BYTES)
                if not chunk:
                    self.state = "eof"
                    return
                self.total_seen += len(chunk)
                room = self.cap - len(self.retained)
                if room > 0:
                    self.retained += chunk[:room]
                if self.total_seen > self.cap:
                    self.state = "overflow"
                    self.on_alert()
                    return
        except OSError as exc:
            self.error = exc
            if self.terminating.is_set():
                self.state = "closed"  # intentional termination closed the stream under us (kill/timeout path)
                return
            self.state = "failed"
            self.on_alert()
        except Exception as exc:  # unexpected reader bug: never hidden
            self.error, self.state = exc, "failed"
            self.on_alert()

    @property
    def overflow(self) -> bool:
        return self.state == "overflow"


def _kill(process: subprocess.Popen) -> None:
    try:
        process.kill()  # no-op once reaped; safe if called from several threads
    except ProcessLookupError:
        pass


def _close(pipe) -> None:
    try:
        pipe.close()
    except OSError:
        pass


def _spawn(command: list[str]) -> subprocess.Popen:
    try:
        return subprocess.Popen(  # noqa: S603 - fixed argument list, shell=False
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        )
    except FileNotFoundError as exc:
        raise LayoutExtractionError(
            "pdftotext was not found; install Poppler (for example the poppler-utils package)") from exc
    except OSError as exc:
        raise LayoutExtractionError(f"pdftotext could not be run ({type(exc).__name__})") from exc


def _run_and_drain(command: list[str], timeout: float) -> tuple[subprocess.Popen, list[_BoundedReader], bool, bool]:
    """Run the child and drain both pipes to EOF. Returns (process, readers, timed_out, readers_stuck); the process is always
    reaped and every reader joined (a stuck one is reported, never ignored). Pipes are closed only after the readers finish."""
    process = _spawn(command)
    terminating = threading.Event()
    kill = lambda: _kill(process)  # noqa: E731
    readers = [_BoundedReader(process.stdout, MAX_OUTPUT_BYTES, kill, terminating),
               _BoundedReader(process.stderr, MAX_STDERR_BYTES, kill, terminating)]
    timed_out = stuck = False
    try:
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)  # readers kill the child themselves on overflow/failure, which ends this wait
        except subprocess.TimeoutExpired:
            timed_out = True
            terminating.set()
            _kill(process)
    except BaseException:
        terminating.set()
        raise
    finally:
        _kill(process)
        process.wait()  # always reap; child exit does NOT mean the pipes are drained, so the readers continue to EOF
        deadline = time.monotonic() + JOIN_SECONDS
        for reader in readers:
            if reader.ident is not None:
                reader.join(max(0.0, deadline - time.monotonic()))
        stuck = any(r.is_alive() for r in readers)
        if stuck:  # strictly-necessary recovery: force the pipes closed so the threads can end; extraction fails regardless
            terminating.set()
            for pipe in (process.stdout, process.stderr):
                _close(pipe)
            for reader in readers:
                reader.join(1.0)
        else:
            for pipe in (process.stdout, process.stderr):
                _close(pipe)
    return process, readers, timed_out, stuck


def extract_page_layout(
    pdf_bytes: bytes, page: int, *, executable: str = EXECUTABLE, timeout: float = TIMEOUT_SECONDS,
) -> str:
    """Layout-preserving text of one PDF page (1-based). Succeeds only if the child exited 0, both readers reached EOF
    (no overflow, no failure, all threads finished) and stdout is non-empty."""
    with tempfile.TemporaryDirectory(prefix="zmt-pdf-") as directory:
        path = Path(directory) / "input.pdf"
        path.write_bytes(pdf_bytes)
        process, readers, timed_out, stuck = _run_and_drain(pdftotext_command(str(path), page, executable), timeout)
    out, err = readers
    if out.state == "overflow" or err.state == "overflow":
        raise LayoutExtractionError("pdftotext output is larger than expected for one page")
    if out.state == "failed" or err.state == "failed":
        raise LayoutExtractionError("pdftotext output reader failed")
    if timed_out:
        raise LayoutExtractionError(f"pdftotext timed out after {timeout:g} s")
    if stuck or out.state != "eof" or err.state != "eof":
        raise LayoutExtractionError("pdftotext output could not be fully read")
    if process.returncode != 0:
        raise LayoutExtractionError(f"pdftotext failed (exit status {process.returncode})")
    text = bytes(out.retained).decode("utf-8", errors="strict") if out.retained else ""
    if not text.strip():
        raise LayoutExtractionError("pdftotext produced no text for the pinned page")
    return text
