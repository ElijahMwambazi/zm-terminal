"""Migration 014 lifecycle functions must read the persistent public.energy_* relations whatever the caller does.

The application role keeps PostgreSQL's default TEMP privilege, and pg_temp is searched BEFORE every other schema for an unqualified
relation name, so a session can create TEMP tables named like Energy tables. These tests log in as the real application role and prove
spoofed temporary rows are never consulted. All data is SYNTHETIC. Every statement here is schema-qualified on purpose: an unqualified
INSERT would itself hit the temporary shadow."""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from test_application_role_privileges import env, one  # noqa: F401  (pytest fixture `env`: real application-role login on public)
from test_energy_vintage_schema import NOW, SERIES, sha

M014 = Path(__file__).parents[3] / "db/init/014_energy_generation_vintages.sql"
ENERGY_TABLES = ("energy_generation_group_totals", "energy_generation_observations", "energy_generation_imports",
                 "energy_publication_candidates", "energy_publications", "energy_source_stations")


def shadow(c, *tables):
    for t in tables:
        c.execute(text(f"CREATE TEMP TABLE {t} (LIKE public.{t} INCLUDING DEFAULTS)"))


def candidate(c, n, year=2025):
    return one(c, "INSERT INTO public.energy_publication_candidates (source_series, nominal_publication_year, source_url, source_sha256, "
                  "source_size_bytes, discovered_at) VALUES (:s,:y,:u,:h,1000,:t) RETURNING id",
               s=SERIES, y=year, u=f"https://example.invalid/{n}.pdf", h=sha(n), t=NOW)


def publication(c, n, year=2025, rev=1, prev=None):
    return one(c, "INSERT INTO public.energy_publications (publisher, source_series, publication_year, revision_sequence, publication_status, "
                  "supersedes_publication_id, title, source_url, source_sha256, source_size_bytes, pdf_pages, rights_status, first_retrieved_at) "
                  "VALUES ('ERB',:s,:y,:r,'approved',:p,'Synthetic',:u,:h,1000,10,'review_required',:t) RETURNING id",
               s=SERIES, y=year, r=rev, p=prev, u=f"https://example.invalid/{n}.pdf", h=sha(n), t=NOW)


def real_import(c, pub, seq, n_obs, n_tot, status="current", reason=None):
    return one(c, "INSERT INTO public.energy_generation_imports (publication_id, import_sequence, import_status, expected_observation_count, "
                  "expected_group_total_count, importer_version, correction_reason) VALUES (:p,:q,:st,:o,:t,'test-1',:r) RETURNING id",
               p=pub, q=seq, st=status, o=n_obs, t=n_tot, r=reason)


def accept(c, cand, pub):
    c.execute(text("UPDATE public.energy_publication_candidates SET review_status='accepted', review_note='ok', reviewed_at=:t, "
                   "accepted_publication_id=:p WHERE id=:i"), {"t": NOW, "p": pub, "i": cand})


def observation(c, imp, station, value, year=2020):
    d = Decimal(value)
    c.execute(text("INSERT INTO public.energy_generation_observations (import_id, source_station_id, observation_year, value_state, "
                   "generation_gwh, published_precision, source_table, source_pdf_page, source_printed_page) "
                   "VALUES (:i,:s,:y,'reported',:v,:p,'T',1,1)"), {"i": imp, "s": station, "y": year, "v": d, "p": -d.as_tuple().exponent})


def counts(admin):
    with admin.connect() as c:
        return {t: one(c, f"SELECT COUNT(*) FROM public.{t}") for t in ENERGY_TABLES}


def attempt_commit_fails(app, body, match):
    with app.connect() as c:
        with pytest.raises(IntegrityError, match=match):
            body(c)
            c.commit()  # the deferred lifecycle triggers run here
        c.rollback()


def test_fake_current_import_in_a_temp_table_cannot_satisfy_the_current_import_check(env):  # noqa: F811
    def body(c):
        shadow(c, "energy_generation_imports", "energy_publication_candidates")
        c.commit()
        cand, pub = candidate(c, 1), publication(c, 1)
        accept(c, cand, pub)  # the real candidate link is genuine; only the current import is faked
        c.execute(text("INSERT INTO pg_temp.energy_generation_imports (publication_id, import_sequence, import_status, "
                       "expected_observation_count, expected_group_total_count, importer_version) VALUES (:p,1,'current',0,0,'fake')"), {"p": pub})
    attempt_commit_fails(env.app, body, "exactly one current import")
    assert counts(env.admin) == dict.fromkeys(ENERGY_TABLES, 0)  # nothing survived in the real schema


def test_fake_accepted_candidate_in_a_temp_table_cannot_satisfy_the_candidate_link_check(env):  # noqa: F811
    def body(c):
        shadow(c, "energy_generation_imports", "energy_publication_candidates")
        c.commit()
        pub = publication(c, 2)
        real_import(c, pub, 1, 0, 0)  # a genuine complete current import; only the accepted candidate is faked
        c.execute(text("INSERT INTO pg_temp.energy_publication_candidates (source_series, nominal_publication_year, source_url, source_sha256, "
                       "source_size_bytes, discovered_at, review_status, review_note, reviewed_at, accepted_publication_id) "
                       "VALUES (:s,2025,:u,:h,1000,:t,'accepted','fake',:t,:p)"), {"s": SERIES, "u": "https://example.invalid/2.pdf", "h": sha(2), "t": NOW, "p": pub})
    attempt_commit_fails(env.app, body, "no matching accepted source candidate")
    assert counts(env.admin) == dict.fromkeys(ENERGY_TABLES, 0)


def test_the_real_lifecycle_works_for_the_application_role_while_every_energy_name_is_shadowed(env):  # noqa: F811
    """With empty temp tables named like all six Energy tables present, every lifecycle function still consults the persistent
    relations: candidate link, chain, one-current, counts, import sequence, station/series check, correction swap and revision r2."""
    with env.app.connect() as c:
        shadow(c, *ENERGY_TABLES)
        c.commit()
        station = one(c, "INSERT INTO public.energy_source_stations (source_series, station_group, station_name) "
                         "VALUES (:s,'independent_power_producer','S') RETURNING id", s=SERIES)
        cand, p1 = candidate(c, 1), publication(c, 1)
        i1 = real_import(c, p1, 1, 1, 0)
        observation(c, i1, station, "1.00")
        accept(c, cand, p1)
        c.commit()  # first import: candidate link, chain, one-current and counts all evaluated against the real tables
        i2 = real_import(c, p1, 2, 1, 0, reason="parser fix")  # import_sequence trigger reads the real imports
        observation(c, i2, station, "1.25")  # station/series trigger reads real stations, publications and imports
        c.execute(text("UPDATE public.energy_generation_imports SET import_status='superseded', status_changed_at=:t WHERE id=:i"), {"t": NOW, "i": i1})
        c.commit()  # correction swap
        cand2, p2 = candidate(c, 2), publication(c, 2, rev=2, prev=p1)
        i3 = real_import(c, p2, 1, 1, 0)
        observation(c, i3, station, "2.00")
        accept(c, cand2, p2)
        c.execute(text("UPDATE public.energy_publications SET publication_status='superseded', status_changed_at=:t, status_note='r' WHERE id=:i"), {"t": NOW, "i": p1})
        c.commit()  # same-year revision
        # the temporary shadows stayed empty and played no role
        assert [one(c, f"SELECT COUNT(*) FROM pg_temp.{t}") for t in ENERGY_TABLES] == [0] * 6
        # the views are bound to the persistent tables even though a temp table with a fake row now exists
        c.execute(text("INSERT INTO pg_temp.energy_generation_observations (import_id, source_station_id, observation_year, value_state, generation_gwh, "
                       "published_precision, source_table, source_pdf_page, source_printed_page) VALUES (999,999,2020,'reported',99.9,1,'T',1,1)"))
        rows = c.execute(text("SELECT revision_sequence, generation_gwh FROM public.energy_generation_canonical")).all()
        assert rows == [(2, Decimal("2.00"))]
        c.rollback()
    real = counts(env.admin)
    assert real["energy_publications"] == 2 and real["energy_generation_imports"] == 3 and real["energy_generation_observations"] == 3


def test_a_temp_table_cannot_hide_or_fake_the_import_sequence_or_station_series_checks(env):  # noqa: F811
    with env.app.connect() as c:
        station = one(c, "INSERT INTO public.energy_source_stations (source_series, station_group, station_name) "
                         "VALUES (:s,'zesco_diesel','D') RETURNING id", s=SERIES)
        cand, pub = candidate(c, 1), publication(c, 1)
        i1 = real_import(c, pub, 1, 1, 0)
        observation(c, i1, station, "1.00")
        accept(c, cand, pub)
        c.commit()
        shadow(c, "energy_generation_imports", "energy_publications", "energy_source_stations")
        c.commit()
        # a fake import 5 in the temp table must not make import_sequence 6 look consecutive, nor hide that the real next one is 2
        c.execute(text("INSERT INTO pg_temp.energy_generation_imports (publication_id, import_sequence, import_status, expected_observation_count, "
                       "expected_group_total_count, importer_version) VALUES (:p,5,'current',0,0,'fake')"), {"p": pub})
        with pytest.raises(IntegrityError, match="import_sequence must be 2"):
            real_import(c, pub, 6, 0, 0, status="superseded", reason="skip")
        c.rollback()


def test_energy_functions_are_pinned_security_invoker_and_have_no_unqualified_energy_relations(env):  # noqa: F811
    with env.admin.connect() as c:
        rows = c.execute(text("SELECT proname, prosecdef, proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                              "WHERE n.nspname='public' AND proname LIKE 'energy\\_%'")).all()
    assert len(rows) == 12
    for name, secdef, config in rows:
        assert secdef is False, name  # SECURITY INVOKER: relation binding is by qualification, not by elevated rights
        assert config == ["search_path=pg_catalog, pg_temp"], name  # pg_temp last: never searched first
    # static audit of the migration text: runtime SQL inside every function body names only public.energy_* relations
    source = M014.read_text()
    bodies = re.findall(r"\$\$(.*?)\$\$", source, flags=re.S)
    assert len(bodies) == 12
    for body in bodies:
        code = "\n".join(line.split("--")[0] for line in body.splitlines())
        assert not re.search(r"\b(FROM|JOIN|INTO|UPDATE|DELETE FROM|INSERT INTO)\s+(?!public\.)energy_", code), code
        for table in re.findall(r"\bpublic\.(energy_\w+)", code):
            assert table in ENERGY_TABLES
    assert not any(re.search(r"\b(FROM|JOIN)\s+(?!public\.)energy_", re.sub(r"--.*", "", b)) for b in bodies)
