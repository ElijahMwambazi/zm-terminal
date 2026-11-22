"""The opt-in real-source suite's enable/fail gate (temporary directories only; no network, no real PDFs)."""

from app.services import zamstats_cpi_source as src
from zamstats_cpi_real_gate import EMPTY_VALUE, ENV_VAR, gate_state, missing_files

NAMES = [p.filename for p in src.PUBLICATIONS]


def make(directory, names):
    for name in names:
        (directory / name).write_bytes(b"%PDF-")


def test_only_an_unset_variable_skips_the_suite():
    assert gate_state(None, NAMES) == ("skip", [])
    assert ENV_VAR == "ZM_ZAMSTATS_CPI_PDF_DIR"


def test_an_explicitly_empty_variable_is_a_configuration_error_not_unset():
    state, problems = gate_state("", NAMES)
    assert state == "fail" and problems == [EMPTY_VALUE]


def test_a_whitespace_only_variable_is_rejected_not_silently_disabled():
    for value in (" ", "   ", "\t", " \n "):
        assert gate_state(value, NAMES) == ("fail", [EMPTY_VALUE])


def test_env_set_to_a_complete_directory_runs(tmp_path):
    make(tmp_path, NAMES)
    assert gate_state(str(tmp_path), NAMES) == ("run", [])


def test_env_set_to_an_empty_directory_fails_and_lists_every_missing_file(tmp_path):
    state, missing = gate_state(str(tmp_path), NAMES)
    assert state == "fail" and missing == NAMES and len(missing) == 14


def test_env_set_with_one_missing_publication_fails_naming_it(tmp_path):
    make(tmp_path, NAMES[:-1])
    assert gate_state(str(tmp_path), NAMES) == ("fail", [NAMES[-1]])


def test_a_missing_directory_fails(tmp_path):
    state, missing = gate_state(str(tmp_path / "nope"), NAMES)
    assert state == "fail" and missing == ["<directory does not exist>"]


def test_unexpected_extra_files_are_ignored(tmp_path):
    make(tmp_path, NAMES + ["extra.pdf", "notes.txt"])
    assert gate_state(str(tmp_path), NAMES) == ("run", []) and missing_files(str(tmp_path), NAMES) == []


def test_a_directory_with_the_right_name_is_not_a_file(tmp_path):
    make(tmp_path, NAMES[1:])
    (tmp_path / NAMES[0]).mkdir()
    assert gate_state(str(tmp_path), NAMES) == ("fail", [NAMES[0]])
