"""Document-processing worker.

    python -m app.worker                 # run forever, polling the job table
    python -m app.worker --once          # drain the queue and exit (useful in development and CI)
    python -m app.worker --enqueue-missing   # create jobs for current file versions that have none, then run

Run as many workers as you like: jobs are claimed with a compare-and-set update and a lease, so two
workers never process the same job at once, and a crashed worker's jobs are requeued when the lease
expires. Logs contain job and document identifiers and error *codes* only, never document content.
"""

import argparse
import logging
import os
import socket
import time
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import jobs
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.domain import ExtractionStatus, JobStatus, JobType
from app.evidence import apply_output, field_specs, recompute_findings, reset_processing_row
from app.models import utcnow
from app.models_applications import Application, ApplicationDocument
from app.models_processing import DocumentProcessing, DocumentSlot, ProcessingJob
from app.processing.errors import ProcessingError
from app.processing.ocr import OcrEngine, get_ocr_engine
from app.processing.pipeline import run_pipeline
from app.processing.scanner import Scanner, get_scanner
from app.services.applications import config_of
from app.storage import LocalStorage, StorageError

log = logging.getLogger("app.worker")


class LeaseLost(Exception):
    """This worker no longer owns the job; whatever it computed must be discarded."""


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def _owns(db: Session, job_id: uuid.UUID, worker_id: str) -> bool:
    q = select(ProcessingJob).where(ProcessingJob.id == job_id)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    job = db.scalars(q.execution_options(populate_existing=True)).one()
    return job.locked_by == worker_id and job.status == JobStatus.RUNNING.value


def run_claimed(
    db: Session, job: ProcessingJob, worker_id: str, settings: Settings, ocr: OcrEngine, scanner: Scanner, storage: LocalStorage, now: datetime | None = None,
) -> str:
    """Execute one already-claimed job. Returns the resulting job status. Never raises for processing problems."""
    job_id, doc_id, app_id = job.id, job.document_id, job.application_id
    try:
        if job.job_type == JobType.CROSS_CHECK.value:
            summary = recompute_findings(db, app_id, settings, now)
            if not _owns(db, job_id, worker_id):
                raise LeaseLost
            jobs.finish_success(db, job, now)
            db.commit()
            log.info("job %s cross-check done: %s", job_id, summary)
            return JobStatus.SUCCEEDED.value

        doc = db.get(ApplicationDocument, doc_id, populate_existing=True)
        if doc.superseded_at is not None or doc.removed_at is not None:
            if _owns(db, job_id, worker_id):
                job.status, job.completed_at, job.locked_by, job.lease_expires_at = JobStatus.CANCELLED.value, now or utcnow(), None, None
                job.last_error_code = "superseded"
                db.execute(DocumentProcessing.__table__.update().where(DocumentProcessing.document_id == doc_id).values(extraction_status=ExtractionStatus.SUPERSEDED.value))
                db.commit()
            return JobStatus.CANCELLED.value

        app = db.scalars(select(Application).where(Application.id == app_id).options(selectinload(Application.rule_version))).one()
        specs = field_specs(config_of(app), doc.item_key)
        try:
            with storage.open(doc.storage_key) as fh:
                data = fh.read()
        except (OSError, StorageError) as exc:
            raise ProcessingError("storage_unavailable", type(exc).__name__) from exc
        media_type, storage_ref = doc.media_type, str(doc.id)

        def heartbeat() -> None:
            with SessionLocal() as hb:
                if not jobs.extend_lease(hb, job_id, worker_id, settings, now):
                    raise LeaseLost

        out = run_pipeline(data, media_type, specs, settings, ocr, scanner, heartbeat)

        # Persist only if we still own the job; an old attempt must never overwrite newer state.
        if not _owns(db, job_id, worker_id):
            raise LeaseLost
        job = db.get(ProcessingJob, job_id, populate_existing=True)
        apply_output(db, doc, job, out, now)
        if out.extraction_status in (ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL):
            jobs.finish_success(db, job, now)
        else:
            jobs.finish_terminal(db, job, out.error_code or "unexpected", now)
        jobs.enqueue_cross_check(db, app_id, settings, now)
        db.commit()
        log.info("job %s document %s finished: %s (%s)", job_id, storage_ref, job.status, out.error_code or "ok")
        return job.status
    except LeaseLost:
        db.rollback()
        log.warning("job %s: lease lost, discarding results", job_id)
        return "lease_lost"
    except ProcessingError as exc:
        db.rollback()
        jobs.record_failure(db, job_id, worker_id, exc, settings, now)
        log.warning("job %s failed: %s (%s)", job_id, exc.code, exc.detail[:80])
        return db.get(ProcessingJob, job_id, populate_existing=True).status
    except Exception as exc:  # unknown failure: treated as transient and bounded; the message is not logged (may echo content)
        db.rollback()
        jobs.record_failure(db, job_id, worker_id, ProcessingError("unexpected", type(exc).__name__), settings, now)
        log.error("job %s failed unexpectedly: %s", job_id, type(exc).__name__)
        return db.get(ProcessingJob, job_id, populate_existing=True).status


def process_one(
    settings: Settings | None = None, *, worker_id: str | None = None, ocr: OcrEngine | None = None, scanner: Scanner | None = None,
    storage: LocalStorage | None = None, now: datetime | None = None,
) -> str | None:
    """Reap expired leases, claim one job, run it. Returns its resulting status, or None when nothing was runnable."""
    settings = settings or get_settings()
    worker_id = worker_id or default_worker_id()
    ocr, scanner = ocr or get_ocr_engine(settings), scanner or get_scanner(settings)
    storage = storage or LocalStorage(settings.storage_dir)
    with SessionLocal() as db:
        jobs.reap_expired(db, settings, now)
        job = jobs.claim_next(db, worker_id, settings, now)
        if job is None:
            return None
        return run_claimed(db, job, worker_id, settings, ocr, scanner, storage, now)


def run_until_idle(settings: Settings | None = None, *, limit: int = 1000, **kw) -> list[str]:
    results: list[str] = []
    for _ in range(limit):
        r = process_one(settings, **kw)
        if r is None:
            break
        results.append(r)
    return results


def enqueue_missing(settings: Settings | None = None) -> int:
    """Recovery/backfill: every current file version gets a processing row and a job if it has none."""
    settings = settings or get_settings()
    n = 0
    with SessionLocal() as db:
        slots = db.scalars(select(DocumentSlot).where(DocumentSlot.current_version_id.is_not(None))).all()
        for slot in slots:
            doc = db.get(ApplicationDocument, slot.current_version_id)
            if doc is None or doc.removed_at is not None:
                continue
            if db.scalar(select(DocumentProcessing.id).where(DocumentProcessing.document_id == doc.id)) is None:
                reset_processing_row(db, doc)
            if db.scalar(select(ProcessingJob.id).where(ProcessingJob.document_id == doc.id, ProcessingJob.job_type == JobType.PROCESS_DOCUMENT.value)) is None:
                jobs.enqueue_document_job(db, doc, settings)
                n += 1
        db.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Document-processing worker")
    ap.add_argument("--once", action="store_true", help="process until the queue is empty, then exit")
    ap.add_argument("--enqueue-missing", action="store_true", help="queue jobs for current file versions that have none")
    ap.add_argument("--worker-id", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    ocr, scanner = get_ocr_engine(settings), get_scanner(settings)
    wid = args.worker_id or default_worker_id()
    log.info("worker %s starting: ocr=%s (available=%s) scanner=%s scan_required=%s", wid, ocr.name, ocr.available(), scanner.name, settings.malware_scan_required)
    if args.enqueue_missing:
        log.info("enqueued %d missing job(s)", enqueue_missing(settings))
    if args.once:
        done = run_until_idle(settings, worker_id=wid, ocr=ocr, scanner=scanner)
        log.info("processed %d job(s)", len(done))
        return 0
    try:
        while True:
            if process_one(settings, worker_id=wid, ocr=ocr, scanner=scanner) is None:
                time.sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        log.info("worker %s stopping", wid)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
