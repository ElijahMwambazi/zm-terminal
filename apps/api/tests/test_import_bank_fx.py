"""Manual exact-date Bank FX importer: SYNTHETIC documents, all HTTP mocked (no live request), storage mocked unless a test says otherwise."""

import json
from datetime import date

import httpx
import pytest

import bank_fx_builders as b
from app.jobs import import_bank_fx as job
from app.services.bank_fx_models import BankFxError
from app.services.bank_fx_store import BankFxConflictError, BankFxSaveResult

DAY = "2030-01-02"
OTHER_UUID = "00000000-0000-4000-8000-0000000000a2"


class Body(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw = raw

    async def __aiter__(self):
        yield self.raw


class Harness:
    def __init__(self, monkeypatch, handler):
        self.requests, self.saved = [], []
        self.save_result = BankFxSaveResult(inserted=1, already_present=0)

        def wrapped(request):
            self.requests.append(request)
            return handler(request)

        monkeypatch.setattr(job, "new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(wrapped), follow_redirects=False))

        def save(snapshots, *, engine=None):
            self.saved.append(list(snapshots))
            if isinstance(self.save_result, Exception):
                raise self.save_result
            return self.save_result

        monkeypatch.setattr(job, "save_bank_fx_snapshots", save)


def serve(doc, status=200):
    return lambda request: httpx.Response(status, headers={"content-type": "application/vnd.api+json"}, stream=Body(json.dumps(doc).encode()))


def run(monkeypatch, capsys, handler, *args):
    h = Harness(monkeypatch, handler)
    code = job.main(list(args))
    out = capsys.readouterr()
    return h, code, out.out, out.err


def test_date_is_required_and_strict(monkeypatch, capsys):
    for args in ([], ["--date"], ["--date", "2030-1-2"], ["--date", "2030-02-30"], ["--date", "20300102"], ["--date", "2030-01-02T00:00:00"],
                  ["--date", " 2030-01-02"], ["--date", "latest"], ["--date", "2030-13-01"]):
        h = Harness(monkeypatch, serve(b.document(b.node())))
        with pytest.raises(SystemExit) as exc:
            job.main(args)
        assert exc.value.code == 2 and h.requests == [] and h.saved == []  # rejected before any network or database use
    capsys.readouterr()


def test_no_range_backfill_or_fallback_options_exist():
    for flag in ("--from", "--to", "--range", "--backfill", "--latest", "--days"):
        with pytest.raises(SystemExit):
            job.main(["--date", DAY, flag, "x"])
    source = open(job.__file__).read()
    assert "timedelta" not in source and "weekday" not in source


def test_one_node_import_makes_exactly_one_request_and_one_save(monkeypatch, capsys):
    h, code, out, _ = run(monkeypatch, capsys, serve(b.document(b.node())), "--date", DAY)
    assert code == 0 and len(h.requests) == 1 and len(h.saved) == 1 and len(h.saved[0]) == 1
    assert "2030-01-02T00:00:00" in str(h.requests[0].url) and "2030-01-02T23:59:59" in str(h.requests[0].url)
    assert out.strip() == "bank_fx_import date=2030-01-02 mode=import snapshots=1 rate_rows=9 layouts=v2 no_publication=0 inserted=1 already_present=0"


def test_progressive_multi_node_batch_is_saved_once_completely(monkeypatch, capsys):
    doc = b.document(b.node(), b.node(uuid=OTHER_UUID, nid=12, vid=23, layout="v1"))
    h, code, out, _ = run(monkeypatch, capsys, serve(doc), "--date", DAY)
    assert code == 0 and len(h.requests) == 1 and len(h.saved) == 1 and len(h.saved[0]) == 2
    assert "snapshots=2 rate_rows=18 layouts=v1,v2" in out


def test_empty_data_is_no_publication_writes_nothing_and_never_falls_back(monkeypatch, capsys):
    h, code, out, _ = run(monkeypatch, capsys, serve(b.document()), "--date", DAY)
    assert code == 0 and len(h.requests) == 1 and h.saved == []
    assert "no_publication=1" in out and "snapshots=0" in out and "inserted" not in out


def test_dry_run_requests_and_validates_but_never_touches_the_database(monkeypatch, capsys):
    def no_engine():
        raise AssertionError("dry run opened PostgreSQL")

    monkeypatch.setattr("app.db.get_engine", no_engine)
    h, code, out, _ = run(monkeypatch, capsys, serve(b.document(b.node())), "--date", DAY, "--dry-run")
    assert code == 0 and len(h.requests) == 1 and h.saved == []
    assert "mode=dry-run" in out and "stored_conflicts=not_checked" in out and "inserted" not in out
    assert "never opens postgresql" in job.__doc__.lower() and "cannot detect" in job.__doc__


def test_dry_run_still_fails_on_invalid_source(monkeypatch, capsys):
    h, code, _, err = run(monkeypatch, capsys, serve(b.cell(b.document(b.node()), 3, 1, "10")), "--date", DAY, "--dry-run")
    assert code == 1 and h.saved == [] and "import failed" in err


def test_stored_conflict_exits_one(monkeypatch, capsys):
    h = Harness(monkeypatch, serve(b.document(b.node())))
    h.save_result = BankFxConflictError("stored Bank FX node x revision 22 differs")
    code = job.main(["--date", DAY])
    err = capsys.readouterr().err
    assert code == 1 and "differs" in err and len(h.saved) == 1


@pytest.mark.parametrize("doc", [
    b.cell(b.document(b.node()), 3, 1, "10.00001"),
    b.document(b.node(), next_link="https://example.test/next"),
    b.document(b.node(rates_date="2030-01-03T10:00:00+02:00")),
    {"data": {"not": "a list"}},
])
def test_parser_failures_exit_one_and_save_nothing(monkeypatch, capsys, doc):
    h, code, out, err = run(monkeypatch, capsys, serve(doc), "--date", DAY)
    assert code == 1 and h.saved == [] and out == "" and "import failed" in err and len(h.requests) == 1


@pytest.mark.parametrize("status", [404, 429, 500, 301])
def test_http_failure_exits_one_without_retry_or_save(monkeypatch, capsys, status):
    h, code, _, err = run(monkeypatch, capsys, lambda r: httpx.Response(status, headers={"location": "https://example.test/"}), "--date", DAY)
    assert code == 1 and len(h.requests) == 1 and h.saved == [] and "import failed" in err


def test_transport_error_is_generic(monkeypatch, capsys):
    def boom(request):
        raise httpx.ConnectError("secret-host.internal refused")

    h, code, _, err = run(monkeypatch, capsys, boom, "--date", DAY)
    assert code == 1 and len(h.requests) == 1 and "secret-host" not in err


def test_database_error_is_reported_by_type_only(monkeypatch, capsys):
    h = Harness(monkeypatch, serve(b.document(b.node())))
    h.save_result = RuntimeError("postgresql://user:hunter2@db/zm_terminal exploded")
    assert job.main(["--date", DAY]) == 1
    err = capsys.readouterr().err
    assert "hunter2" not in err and "postgresql://" not in err and "RuntimeError" in err


def test_output_has_counts_only(monkeypatch, capsys):
    doc = b.document(b.node(), b.node(uuid=OTHER_UUID, nid=12, vid=23))
    h, code, out, err = run(monkeypatch, capsys, serve(doc), "--date", DAY)
    text = out + err
    for forbidden in ("10.0001", "ALPHA", "BETA", "MARKET", OTHER_UUID, b.NODE_UUID, "postgresql", "password", "{", "https://"):
        assert forbidden not in text
    assert out.count("\n") == 1


def test_run_import_returns_counts_for_a_supplied_client(monkeypatch):
    h = Harness(monkeypatch, serve(b.document(b.node())))
    result = job.run_import(date(2030, 1, 2), dry_run=True)
    assert result["snapshots"] == 1 and len(h.requests) == 1
