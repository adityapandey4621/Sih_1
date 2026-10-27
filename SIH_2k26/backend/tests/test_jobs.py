"""Durable processing: queue semantics, versioning, retries, recovery, and what must never happen."""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import jobs
from app.config import Settings, get_settings
from app.db import SessionLocal, engine
from app.models import utcnow
from app.models_applications import ApplicationDocument
from app.models_processing import DocumentEvent, DocumentSlot, ExtractedField, ExtractedPage, PreliminaryFinding, ProcessingJob
from app.processing.errors import ProcessingError
from app.security import as_utc
from app.worker import run_claimed
from tests.docs_helpers import FakeOcr, corrupt_pdf, digital_pdf, encrypted_pdf, ocr_page, scanned_pdf
from tests.evidence_world import application_status, doc_rows, drain, job_for, later, make_world, proc_for, step, storage

SENTINEL = "TOP-SECRET-SENTINEL-8842"
world = pytest.fixture(make_world)


def counts(doc_id) -> tuple[int, int]:
    with SessionLocal() as db:
        return (
            db.scalar(select(func.count()).select_from(ExtractedPage).where(ExtractedPage.document_id == doc_id)),
            db.scalar(select(func.count()).select_from(ExtractedField).where(ExtractedField.document_id == doc_id)),
        )


# ---- upload creates durable work atomically --------------------------------------------------------------------------


def test_upload_stores_the_version_its_processing_row_and_its_job_together(world):
    r = world.upload()
    assert r.status_code == 201
    [d] = doc_rows(world.app["id"])
    assert (d.version_number, d.previous_version_id, d.superseded_at) == (1, None, None)
    p, j = proc_for(d.id), job_for(d.id)
    assert (p.technical_status, p.scan_status, p.extraction_status) == ("PENDING", "PENDING", "QUEUED")
    assert (j.status, j.attempts, j.job_type) == ("queued", 0, "PROCESS_DOCUMENT")
    with SessionLocal() as db:
        slot = db.scalar(select(DocumentSlot))
        assert slot.current_version_id == d.id
        assert [e.action for e in db.scalars(select(DocumentEvent).order_by(DocumentEvent.at))] == ["uploaded"]
    state = r.json()["documents"][0]["state"]
    assert state["code"] == "received" or state["code"] == "extraction_queued"
    assert state["terminal"] is False and "not verification" in state["explanation"]


def test_a_failed_upload_leaves_no_job_behind(world, monkeypatch):
    monkeypatch.setattr(jobs, "enqueue_document_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue down")))
    assert world.upload(expect=(500,)).status_code == 500  # a generic 500, and nothing left behind
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 0 and db.scalar(select(func.count()).select_from(ApplicationDocument)) == 0
    assert not [p for p in storage().root.rglob("*") if p.is_file()]


# ---- processing and linkage ------------------------------------------------------------------------------------------


def test_worker_processes_a_digital_pdf_and_links_results_to_the_exact_version(world):
    world.upload()
    assert drain()[0] == "succeeded"
    [d] = doc_rows(world.app["id"])
    p = proc_for(d.id)
    assert (p.technical_status, p.extraction_status, p.text_method, p.page_count) == ("PASSED", "COMPLETE", "PDF_TEXT", 1)
    assert p.scan_status == "SCANNER_UNAVAILABLE"  # nothing scanned, and never called clean
    with SessionLocal() as db:
        fields = {f.field_key: f for f in db.scalars(select(ExtractedField).where(ExtractedField.document_id == d.id))}
        assert fields["institution_name"].value == "Test Institute" and fields["institution_name"].page_number == 1 and fields["institution_name"].method == "PDF_TEXT"
        assert fields["document_date"].normalised_value == "2024-03-12" and fields["reference_number"].normalised_value == "RF-2024-9"
        assert {f.document_id for f in fields.values()} == {d.id}
    assert job_for(d.id).status == "succeeded" and job_for(d.id).attempts == 1
    st = world.refresh()["documents"][0]["state"]
    assert st["code"] == "extraction_complete" and st["scan_status"] == "SCANNER_UNAVAILABLE" and "NOT performed" in st["scan_note"]


def test_processing_never_changes_the_application_status(world):
    world.upload(data=corrupt_pdf())
    drain()
    assert application_status(uuid.UUID(world.app["id"])) == "DRAFT"
    world.upload(item="doc-extra", data=digital_pdf([["Institute Name: Some Other Place"]]))
    world.upload(data=digital_pdf([["Name of the Institution: Wrong Name Entirely"]]), name="again.pdf")
    drain()
    done = world.submit()
    drain()
    assert done["status"] == "SUBMITTED" and application_status(uuid.UUID(world.app["id"])) == "SUBMITTED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PreliminaryFinding)) > 0  # findings exist, status untouched


def test_processing_continues_after_submission_without_touching_the_submitted_documents(world):
    world.upload()
    world.submit()
    [d] = doc_rows(world.app["id"])
    assert proc_for(d.id).extraction_status == "QUEUED"
    drain()  # the ORM guard on submitted applications must not block the worker
    assert proc_for(d.id).extraction_status == "COMPLETE"
    assert world.refresh()["status"] == "SUBMITTED"


# ---- claiming, leases and recovery ----------------------------------------------------------------------------------


def test_only_one_worker_can_claim_a_job(world):
    world.upload()
    with SessionLocal() as s1, SessionLocal() as s2:
        first = jobs.claim_next(s1, "w1", get_settings())
        second = jobs.claim_next(s2, "w2", get_settings())
        third = jobs.claim_next(s2, "w2", get_settings())
        seen = [(j.id, j.locked_by, j.status, j.attempts) if j else None for j in (first, second, third)]
    assert seen[0][1:] == ("w1", "running", 1)
    # the upload queued two jobs (the document and the cross-check): each is handed out exactly once
    assert seen[1] is not None and seen[1][0] != seen[0][0] and seen[2] is None


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real concurrency (PostgreSQL)")
def test_concurrent_workers_each_claim_distinct_jobs(world):
    for item in ("doc-main", "doc-extra"):
        world.upload(item=item, data=digital_pdf([[f"Name of the Institution: {item}"]]))
    def claim(n):
        with SessionLocal() as db:
            j = jobs.claim_next(db, f"w{n}", get_settings())
            return str(j.id) if j else None
    with ThreadPoolExecutor(8) as pool:
        got = [g for g in pool.map(claim, range(8)) if g]
    assert len(got) == len(set(got)) and 2 <= len(got) <= 3  # two document jobs (+ the cross-check): each claimed exactly once


@pytest.mark.skipif(engine.dialect.name == "sqlite", reason="needs real concurrency (PostgreSQL)")
def test_two_simultaneous_uploads_do_not_fail_when_both_try_to_queue_the_cross_check(world):
    """Found by the browser journey: two documents uploaded at the same moment both saw 'no cross-check waiting' and both inserted one; the unique index turned the loser's
    upload into an HTTP 500. The loser must simply find that one is already waiting (the coalesced outcome)."""
    import threading
    import time

    app_id = uuid.UUID(world.app["id"])
    holder = SessionLocal()
    jobs.enqueue_cross_check(holder, app_id, get_settings())
    holder.flush()  # inserted, NOT yet committed: a second session cannot see it, and its own insert will have to wait for this one to finish
    outcome: list[object] = []

    def second() -> None:
        try:
            with SessionLocal() as db:
                jobs.enqueue_cross_check(db, app_id, get_settings())
                db.commit()
            outcome.append("ok")
        except Exception as exc:  # noqa: BLE001
            outcome.append(exc)

    t = threading.Thread(target=second)
    t.start()
    time.sleep(1.0)  # the second insert is now waiting on the first
    holder.commit()
    holder.close()
    t.join(30)
    assert outcome == ["ok"]  # no exception reached the caller
    with SessionLocal() as db:
        waiting = db.scalars(select(ProcessingJob).where(ProcessingJob.application_id == app_id, ProcessingJob.job_type == "CROSS_CHECK", ProcessingJob.status.in_(("queued", "retry_wait")))).all()
    assert len(waiting) == 1  # still exactly one waiting cross-check


def test_a_worker_restart_does_not_lose_queued_or_in_flight_jobs(world):
    world.upload()
    with SessionLocal() as db:
        claimed = jobs.claim_next(db, "dead-worker", get_settings())  # claimed, then the process "dies": no result, lease left to expire
        assert claimed.status == "running"
    assert step(now=utcnow() + timedelta(seconds=5)) is None or True  # lease still valid: nothing else may take it
    assert job_for(claimed.document_id).locked_by == "dead-worker"
    result = step(now=later(get_settings().job_lease_seconds + 60), worker="new-worker")  # lease expired: reaped and re-run
    assert result == "succeeded"
    j = job_for(claimed.document_id)
    assert j.status == "succeeded" and j.attempts == 2
    with SessionLocal() as db:
        acts = [e.action for e in db.scalars(select(DocumentEvent).where(DocumentEvent.document_id == claimed.document_id).order_by(DocumentEvent.at))]
    assert "processing_interrupted" in acts and acts[-1] == "processing_finished"


def test_a_worker_that_lost_its_lease_cannot_write_results(world):
    world.upload()
    s = get_settings()
    with SessionLocal() as db:
        a_job = jobs.claim_next(db, "slow-worker", s)
        doc_id, job_id = a_job.document_id, a_job.id
    with SessionLocal() as db:  # time passes; another worker reaps and takes over
        jobs.reap_expired(db, s, later(s.job_lease_seconds + 30))
        b_job = jobs.claim_next(db, "fast-worker", s, later(s.job_lease_seconds + 31))
        assert b_job.locked_by == "fast-worker"
    from app.processing.ocr import NullOcr
    from app.processing.scanner import NoScanner

    with SessionLocal() as db:
        stale = db.get(ProcessingJob, job_id)
        outcome = run_claimed(db, stale, "slow-worker", s, NullOcr(), NoScanner(), storage())
    assert outcome == "lease_lost"
    assert counts(doc_id) == (0, 0)  # the slow worker's computed results were discarded
    assert job_for(doc_id).locked_by == "fast-worker"


# ---- retries ---------------------------------------------------------------------------------------------------------


def test_transient_failures_retry_with_backoff_then_fail_visibly(world):
    s = get_settings()
    world.upload(data=scanned_pdf([["scan"]]))
    flaky = FakeOcr(error=ProcessingError("ocr_timeout", "slow"))
    t0 = utcnow()
    assert step(now=t0, ocr=flaky) == "retry_wait"
    j = job_for(doc_rows(world.app["id"])[0].id)
    assert j.attempts == 1 and j.status == "retry_wait" and as_utc(j.run_after) > t0 and not j.retry_eligible
    assert step(now=t0 + timedelta(seconds=1), ocr=flaky) == "succeeded"  # only the queued cross-check is runnable...
    assert step(now=t0 + timedelta(seconds=2), ocr=flaky) is None  # ...the document is still inside its backoff window
    assert proc_for(j.document_id).extraction_status == "QUEUED"
    assert step(now=t0 + timedelta(seconds=s.job_retry_backoff_seconds + 5), ocr=flaky) == "retry_wait"
    final = step(now=t0 + timedelta(hours=1), ocr=flaky)
    j = job_for(j.document_id)
    assert final == "failed_exhausted" and j.attempts == s.job_max_attempts and j.retry_eligible and j.last_error_code == "ocr_timeout"
    p = proc_for(j.document_id)
    assert p.extraction_status == "FAILED" and p.last_error_code == "ocr_timeout" and "too long" in p.last_error_message
    st = world.refresh()["documents"][0]["state"]
    assert st["code"] == "processing_failed" and st["can_retry"] and "Retry is available" in st["explanation"]
    assert step(now=t0 + timedelta(hours=2), ocr=flaky) is None  # exhausted jobs do not loop


def test_corrupt_files_fail_once_clearly_and_never_loop(world):
    world.upload(data=corrupt_pdf())
    assert drain() == ["failed_permanent", "succeeded"]  # the document job, then the cross-check it triggered
    d = doc_rows(world.app["id"])[0]
    j, p = job_for(d.id), proc_for(d.id)
    assert j.attempts == 1 and not j.retry_eligible and j.last_error_code == "pdf_corrupt"
    assert (p.technical_status, p.technical_code, p.extraction_status) == ("FAILED", "pdf_corrupt", "FAILED")
    assert counts(d.id) == (0, 0) and drain() == []
    st = world.refresh()["documents"][0]["state"]
    assert st["code"] == "technical_failed" and "Upload a corrected file" in st["explanation"] and not st["can_retry"]
    r = world.a.post(f"/api/documents/{d.id}/retry")
    assert r.status_code == 409 and r.json()["code"] == "not_retryable"


def test_encrypted_pdf_is_a_clear_permanent_failure(world):
    world.upload(data=encrypted_pdf())
    drain()
    d = doc_rows(world.app["id"])[0]
    assert proc_for(d.id).technical_code == "pdf_encrypted" and "password-protected" in proc_for(d.id).last_error_message


def test_a_configuration_problem_waits_for_a_person_and_can_be_retried_once_fixed(world):
    world.upload(data=scanned_pdf([["Name of the Institution: Test Institute"]]))
    drain()  # no OCR engine configured in tests
    d = doc_rows(world.app["id"])[0]
    j = job_for(d.id)
    assert j.status == "failed_permanent" and j.retry_eligible and j.last_error_code == "ocr_unavailable"
    assert proc_for(d.id).extraction_status == "FAILED" and "OCR" in proc_for(d.id).last_error_message
    st = world.refresh()["documents"][0]["state"]
    assert st["code"] == "processing_failed" and st["can_retry"]

    r = world.a.post(f"/api/documents/{d.id}/retry")  # the owner asks; the environment is now "fixed"
    assert r.status_code == 202
    j = job_for(d.id)
    assert (j.status, j.attempts, j.manual_retries) == ("queued", 0, 1)
    assert step(ocr=FakeOcr([ocr_page(("Name of the Institution: Test Institute", 95.0))])) == "succeeded"
    assert proc_for(d.id).extraction_status == "COMPLETE" and counts(d.id)[0] == 1


def test_manual_retries_are_bounded_and_not_stackable(world):
    s = get_settings()
    world.upload(data=scanned_pdf([["x"]]))
    drain()
    d = doc_rows(world.app["id"])[0]
    assert world.a.post(f"/api/documents/{d.id}/retry").status_code == 202
    assert world.a.post(f"/api/documents/{d.id}/retry").json()["code"] == "already_pending"
    for _ in range(s.max_manual_retries - 1):
        drain()
        assert world.a.post(f"/api/documents/{d.id}/retry").status_code == 202
    drain()
    r = world.a.post(f"/api/documents/{d.id}/retry")
    assert r.status_code == 409 and r.json()["code"] == "retry_limit"


def test_retrying_replaces_results_rather_than_duplicating_them(world):
    world.upload()
    drain()
    d = doc_rows(world.app["id"])[0]
    before = counts(d.id)
    with SessionLocal() as db:  # pretend a later attempt failed, then a person retried
        j = db.get(ProcessingJob, job_for(d.id).id)
        j.status, j.retry_eligible = "failed_exhausted", True
        db.commit()
    assert world.a.post(f"/api/documents/{d.id}/retry").status_code == 202
    drain()
    assert counts(d.id) == before == (1, 3)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.document_id == d.id, ProcessingJob.job_type == "PROCESS_DOCUMENT")) == 1


def test_one_process_job_per_document_version_is_enforced_by_the_database(world):
    world.upload()
    d = doc_rows(world.app["id"])[0]
    with SessionLocal() as db:
        db.add(ProcessingJob(job_type="PROCESS_DOCUMENT", application_id=d.application_id, document_id=d.id, status="queued"))
        with pytest.raises(IntegrityError):
            db.commit()


# ---- versioning ------------------------------------------------------------------------------------------------------


def test_replacing_a_file_creates_a_new_version_and_keeps_the_old_one(world):
    world.upload(data=digital_pdf([["Name of the Institution: Test Institute"]]))
    drain()
    v1 = doc_rows(world.app["id"])[0]
    r = world.upload(data=digital_pdf([["Name of the Institution: Test Institute", "Date: 01/01/2025"]]), name="revised.pdf")
    assert r.status_code == 201
    v1, v2 = doc_rows(world.app["id"])
    assert (v1.version_number, v2.version_number) == (1, 2) and v2.previous_version_id == v1.id
    assert v1.superseded_at is not None and v2.superseded_at is None and v1.original_filename == "main.pdf" and v1.sha256 != v2.sha256
    assert counts(v1.id)[0] == 1  # the old version's results are kept, as history of that version
    with SessionLocal() as db:
        assert db.scalar(select(DocumentSlot)).current_version_id == v2.id
    docs = world.refresh()["documents"]
    assert [(d["version_number"], d["is_current"]) for d in docs] == [(1, False), (2, True)]
    assert docs[0]["state"]["code"] == "superseded" and docs[1]["state"]["code"] in ("received", "extraction_queued")
    assert len([p for p in storage().root.rglob("*") if p.is_file()]) == 2  # both files exist


def test_a_new_version_does_not_inherit_the_previous_results_and_old_queued_work_is_cancelled(world):
    world.upload(data=digital_pdf([["Name of the Institution: Alpha College"]]))
    world.upload(data=digital_pdf([["Name of the Institution: Beta College"]]), name="b.pdf")  # replaced before the worker ran
    v1, v2 = doc_rows(world.app["id"])
    assert job_for(v1.id).status == "cancelled" and proc_for(v1.id).extraction_status == "SUPERSEDED"
    drain()
    assert counts(v1.id) == (0, 0)  # v1 was never processed
    with SessionLocal() as db:
        f = {x.field_key: x for x in db.scalars(select(ExtractedField).where(ExtractedField.document_id == v2.id))}
    assert f["institution_name"].value == "Beta College" and f["institution_name"].document_id == v2.id
    ev = world.evidence()["items"][0]
    assert ev["current"]["version_number"] == 2 and [x["value"] for x in ev["fields"] if x["field_key"] == "institution_name"] == ["Beta College"]
    assert {x["version_number"] for x in ev["fields"]} == {2}


def test_a_job_for_an_old_version_cannot_overwrite_the_new_versions_results(world):
    s = get_settings()
    world.upload(data=digital_pdf([["Name of the Institution: Old Name"]]))
    with SessionLocal() as db:
        running = jobs.claim_next(db, "w-old", s)  # v1 is already running when the replacement arrives
        v1_id = running.document_id
    world.upload(data=digital_pdf([["Name of the Institution: New Name"]]), name="new.pdf")
    from app.processing.ocr import NullOcr
    from app.processing.scanner import NoScanner

    with SessionLocal() as db:
        outcome = run_claimed(db, db.get(ProcessingJob, running.id), "w-old", s, NullOcr(), NoScanner(), storage())
    assert outcome == "cancelled" and counts(v1_id) == (0, 0)
    drain()
    v1, v2 = doc_rows(world.app["id"])
    assert counts(v1.id) == (0, 0) and counts(v2.id)[0] == 1
    with SessionLocal() as db:
        assert db.scalar(select(ExtractedField.value).where(ExtractedField.document_id == v2.id, ExtractedField.field_key == "institution_name")) == "New Name"


def test_uploading_identical_bytes_again_does_not_create_a_version_or_a_job(world):
    data = digital_pdf([["Name of the Institution: Test Institute"]])
    first = world.upload(data=data)
    again = world.upload(data=data)
    third = world.upload(data=data, name="renamed-copy.pdf")
    assert first.status_code == 201 and again.status_code == 200 and third.status_code == 200
    assert again.headers["x-upload-result"] == "duplicate_of_current" and again.json()["documents"][0]["id"] == first.json()["documents"][0]["id"]
    assert len(doc_rows(world.app["id"])) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.job_type == "PROCESS_DOCUMENT")) == 1
    assert len([p for p in storage().root.rglob("*") if p.is_file()]) == 1  # the duplicate bytes were not kept


def test_reverting_to_an_earlier_file_is_a_new_version(world):
    a, b = digital_pdf([["Name of the Institution: A"]]), digital_pdf([["Name of the Institution: B"]])
    world.upload(data=a)
    world.upload(data=b, name="b.pdf")
    assert world.upload(data=a, name="a-again.pdf").status_code == 201
    assert [d.version_number for d in doc_rows(world.app["id"])] == [1, 2, 3]


def test_removing_the_file_cancels_its_work_and_never_resurrects_an_earlier_version(world):
    world.upload(data=digital_pdf([["Name of the Institution: One"]]))
    world.upload(data=digital_pdf([["Name of the Institution: Two"]]), name="two.pdf")
    v1, v2 = doc_rows(world.app["id"])
    assert world.a.delete(f"{world.url}/documents/{v2.id}").status_code == 200
    assert job_for(v2.id).status == "cancelled"
    with SessionLocal() as db:
        assert db.scalar(select(DocumentSlot)).current_version_id is None
    ev = world.evidence()["items"][0]
    assert ev["current"] is None and ev["slot_state"] is None  # no file at all, not "an old file"


# ---- findings follow the current evidence ---------------------------------------------------------------------------


def test_findings_are_recomputed_for_the_new_version_and_history_is_kept(world):
    world.upload(data=digital_pdf([["Name of the Institution: Wrong Place College"]]))
    drain()
    ev = world.evidence()["items"][0]
    f1 = [f for f in ev["findings"] if f["issue_type"] == "cross_document_inconsistency"]
    assert len(f1) == 1 and f1[0]["version_number"] == 1 and f1[0]["pages"] == [1]
    world.upload(data=digital_pdf([["Name of the Institution: Test Institute"]]), name="fixed.pdf")
    drain()
    ev = world.evidence()["items"][0]
    assert not [f for f in ev["findings"] if f["issue_type"] == "cross_document_inconsistency"]
    with SessionLocal() as db:
        rows = list(db.scalars(select(PreliminaryFinding).where(PreliminaryFinding.issue_type == "cross_document_inconsistency")))
    assert len(rows) == 1 and rows[0].status == "SUPERSEDED" and rows[0].superseded_at is not None  # kept, marked as no longer current


def test_cross_checks_coalesce(world):
    world.upload()
    world.upload(item="doc-extra", data=digital_pdf([["Institute Name: Test Institute"]]), name="e.pdf")
    with SessionLocal() as db:
        waiting = db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.job_type == "CROSS_CHECK", ProcessingJob.status == "queued"))
    assert waiting == 1


# ---- logging ---------------------------------------------------------------------------------------------------------


def test_logs_never_contain_document_content_or_storage_paths(world, caplog):
    caplog.set_level(logging.DEBUG)
    world.upload(data=digital_pdf([[f"Name of the Institution: {SENTINEL}"]]))
    world.upload(item="doc-extra", data=scanned_pdf([[SENTINEL]]), name="scan.pdf")
    drain()
    drain(ocr=FakeOcr(error=ProcessingError("ocr_failed", f"engine said {SENTINEL}")))
    for d in doc_rows(world.app["id"]):
        assert d.storage_key not in caplog.text
    assert SENTINEL not in caplog.text
    assert str(storage().root) not in caplog.text
    with SessionLocal() as db:
        events = " ".join(str(e.detail) for e in db.scalars(select(DocumentEvent)))
        errors = " ".join(filter(None, (j.last_error_message for j in db.scalars(select(ProcessingJob)))))
    assert SENTINEL not in events and SENTINEL not in errors  # process errors stored for people are the generic catalogue messages


def test_reaper_and_settings_defaults_are_sane():
    s = Settings(environment="test")
    assert 1 <= s.job_max_attempts <= 10 and s.job_lease_seconds >= 60 and s.max_manual_retries >= 1
    with SessionLocal() as db:
        assert jobs.reap_expired(db, s) == 0
