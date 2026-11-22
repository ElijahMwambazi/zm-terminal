"""Gate logic of the opt-in real-source fidelity suite (importable and testable on its own; no network, no PDF content).

Environment variable unset  -> "skip": the suite is not enabled.
Environment variable set    -> the suite is explicitly enabled and must find the COMPLETE manifest PDF set; any missing file
                               (or a missing directory) is "fail", never a silent skip. Unexpected extra files are ignored.
"""

from pathlib import Path

ENV_VAR = "ZM_ZAMSTATS_CPI_PDF_DIR"


def missing_files(directory: str, filenames) -> list[str]:
    folder = Path(directory)
    if not folder.is_dir():
        return ["<directory does not exist>"]
    return [name for name in filenames if not (folder / name).is_file()]


def gate_state(env_value: str | None, filenames) -> tuple[str, list[str]]:
    """("skip" | "run" | "fail", missing file names)."""
    if not env_value:
        return "skip", []
    missing = missing_files(env_value, filenames)
    return ("fail" if missing else "run"), missing
