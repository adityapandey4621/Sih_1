"""Durable processing-job queue in PostgreSQL.

* Jobs are rows, so they survive restarts. A worker *claims* a job with a compare-and-set update
  (plus SKIP LOCKED on PostgreSQL) and holds a **lease**; a crashed worker's lease expires and
  the job is requeued by `reap_expired`.
* One PROCESS_DOCUMENT job exists per document version; a retry re-queues the same row, so a retry
  can never produce a duplicate result set.
* Failure handling follows the error taxonomy: input problems fail permanently (no loop),
  configuration problems wait for a person, transient problems retry with bounded backoff and then
  fail visibly (`failed_exhausted`).
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import ExtractionStatus, JobStatus, JobType
from app.models import utcnow
from app.models_applications import ApplicationDocument
from app.models_processing import DocumentEvent, DocumentProcessing, ProcessingJob
from app.processing import PROCESSOR_VERSION
from app.processing.errors import ErrorKind, ProcessingError
from app.security import as_utc

_CLAIMABLE = (JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value)
_WAITING = _CLAIMABLE


def add_event(db: Session, document_id: uuid.UUID, action: str, detail: dict | None = None, actor_id: uuid.UUID | None = None) -> None:
    db.add(DocumentEvent(document_id=document_id, action=action, detail=detail, actor_id=actor_id))


# ---- enqueue ------------------------------------------------------------------------------------


def enqueue_document_job(db: Session, doc: ApplicationDocument, settings: Settings, now: datetime | None = None) -> ProcessingJob:
    now = now or utcnow()
    job = ProcessingJob(
        job_type=JobType.PROCESS_DOCUMENT.value, application_id=doc.application_id, document_id=doc.id,
        status=JobStatus.QUEUED.value, max_attempts=settings.job_max_attempts, run_after=now, processor_version=PROCESSOR_VERSION,
    )
    db.add(job)
    return job


def enqueue_cross_check(db: Session, application_id: uuid.UUID, settings: Settings, now: datetime | None = None) -> None:
    """Coalescing: if a cross-check is already waiting it will see the latest data when it runs."""
    waiting = db.scalar(
        select(ProcessingJob.id).where(ProcessingJob.application_id == application_id, ProcessingJob.job_type == JobType.CROSS_CHECK.value, ProcessingJob.status.in_(_WAITING)).limit(1)
    )
    if waiting is None:
        db.add(ProcessingJob(job_type=JobType.CROSS_CHECK.value, application_id=application_id, status=JobStatus.QUEUED.value, max_attempts=settings.job_max_attempts, run_after=now or utcnow(), processor_version=PROCESSOR_VERSION))


def cancel_waiting_for_documents(db: Session, document_ids: list[uuid.UUID], reason: str) -> int:
    """A newer version was uploaded (or the file removed): queued work for the old version is pointless."""
    if not document_ids:
        return 0
    now = utcnow()
    res = db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.document_id.in_(document_ids), ProcessingJob.job_type == JobType.PROCESS_DOCUMENT.value, ProcessingJob.status.in_(_WAITING))
        .values(status=JobStatus.CANCELLED.value, completed_at=now, updated_at=now, last_error_code="superseded", last_error_message=reason[:200], retry_eligible=False)
    )
    if res.rowcount:
        db.execute(update(DocumentProcessing).where(DocumentProcessing.document_id.in_(document_ids), DocumentProcessing.extraction_status.in_((ExtractionStatus.QUEUED.value, ExtractionStatus.NOT_STARTED.value))).values(extraction_status=ExtractionStatus.SUPERSEDED.value, updated_at=now))
    return res.rowcount


# ---- claim / lease ------------------------------------------------------------------------------


def claim_next(db: Session, worker_id: str, settings: Settings, now: datetime | None = None) -> ProcessingJob | None:
    now = now or utcnow()
    q = (
        select(ProcessingJob.id)
        .where(ProcessingJob.status.in_(_CLAIMABLE), ProcessingJob.run_after <= now)
        .order_by(ProcessingJob.created_at)
        .limit(5)
    )
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    for job_id in db.scalars(q).all():
        res = db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.status.in_(_CLAIMABLE))
            .values(
                status=JobStatus.RUNNING.value, attempts=ProcessingJob.attempts + 1, locked_by=worker_id, locked_at=now,
                lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds), started_at=now, updated_at=now,
                processor_version=PROCESSOR_VERSION,
            )
        )
        if res.rowcount == 1:  # compare-and-set: exactly one worker wins
            job = db.get(ProcessingJob, job_id, populate_existing=True)
            if job.document_id:
                db.execute(update(DocumentProcessing).where(DocumentProcessing.document_id == job.document_id).values(extraction_status=ExtractionStatus.RUNNING.value, updated_at=now))
                add_event(db, job.document_id, "processing_started", {"attempt": job.attempts, "worker": worker_id})
            db.commit()
            return job
    db.rollback()
    return None


def extend_lease(db: Session, job_id: uuid.UUID, worker_id: str, settings: Settings, now: datetime | None = None) -> bool:
    """False means this worker no longer owns the job (its lease expired and it was reassigned)."""
    now = now or utcnow()
    res = db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id, ProcessingJob.locked_by == worker_id, ProcessingJob.status == JobStatus.RUNNING.value)
        .values(lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds), updated_at=now)
    )
    db.commit()
    return res.rowcount == 1


def reap_expired(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Requeue jobs whose worker vanished. This is what makes a worker restart lose nothing."""
    now = now or utcnow()
    n = 0
    q = select(ProcessingJob).where(ProcessingJob.status == JobStatus.RUNNING.value, ProcessingJob.lease_expires_at < now)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    for job in db.scalars(q).all():
        _fail(db, job, ProcessingError("worker_lost"), settings, now, lost_worker=True)
        n += 1
    db.commit()
    return n


# ---- outcomes -----------------------------------------------------------------------------------


def _owns(job: ProcessingJob, worker_id: str | None) -> bool:
    return worker_id is None or (job.locked_by == worker_id and job.status == JobStatus.RUNNING.value)


def finish_success(db: Session, job: ProcessingJob, now: datetime | None = None) -> None:
    now = now or utcnow()
    job.status, job.completed_at, job.updated_at = JobStatus.SUCCEEDED.value, now, now
    job.locked_by = job.lease_expires_at = None
    job.last_error_code = job.last_error_message = None
    job.retry_eligible = False


def finish_terminal(db: Session, job: ProcessingJob, code: str, now: datetime | None = None) -> None:
    """The pipeline reached a definite non-success outcome (bad input, blocked, no OCR). No automatic retry."""
    now = now or utcnow()
    err = ProcessingError(code)
    job.status, job.completed_at, job.updated_at = JobStatus.FAILED_PERMANENT.value, now, now
    job.locked_by = job.lease_expires_at = None
    job.last_error_code, job.last_error_message = code, err.info.message
    job.retry_eligible = err.kind == ErrorKind.CONFIG  # a person may retry after fixing the environment; corrupt input never loops


def _fail(db: Session, job: ProcessingJob, error: ProcessingError, settings: Settings, now: datetime, lost_worker: bool = False) -> None:
    job.last_error_code, job.last_error_message, job.updated_at = error.code, error.info.message, now
    job.locked_by = job.lease_expires_at = None
    if error.kind == ErrorKind.INPUT:
        job.status, job.completed_at, job.retry_eligible = JobStatus.FAILED_PERMANENT.value, now, False
    elif error.kind == ErrorKind.CONFIG:
        job.status, job.completed_at, job.retry_eligible = JobStatus.FAILED_PERMANENT.value, now, True
    elif job.attempts >= job.max_attempts:
        job.status, job.completed_at, job.retry_eligible = JobStatus.FAILED_EXHAUSTED.value, now, job.manual_retries < settings.max_manual_retries
    else:
        # a vanished worker says nothing about the file, so its job is runnable again immediately
        backoff = 0 if lost_worker else settings.job_retry_backoff_seconds * (2 ** max(0, job.attempts - 1))
        job.status, job.run_after, job.retry_eligible = JobStatus.RETRY_WAIT.value, now + timedelta(seconds=backoff), False
    if job.document_id:
        waiting = job.status == JobStatus.RETRY_WAIT.value
        db.execute(
            update(DocumentProcessing).where(DocumentProcessing.document_id == job.document_id).values(
                extraction_status=ExtractionStatus.QUEUED.value if waiting else ExtractionStatus.FAILED.value,
                last_error_code=error.code, last_error_message=error.info.message, updated_at=now,
            )
        )
        add_event(db, job.document_id, "processing_interrupted" if lost_worker else "processing_failed", {"code": error.code, "attempt": job.attempts, "will_retry": waiting})


def record_failure(db: Session, job_id: uuid.UUID, worker_id: str | None, error: ProcessingError, settings: Settings, now: datetime | None = None) -> bool:
    """Apply a failure to a job this worker still owns. Returns False (and does nothing) if ownership was lost."""
    now = now or utcnow()
    job = db.scalars(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update() if db.bind.dialect.name == "postgresql" else select(ProcessingJob).where(ProcessingJob.id == job_id)).one()
    db.refresh(job)
    if not _owns(job, worker_id):
        db.rollback()
        return False
    _fail(db, job, error, settings, now)
    db.commit()
    return True


# ---- manual retry -------------------------------------------------------------------------------


class RetryNotAllowed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def request_retry(db: Session, doc: ApplicationDocument, settings: Settings, actor_id: uuid.UUID, now: datetime | None = None) -> ProcessingJob:
    now = now or utcnow()
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.document_id == doc.id, ProcessingJob.job_type == JobType.PROCESS_DOCUMENT.value).with_for_update() if db.bind.dialect.name == "postgresql" else select(ProcessingJob).where(ProcessingJob.document_id == doc.id, ProcessingJob.job_type == JobType.PROCESS_DOCUMENT.value))
    if job is None:
        raise RetryNotAllowed("no_job", "There is no processing job for this file.")
    if doc.superseded_at is not None:
        raise RetryNotAllowed("superseded", "This file has been replaced by a newer version; retry that one instead.")
    if job.status in (JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value, JobStatus.RUNNING.value):
        raise RetryNotAllowed("already_pending", "Processing is already queued or running for this file.")
    if job.status == JobStatus.SUCCEEDED.value:
        raise RetryNotAllowed("already_succeeded", "Processing already completed for this file.")
    if not job.retry_eligible:
        raise RetryNotAllowed("not_retryable", "This failure cannot be fixed by retrying: the file itself could not be processed. Upload a corrected file.")
    if job.manual_retries >= settings.max_manual_retries:
        raise RetryNotAllowed("retry_limit", "The retry limit has been reached. Please contact an administrator.")
    job.status, job.run_after, job.attempts, job.retry_eligible = JobStatus.QUEUED.value, now, 0, False
    job.manual_retries += 1
    job.completed_at = None
    job.updated_at = now
    db.execute(update(DocumentProcessing).where(DocumentProcessing.document_id == doc.id).values(extraction_status=ExtractionStatus.QUEUED.value, last_error_code=None, last_error_message=None, updated_at=now))
    add_event(db, doc.id, "retry_requested", {"manual_retries": job.manual_retries}, actor_id)
    return job


def lease_is_expired(job: ProcessingJob, now: datetime) -> bool:
    return job.lease_expires_at is not None and as_utc(job.lease_expires_at) < now
