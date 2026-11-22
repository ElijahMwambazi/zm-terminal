"""Gate logic of the opt-in real-source fidelity suite (importable and testable on its own; no network, no PDF content).

Environment variable UNSET (None)      -> "skip": the suite is not enabled.
Environment variable SET (any string)  -> the suite is explicitly enabled and must find the COMPLETE manifest PDF set. An EMPTY or
                                          whitespace-only value is a configuration error and "fail"s (it is never treated as unset);
                                          a missing directory or any missing file is "fail", never a silent skip. Unexpected extra
                                          files are ignored.
"""

from pathlib import Path

ENV_VAR = "ZM_ZAMSTATS_CPI_PDF_DIR"


def missing_files(directory: str, filenames) -> list[str]:
    folder = Path(directory)
    if not folder.is_dir():
        return ["<directory does not exist>"]
    return [name for name in filenames if not (folder / name).is_file()]


EMPTY_VALUE = "<empty value: set ZM_ZAMSTATS_CPI_PDF_DIR to a directory or unset it>"


def gate_state(env_value: str | None, filenames) -> tuple[str, list[str]]:
    """("skip" | "run" | "fail", problems). None (unset) is the only way to disable the suite."""
    if env_value is None:
        return "skip", []
    if env_value.strip() == "":
        return "fail", [EMPTY_VALUE]
    missing = missing_files(env_value, filenames)
    return ("fail" if missing else "run"), missing
