"""The EVC visit: observations, private evidence, the versioned report, and completing the stage.

Principles enforced here (and tested):
  * Only a member assigned to the case acts on the visit; the coordinator arranges and completes the stage. Neither decides anything.
  * An observation records what was seen. It is never "compliant" / "approved"; its note goes into the report, its internal note never does.
  * A submitted report is immutable and carries a snapshot of what it rests on (document versions, observations, evidence, policy, visit).
    A correction is a NEW version, opened by an authorised coordinator with a reason. Earlier versions remain.
  * Submitting a report completes nothing by itself and approves nothing. The coordinator completes the stage; only then does the route engine
    (services/stages.py) say what follows.
  * The checklist, required sections and evidence requirements come from the case's frozen committee policy, not from this code.
"""

import hashlib
import json
import re
import uuid
from urllib.parse import urlparse

from fastapi import Request, UploadFile
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit, committee_access
from app.config import Settings
from app.domain import CAction, CommitteeStage, CState, ObservationStatus, ReportKind, ReportStatus
from app.evidence import current_documents
from app.models import User, utcnow
from app.models_applications import ApplicationDocument
from app.models_committee import CommitteeCase, CommitteeEvidence, CommitteeReport, EvcObservation
from app.processing.documents import sniff
from app.security import as_utc
from app.services import committee_core as core
from app.services import committee_texts as ctexts
from app.services import scheduling, stages
from app.services.errors import Issue, ServiceError, conflict, invalid, not_found
from app.storage import LocalStorage, StorageError

_CHUNK = 1024 * 1024
STAGE = CommitteeStage.EVC
OBS_STATUSES = {s.value for s in ObservationStatus}


def _member_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, user, case_id, request, stage=STAGE, need="member")
    return core.lock_case(db, case.id)


def _coordinator_case(db: Session, user: User, case_id: uuid.UUID, request: Request | None) -> CommitteeCase:
    case, _ = committee_access.get_case(db, user, case_id, request, stage=STAGE, need="coordinator")
    return core.lock_case(db, case.id)


def checklist(case: CommitteeCase) -> list[dict]:
    return list(core.policy(case).get("content", {}).get("checklist", []))


def report_sections(case: CommitteeCase) -> list[dict]:
    return list(core.policy(case).get("content", {}).get("report", {}).get("sections", []))


def draft_report(db: Session, case_id: uuid.UUID) -> CommitteeReport | None:
    return db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == case_id, CommitteeReport.kind == ReportKind.EVC_REPORT.value, CommitteeReport.status == ReportStatus.DRAFT.value).execution_options(populate_existing=True))


def submitted_reports(db: Session, case_id: uuid.UUID) -> list[CommitteeReport]:
    return list(db.scalars(select(CommitteeReport).where(CommitteeReport.case_id == case_id, CommitteeReport.kind == ReportKind.EVC_REPORT.value, CommitteeReport.status == ReportStatus.SUBMITTED.value).order_by(CommitteeReport.version_number)))


def latest_version(db: Session, case_id: uuid.UUID) -> int:
    from sqlalchemy import func

    return db.scalar(select(func.max(CommitteeReport.version_number)).where(CommitteeReport.case_id == case_id, CommitteeReport.kind == ReportKind.EVC_REPORT.value)) or 0


# ---- the visit -------------------------------------------------------------------------------------


def start_visit(db: Session, user: User, case_id: uuid.UUID, row_version: int, request: Request | None) -> CommitteeCase:
    case = _member_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.SCHEDULED)
    core.require_no_blockers(case)
    sch = scheduling.active(db, case.id)
    if sch is None or sch.status != "CONFIRMED":
        raise conflict("no_confirmed_schedule", "The visit has no confirmed schedule.")
    from_state = case.state
    core.transition(db, case, CAction.START_VISIT, expected_version=row_version)
    if draft_report(db, case.id) is None and latest_version(db, case.id) == 0:
        db.add(CommitteeReport(case_id=case.id, kind=ReportKind.EVC_REPORT.value, version_number=1, status=ReportStatus.DRAFT.value, content={"sections": {}}, created_by_id=user.id))
    core.log_event(db, case, user, "visit_started", request=request, object_type="committee_schedule", object_id=sch.id, from_state=from_state, to_state=case.state, institution_visible=True,
                   detail={"before_scheduled_time": utcnow() < as_utc(sch.start_at), "mode": sch.mode})
    db.flush()
    ctexts.report_awaited(db, case, core.application(db, case))
    return case


def _working(case: CommitteeCase) -> None:
    core.require_state(case, CState.VISIT_IN_PROGRESS)


def _check_doc_refs(db: Session, case: CommitteeCase, refs: list[dict] | None) -> list[dict]:
    out = []
    for r in refs or []:
        doc = db.get(ApplicationDocument, uuid.UUID(str(r["document_id"])))
        if doc is None or doc.application_id != case.application_id or doc.removed_at is not None:
            raise invalid("A referenced document does not belong to this application.", [Issue("document_refs", "Unknown document.", "document_refs")])
        if doc.superseded_at is not None:
            raise invalid("A referenced document has a newer version; refer to the current one.", [Issue("document_refs", "Superseded version.", "document_refs")])
        out.append({"document_id": str(doc.id), "item_key": doc.item_key, "version_number": doc.version_number, "page": r.get("page")})
    return out


def record_observation(db: Session, user: User, case_id: uuid.UUID, item_key: str, data: dict, row_version: int | None, request: Request | None) -> EvcObservation:
    case = _member_case(db, user, case_id, request)
    _working(case)
    item = next((i for i in checklist(case) if i["item_key"] == item_key), None)
    if item is None:
        raise not_found("Checklist item")
    status = data.get("status")
    if status not in OBS_STATUSES:
        raise invalid("Choose what you observed.", [Issue("status", "Not a valid status.", "status")])
    note = (data.get("note") or "").strip()
    if len(note) < 3:
        raise invalid("Write what you observed, or why it could not be observed.", [Issue("note", "A note is needed.", "note")])
    refs = _check_doc_refs(db, case, data.get("document_refs"))
    values = dict(status=status, note=note, internal_note=(data.get("internal_note") or "").strip() or None, shortcoming=bool(data.get("shortcoming")), document_refs=refs or None, recorded_by_id=user.id)
    cur = db.scalar(select(EvcObservation).where(EvcObservation.case_id == case.id, EvcObservation.item_key == item_key).execution_options(populate_existing=True))
    now = utcnow()
    if cur is None:
        if row_version is not None:
            raise conflict("stale_version", "This observation does not exist yet; reload.")
        cur = EvcObservation(case_id=case.id, item_key=item_key, **values)
        db.add(cur)
        db.flush()
        action = "observation_recorded"
    else:
        if row_version is None or cur.row_version != row_version:
            raise conflict("stale_version", "This observation was changed by someone else since you opened it. Reload it to see their version before saving yours.")
        res = db.execute(update(EvcObservation).where(EvcObservation.id == cur.id, EvcObservation.row_version == row_version).values(**values, row_version=row_version + 1, updated_at=now))
        if res.rowcount != 1:
            raise conflict("stale_version", "This observation was changed at the same moment. Reload it and try again.")
        db.refresh(cur)
        action = "observation_updated"
    core.log_event(db, case, user, action, request=request, object_type="evc_observation", object_id=cur.id,
                   detail={"item_key": item_key, "status": status, "shortcoming": cur.shortcoming, "documents": [(d["document_id"], d["version_number"]) for d in refs]})
    return cur


# ---- evidence (private) -------------------------------------------------------------------------------


def _read_limited(upload: UploadFile, limit: int):
    total = 0
    while chunk := upload.file.read(_CHUNK):
        total += len(chunk)
        if total > limit:
            raise ServiceError(413, "file_too_large", f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        yield chunk


def add_evidence_file(db: Session, user: User, case_id: uuid.UUID, upload: UploadFile, item_key: str | None, description: str, request: Request | None, settings: Settings, storage: LocalStorage) -> CommitteeEvidence:
    """Store a visit file in the same private storage as application documents (PDF, PNG, JPEG; size-limited). It is NOT malware-scanned or text-processed here."""
    case = _member_case(db, user, case_id, request)
    _working(case)
    if item_key is not None and not any(i["item_key"] == item_key for i in checklist(case)):
        raise not_found("Checklist item")
    name = re.sub(r"[\x00-\x1f\x7f]", "", re.split(r"[\\/]", upload.filename or "")[-1]).strip().strip(".")[:255] or "file"
    head = upload.file.read(16)
    upload.file.seek(0)
    kind = sniff(head)
    if kind is None:
        raise ServiceError(415, "type_not_allowed", "Only PDF, PNG and JPEG files are accepted, and the file's content must match its type.")
    media, exts = kind
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in exts:
        raise ServiceError(415, "type_mismatch", "The file's name and content do not agree. Upload the original PDF, PNG or JPEG file.")
    key = storage.new_key()
    try:
        size, digest = storage.save(key, _read_limited(upload, settings.max_upload_bytes))
    except ServiceError:
        raise
    except (StorageError, OSError) as exc:
        audit.record(db, "committee.evidence_store_failed", request=request, actor=user, target_type="committee_case", target_id=case.id, detail={"error": type(exc).__name__})
        db.commit()
        raise ServiceError(503, "storage_unavailable", "The file could not be stored. Nothing was saved; please try again.") from exc
    if size == 0:
        storage.delete(key)
        raise invalid("The file is empty.")
    try:
        ev = CommitteeEvidence(case_id=case.id, item_key=item_key, kind="FILE", description=(description or "").strip()[:500], storage_key=key, original_filename=name, media_type=media, size_bytes=size, sha256=digest, uploaded_by_id=user.id)
        db.add(ev)
        db.flush()
        core.log_event(db, case, user, "evidence_added", request=request, object_type="committee_evidence", object_id=ev.id, detail={"item_key": item_key, "kind": "FILE", "size": size, "sha256": digest})
        db.commit()
    except BaseException:
        db.rollback()
        storage.delete(key)  # no orphaned file when the record could not be saved
        raise
    return ev


def add_evidence_link(db: Session, user: User, case_id: uuid.UUID, url: str, description: str, item_key: str | None, request: Request | None) -> CommitteeEvidence:
    """Record where material is held (for example the recording the institution published, handbook cl. 1.9(f)). The system never fetches or vouches for it."""
    case = _member_case(db, user, case_id, request)
    _working(case)
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 1000:
        raise invalid("Enter a full web address starting with http:// or https://.", [Issue("url", "Not a valid address.", "url")])
    if not (description or "").strip():
        raise invalid("Say what the linked material is.", [Issue("description", "A description is needed.", "description")])
    if item_key is not None and not any(i["item_key"] == item_key for i in checklist(case)):
        raise not_found("Checklist item")
    ev = CommitteeEvidence(case_id=case.id, item_key=item_key, kind="LINK", description=description.strip()[:500], url=url.strip(), uploaded_by_id=user.id)
    db.add(ev)
    db.flush()
    core.log_event(db, case, user, "evidence_added", request=request, object_type="committee_evidence", object_id=ev.id, detail={"item_key": item_key, "kind": "LINK", "host": parsed.netloc})
    return ev


def remove_evidence(db: Session, user: User, case_id: uuid.UUID, evidence_id: uuid.UUID, request: Request | None) -> None:
    case = _member_case(db, user, case_id, request)
    _working(case)
    ev = db.get(CommitteeEvidence, evidence_id)
    if ev is None or ev.case_id != case.id or ev.removed_at is not None:
        raise not_found("Evidence")
    ev.removed_at, ev.removed_by_id = utcnow(), user.id  # the record that it existed stays
    core.log_event(db, case, user, "evidence_removed", request=request, object_type="committee_evidence", object_id=ev.id, detail={"item_key": ev.item_key})


def evidence_for_download(db: Session, user: User, evidence_id: uuid.UUID, request: Request | None) -> CommitteeEvidence:
    ev = db.get(CommitteeEvidence, evidence_id)
    case = db.get(CommitteeCase, ev.case_id) if ev else None
    if ev is None or case is None or case.stage != STAGE.value or ev.kind != "FILE":
        raise not_found("Evidence")
    committee_access.get_case(db, user, case.id, request, stage=STAGE)  # scope: coordinator or assigned member, else "not found" + audit
    audit.record(db, "committee.evidence_downloaded", request=request, actor=user, target_type="committee_evidence", target_id=ev.id, detail={"case_id": str(case.id)})
    db.commit()
    return ev


def live_evidence(db: Session, case_id: uuid.UUID) -> list[CommitteeEvidence]:
    return list(db.scalars(select(CommitteeEvidence).where(CommitteeEvidence.case_id == case_id, CommitteeEvidence.removed_at.is_(None)).order_by(CommitteeEvidence.uploaded_at)))


def observations(db: Session, case_id: uuid.UUID) -> list[EvcObservation]:
    return list(db.scalars(select(EvcObservation).where(EvcObservation.case_id == case_id).order_by(EvcObservation.item_key).execution_options(populate_existing=True)))


# ---- the report ----------------------------------------------------------------------------------------


def save_report_draft(db: Session, user: User, case_id: uuid.UUID, sections: dict[str, str], row_version: int, request: Request | None) -> CommitteeReport:
    case = _member_case(db, user, case_id, request)
    _working(case)
    draft = draft_report(db, case.id)
    if draft is None:
        raise conflict("no_draft_report", "There is no draft report to edit.")
    known = {s["key"] for s in report_sections(case)}
    unknown = sorted(set(sections) - known)
    if unknown:
        raise invalid("Some sections are not part of the configured report.", [Issue("sections", f"Unknown section: {k}", k) for k in unknown])
    if any(len(v) > 20000 for v in sections.values()):
        raise invalid("A section is too long.")
    res = db.execute(update(CommitteeReport).where(CommitteeReport.id == draft.id, CommitteeReport.row_version == row_version, CommitteeReport.status == ReportStatus.DRAFT.value).values(
        content={"sections": {k: v for k, v in sections.items()}}, row_version=row_version + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        raise conflict("stale_version", "The report draft was changed by someone else since you opened it. Reload it to see their version before saving yours.")
    db.refresh(draft)
    core.log_event(db, case, user, "report_draft_saved", request=request, object_type="committee_report", object_id=draft.id, detail={"version": draft.version_number})
    return draft


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def _validate_for_submission(db: Session, case: CommitteeCase, draft: CommitteeReport) -> tuple[list[Issue], dict]:
    issues: list[Issue] = []
    obs = {o.item_key: o for o in observations(db, case.id)}
    evidence = live_evidence(db, case.id)
    by_item: dict[str | None, int] = {}
    for e in evidence:
        by_item[e.item_key] = by_item.get(e.item_key, 0) + 1
    current = current_documents(db, case.application_id)
    current_ids = {str(d.id) for d in current.values()}
    for item in checklist(case):
        o = obs.get(item["item_key"])
        if item.get("required") and o is None:
            issues.append(Issue("observation_missing", f"No observation was recorded for “{item['label']}”.", item["item_key"]))
            continue
        if o is not None:
            if item.get("evidence_required") and by_item.get(item["item_key"], 0) == 0 and o.status != ObservationStatus.EVIDENCE_UNAVAILABLE.value:
                issues.append(Issue("evidence_missing", f"“{item['label']}” needs supporting evidence, or an observation marked as evidence unavailable.", item["item_key"]))
            for ref in o.document_refs or []:
                if ref["document_id"] not in current_ids:
                    issues.append(Issue("evidence_superseded", f"“{item['label']}” relies on a document version that has since been replaced. Review the current version and update the observation.", item["item_key"]))
                    break
    sections = (draft.content or {}).get("sections", {})
    for s in report_sections(case):
        if s.get("required") and len((sections.get(s["key"]) or "").strip()) < 3:
            issues.append(Issue("section_missing", f"The section “{s['label']}” is required.", s["key"]))
    sch = scheduling.active(db, case.id)
    snapshot = {
        "documents": [{"item_key": k, "document_id": str(d.id), "version_number": d.version_number, "sha256": d.sha256} for k, d in sorted(current.items())],
        "observations": [{"item_key": o.item_key, "status": o.status, "note": o.note, "internal_note_present": bool(o.internal_note), "shortcoming": o.shortcoming, "document_refs": o.document_refs or [], "recorded_by": o.recorded_by.full_name} for o in sorted(obs.values(), key=lambda x: x.item_key)],
        "evidence": [{"id": str(e.id), "kind": e.kind, "item_key": e.item_key, "filename": e.original_filename, "sha256": e.sha256, "url": e.url, "description": e.description} for e in evidence],
        "visit": {"schedule_id": str(sch.id), "start_at": sch.start_at.isoformat(), "timezone": sch.timezone, "mode": sch.mode, "sequence": sch.sequence} if sch else None,
        "policy": core.policy(case) and {k: core.policy(case)[k] for k in ("id", "key", "version", "classification", "content_hash")},
        "rule_version": case.trigger.get("rule_version"), "not_an_approval": True,
    }
    return issues, snapshot


def submit_report(db: Session, user: User, case_id: uuid.UUID, idempotency_key: str, row_version: int, request: Request | None) -> tuple[CommitteeCase, CommitteeReport, bool]:
    """-> (case, report, replay). One immutable record; a retry with the same key returns it."""
    case = _member_case(db, user, case_id, request)
    prior = db.scalar(select(CommitteeReport).where(CommitteeReport.case_id == case.id, CommitteeReport.kind == ReportKind.EVC_REPORT.value, CommitteeReport.idempotency_key == idempotency_key))
    if prior is not None:
        return case, prior, True
    _working(case)
    draft = draft_report(db, case.id)
    if draft is None:
        raise conflict("no_draft_report", "There is no draft report to submit.")
    if draft.row_version != row_version:
        raise conflict("stale_version", "The report draft was changed since you opened it. Reload it before submitting.")
    issues, snapshot = _validate_for_submission(db, case, draft)
    if issues:
        raise conflict("report_incomplete", "The report cannot be submitted yet.", issues)
    content = {"sections": (draft.content or {}).get("sections", {})}
    digest = hashlib.sha256(_canonical({"content": content, "snapshot": snapshot}).encode()).hexdigest()
    now = utcnow()
    res = db.execute(update(CommitteeReport).where(CommitteeReport.id == draft.id, CommitteeReport.status == ReportStatus.DRAFT.value, CommitteeReport.row_version == row_version).values(
        status=ReportStatus.SUBMITTED.value, content=content, source_snapshot=snapshot, content_hash=digest, submitted_by_id=user.id, submitted_at=now, idempotency_key=idempotency_key, row_version=row_version + 1, updated_at=now))
    if res.rowcount != 1:
        raise conflict("stale_version", "The report was changed or submitted at the same moment. Reload it.")
    db.refresh(draft)
    from_state = case.state
    core.transition(db, case, CAction.SUBMIT_REPORT)
    scheduling.mark_completed(db, case, user, request)
    core.log_event(db, case, user, "report_submitted", request=request, object_type="committee_report", object_id=draft.id, from_state=from_state, to_state=case.state,
                   detail={"version": draft.version_number, "content_hash": digest, "documents": len(snapshot["documents"]), "approved": False})
    db.flush()
    ctexts.report_submitted(db, case, core.application(db, case), draft.id, draft.version_number)
    return case, draft, False


def open_amendment(db: Session, user: User, case_id: uuid.UUID, reason: str, row_version: int, request: Request | None) -> CommitteeCase:
    """An authorised coordinator allows a corrected version of a submitted report. The submitted version stays exactly as it was."""
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.REPORT_SUBMITTED)
    if not reason or len(reason.strip()) < 5:
        raise invalid("Record why an amendment is needed.")
    last = submitted_reports(db, case.id)[-1]
    from_state = case.state
    core.transition(db, case, CAction.OPEN_AMENDMENT, expected_version=row_version)
    new = CommitteeReport(case_id=case.id, kind=ReportKind.EVC_REPORT.value, version_number=last.version_number + 1, status=ReportStatus.DRAFT.value, content={"sections": dict((last.content or {}).get("sections", {}))},
                          supersedes_id=last.id, amendment_reason=reason.strip(), amendment_authorised_by_id=user.id, created_by_id=user.id)
    db.add(new)
    db.flush()
    core.log_event(db, case, user, "amendment_opened", request=request, object_type="committee_report", object_id=new.id, from_state=from_state, to_state=case.state, reason=reason, detail={"supersedes": str(last.id), "version": new.version_number})
    return case


def complete(db: Session, user: User, case_id: uuid.UUID, summary: str, row_version: int, request: Request | None) -> CommitteeCase:
    """Record that the EVC stage is finished. NOT an approval. The route engine then records what follows."""
    case = _coordinator_case(db, user, case_id, request)
    core.require_version(case, row_version)
    core.require_state(case, CState.REPORT_SUBMITTED)
    if not summary or len(summary.strip()) < 5:
        raise invalid("Record a short note on completing the EVC stage.")
    last = submitted_reports(db, case.id)[-1]
    obs = observations(db, case.id)
    now = utcnow()
    completion = {
        "summary": summary.strip(), "report_id": str(last.id), "report_version": last.version_number, "report_hash": last.content_hash,
        "observations": len(obs), "shortcomings_flagged": sum(1 for o in obs if o.shortcoming), "approved": False,
    }
    from_state = case.state
    core.transition(db, case, CAction.COMPLETE, expected_version=row_version, completed_at=now, completed_by_id=user.id, completion=completion)
    core.log_event(db, case, user, "stage_completed", request=request, object_type="committee_case", object_id=case.id, from_state=from_state, to_state=case.state, reason=summary, institution_visible=True,
                   detail={"report_version": last.version_number, "approved": False})
    db.flush()
    ctexts.stage_completed(db, case, core.application(db, case))
    stages.advance_from_case(db, case, user, request)
    return case
