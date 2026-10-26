"""Closed vocabularies for the rule catalogue and applications.

These are *software states*, not regulatory content. Regulatory content (categories,
checklists, routes, deadlines) lives in database records governed by the rule catalogue.
"""

import re
from enum import StrEnum


class SourceVerification(StrEnum):
    VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"  # read in the issuing authority's own publication
    SECONDARY_UNVERIFIED = "SECONDARY_UNVERIFIED"  # mirror, summary or secondary report; not yet checked against the official source
    PROPOSED_POLICY = "PROPOSED_POLICY"  # our own proposed product behaviour; not a regulatory statement
    DEMO_ONLY = "DEMO_ONLY"  # illustrative; invented for demonstration


class Classification(StrEnum):
    OFFICIAL = "OFFICIAL"  # may only be published when every source is VERIFIED_OFFICIAL
    DEMO = "DEMO"  # DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES; non-production only


DEMO_BANNER = "DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    RETIRED = "retired"


# Explicit lifecycle. Anything not listed here is rejected by the backend.
ALLOWED_TRANSITIONS: dict[VersionStatus, frozenset[VersionStatus]] = {
    VersionStatus.DRAFT: frozenset({VersionStatus.IN_REVIEW}),
    VersionStatus.IN_REVIEW: frozenset({VersionStatus.DRAFT, VersionStatus.PUBLISHED}),
    VersionStatus.PUBLISHED: frozenset({VersionStatus.RETIRED}),
    VersionStatus.RETIRED: frozenset(),
}


class ItemType(StrEnum):
    DOCUMENT_UPLOAD = "document_upload"
    FORM_FIELD = "form_field"
    DECLARATION = "declaration"


class FieldKind(StrEnum):
    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    SELECT = "select"
    DATE = "date"


class DeadlineType(StrEnum):
    STATUTORY = "STATUTORY"
    INTERNAL_SLA = "INTERNAL_SLA"


class GapReason(StrEnum):
    NO_APPROVED_VERSION = "no_approved_version"
    AMBIGUOUS = "ambiguous"
    INCOMPLETE = "incomplete"


class ApplicationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"


class DocumentVerification(StrEnum):
    NOT_VERIFIED = "NOT_VERIFIED"  # the only value in Phase 2: upload is not verification


ACADEMIC_YEAR_RE = re.compile(r"^(\d{4})-(\d{2})$")


def is_valid_academic_year(value: str) -> bool:
    m = ACADEMIC_YEAR_RE.match(value)
    return bool(m) and (int(m.group(1)) + 1) % 100 == int(m.group(2))

# ---- document processing (Phase 3) ---------------------------------------------------------------
# Each of these answers a different question. None of them is "verified" or "accepted":
#   upload      -> a file was stored            (ApplicationDocument)
#   technical   -> the file is readable          (TechnicalStatus)
#   scan        -> malware scan outcome          (ScanStatus)
#   extraction  -> text/fields were read         (ExtractionStatus, FieldStatus)
#   findings    -> preliminary issues for review (FindingStatus)
# Regulatory acceptance belongs to the authorised scrutiny process (a later phase).


class TechnicalStatus(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"  # readable and structurally sound; says nothing about content
    FAILED = "FAILED"


class ScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    SCANNER_UNAVAILABLE = "SCANNER_UNAVAILABLE"  # nothing was scanned; never to be shown as "clean"
    ERROR = "ERROR"


class ExtractionStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"  # some pages could not be read reliably
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"  # held: quarantined, or scanning required but unavailable
    SUPERSEDED = "SUPERSEDED"


class JobType(StrEnum):
    PROCESS_DOCUMENT = "PROCESS_DOCUMENT"
    CROSS_CHECK = "CROSS_CHECK"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED_PERMANENT = "failed_permanent"
    FAILED_EXHAUSTED = "failed_exhausted"
    CANCELLED = "cancelled"


class PageMethod(StrEnum):
    PDF_TEXT = "PDF_TEXT"
    OCR = "OCR"
    NONE = "NONE"


class PageStatus(StrEnum):
    OK = "OK"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    EMPTY = "EMPTY"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    FAILED = "FAILED"


class FieldStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    UNCERTAIN = "UNCERTAIN"
    NOT_FOUND = "NOT_FOUND"  # "unable to extract": no value is invented


class FindingStatus(StrEnum):
    PRELIMINARY_ISSUE = "PRELIMINARY_ISSUE"  # awaiting scrutiny review; NOT a deficiency notice
    SUPERSEDED = "SUPERSEDED"  # no longer produced by the current evidence (kept for history)


class ExtractionKind(StrEnum):
    TEXT = "text"
    DATE = "date"
    IDENTIFIER = "identifier"


# ---- scrutiny, notices and responses (Phase 4) ----------------------------------------------------
# Four lifecycles, deliberately kept apart:
#   application (DRAFT/SUBMITTED, Phase 2)  - never touched by scrutiny
#   scrutiny case                            - where the application is in scrutiny / re-scrutiny
#   deficiency notice                        - draft -> in review -> issued -> response received -> closed
#   finding                                  - one reviewer's conclusion about one issue
# Every state change goes through the transition tables below; no handler assigns a status directly.

STAGE_KEY_SCRUTINY = "scrutiny"  # route-stage key that marks the scrutiny / re-scrutiny stage (convention; see docs/DECISIONS.md)
NOTICE_ISSUED_TRIGGER = "deficiency_notice_issued"  # deadline-policy trigger event that governs a notice's response period


class CaseState(StrEnum):
    AWAITING_ASSIGNMENT = "AWAITING_ASSIGNMENT"  # submitted; no authorised assignment yet
    READY_FOR_SCRUTINY = "READY_FOR_SCRUTINY"  # assigned; the reviewer has not started
    UNDER_SCRUTINY = "UNDER_SCRUTINY"
    AWAITING_NOTICE_DECISION = "AWAITING_NOTICE_DECISION"  # a prepared notice awaits an authorised issuer
    AWAITING_RESPONSE = "AWAITING_RESPONSE"  # a notice is issued; the institution has not responded
    AWAITING_RESCRUTINY = "AWAITING_RESCRUTINY"  # response received; the reviewer has not started re-scrutiny
    UNDER_RESCRUTINY = "UNDER_RESCRUTINY"
    SCRUTINY_COMPLETED = "SCRUTINY_COMPLETED"  # NOT approval: the case may proceed to its next configured stage
    CONFIGURATION_EXCEPTION = "CONFIGURATION_EXCEPTION"  # the approved route cannot be applied; nothing is guessed


class CaseAction(StrEnum):
    ASSIGN = "assign"
    START = "start"
    PREPARE_NOTICE = "prepare_notice"  # a notice was submitted for issuance
    RETURN_NOTICE = "return_notice"  # the issuer sent it back (or it was withdrawn) during first-round scrutiny
    RETURN_NOTICE_RESCRUTINY = "return_notice_rescrutiny"  # ... during re-scrutiny
    ISSUE_NOTICE = "issue_notice"
    RECEIVE_RESPONSE = "receive_response"
    COMPLETE = "complete"
    FLAG_EXCEPTION = "flag_exception"


_CS = CaseState
CASE_TRANSITIONS: dict[tuple[CaseState, CaseAction], CaseState] = {
    (_CS.AWAITING_ASSIGNMENT, CaseAction.ASSIGN): _CS.READY_FOR_SCRUTINY,
    (_CS.READY_FOR_SCRUTINY, CaseAction.START): _CS.UNDER_SCRUTINY,
    (_CS.UNDER_SCRUTINY, CaseAction.PREPARE_NOTICE): _CS.AWAITING_NOTICE_DECISION,
    (_CS.UNDER_RESCRUTINY, CaseAction.PREPARE_NOTICE): _CS.AWAITING_NOTICE_DECISION,
    (_CS.AWAITING_NOTICE_DECISION, CaseAction.RETURN_NOTICE): _CS.UNDER_SCRUTINY,
    (_CS.AWAITING_NOTICE_DECISION, CaseAction.RETURN_NOTICE_RESCRUTINY): _CS.UNDER_RESCRUTINY,
    (_CS.AWAITING_NOTICE_DECISION, CaseAction.ISSUE_NOTICE): _CS.AWAITING_RESPONSE,
    (_CS.AWAITING_RESPONSE, CaseAction.RECEIVE_RESPONSE): _CS.AWAITING_RESCRUTINY,
    (_CS.AWAITING_RESCRUTINY, CaseAction.START): _CS.UNDER_RESCRUTINY,
    (_CS.UNDER_SCRUTINY, CaseAction.COMPLETE): _CS.SCRUTINY_COMPLETED,
    (_CS.UNDER_RESCRUTINY, CaseAction.COMPLETE): _CS.SCRUTINY_COMPLETED,
    (_CS.AWAITING_ASSIGNMENT, CaseAction.FLAG_EXCEPTION): _CS.CONFIGURATION_EXCEPTION,
    (_CS.READY_FOR_SCRUTINY, CaseAction.FLAG_EXCEPTION): _CS.CONFIGURATION_EXCEPTION,
    (_CS.UNDER_SCRUTINY, CaseAction.FLAG_EXCEPTION): _CS.CONFIGURATION_EXCEPTION,
    (_CS.UNDER_RESCRUTINY, CaseAction.FLAG_EXCEPTION): _CS.CONFIGURATION_EXCEPTION,
}


class RoundKind(StrEnum):
    INITIAL = "INITIAL"
    RE_SCRUTINY = "RE_SCRUTINY"


class RoundStatus(StrEnum):
    OPEN = "OPEN"
    NOTICE_ISSUED = "NOTICE_ISSUED"  # this round ended with a formal notice
    COMPLETED = "COMPLETED"


class FindingState(StrEnum):
    OPEN = "OPEN"  # recorded by a reviewer
    NOTICED = "NOTICED"  # included in an issued notice; awaiting the institution's response
    UNRESOLVED = "UNRESOLVED"  # re-scrutiny: the response does not resolve it
    RESOLVED = "RESOLVED"
    NOT_SUPPORTED = "NOT_SUPPORTED"  # the reviewer concluded it is not a valid finding


FINDING_TRANSITIONS: dict[FindingState, frozenset[FindingState]] = {
    FindingState.OPEN: frozenset({FindingState.NOTICED, FindingState.RESOLVED, FindingState.NOT_SUPPORTED}),
    FindingState.NOTICED: frozenset({FindingState.RESOLVED, FindingState.UNRESOLVED, FindingState.NOT_SUPPORTED}),
    FindingState.UNRESOLVED: frozenset({FindingState.NOTICED, FindingState.RESOLVED, FindingState.NOT_SUPPORTED}),
    FindingState.RESOLVED: frozenset(),
    FindingState.NOT_SUPPORTED: frozenset(),
}
FINDING_PENDING_STATES = frozenset({FindingState.OPEN, FindingState.NOTICED})  # must be dispositioned before a round can be completed


class Visibility(StrEnum):
    INTERNAL_ONLY = "INTERNAL_ONLY"
    ELIGIBLE_FOR_NOTICE = "ELIGIBLE_FOR_NOTICE"


class SourceRefStatus(StrEnum):
    ESTABLISHED = "ESTABLISHED"  # chosen from the case's own frozen rule version
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"  # no source established; flagged, never invented


class NoticeStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"  # prepared; awaiting an authorised issuer
    ISSUED = "ISSUED"  # official to the institution; awaiting its response
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    CLOSED = "CLOSED"  # re-scrutiny has evaluated the response
    WITHDRAWN = "WITHDRAWN"  # never issued


NOTICE_TRANSITIONS: dict[NoticeStatus, frozenset[NoticeStatus]] = {
    NoticeStatus.DRAFT: frozenset({NoticeStatus.IN_REVIEW, NoticeStatus.WITHDRAWN}),
    NoticeStatus.IN_REVIEW: frozenset({NoticeStatus.DRAFT, NoticeStatus.ISSUED, NoticeStatus.WITHDRAWN}),
    NoticeStatus.ISSUED: frozenset({NoticeStatus.RESPONSE_RECEIVED}),
    NoticeStatus.RESPONSE_RECEIVED: frozenset({NoticeStatus.CLOSED}),
    NoticeStatus.CLOSED: frozenset(),
    NoticeStatus.WITHDRAWN: frozenset(),
}
NOTICE_PREPARING = (NoticeStatus.DRAFT.value, NoticeStatus.IN_REVIEW.value)
NOTICE_LIVE = (NoticeStatus.ISSUED.value, NoticeStatus.RESPONSE_RECEIVED.value)
NOTICE_VISIBLE_TO_INSTITUTION = (NoticeStatus.ISSUED.value, NoticeStatus.RESPONSE_RECEIVED.value, NoticeStatus.CLOSED.value)


class ResponseStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"


class ItemResponseState(StrEnum):
    NOT_RESPONDED = "NOT_RESPONDED"
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"  # received by the scrutiny system and awaiting re-scrutiny
    REVIEWED = "REVIEWED"  # re-scrutiny has recorded a conclusion on the linked finding (derived, never stored)


class AssignmentSource(StrEnum):
    DEMO_MANUAL = "DEMO_MANUAL"  # controlled demonstration only; NOT the official AICTE selection process
    OFFICIAL_SELECTION = "OFFICIAL_SELECTION"  # result of the authorised selection mechanism (no interface exists yet)


class HandoffStatus(StrEnum):
    QUEUED = "QUEUED"  # the next configured stage is known; its module is not part of this phase
    AWAITING_DETERMINATION = "AWAITING_DETERMINATION"  # the route has a conditional / multiple next stage; a person must determine
    NO_FURTHER_STAGE = "NO_FURTHER_STAGE"  # the frozen route configures nothing after scrutiny
    EXCEPTION = "EXCEPTION"  # route missing or unusable


class ResponsePolicyState(StrEnum):
    COMPUTED = "COMPUTED"  # a due date follows from a usable, verified policy
    DEMO_COMPUTED = "DEMO_COMPUTED"  # as above, from a DEMO_ONLY policy on a demo-classified case
    NO_DURATION = "NO_DURATION"  # a policy exists but states no duration
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    UNVERIFIED = "UNVERIFIED"  # a duration exists but its source is not verified (and this is not a demo case)
    UNSUPPORTED = "UNSUPPORTED"  # e.g. working days: there is no verified calendar to count with


class NotificationEvent(StrEnum):
    CASE_ENTERED_QUEUE = "CASE_ENTERED_QUEUE"
    CASE_ASSIGNED = "CASE_ASSIGNED"
    NOTICE_AWAITING_ISSUANCE = "NOTICE_AWAITING_ISSUANCE"
    NOTICE_ISSUED = "NOTICE_ISSUED"
    RESPONSE_SUBMITTED = "RESPONSE_SUBMITTED"
    READY_FOR_RESCRUTINY = "READY_FOR_RESCRUTINY"
    SCRUTINY_COMPLETED = "SCRUTINY_COMPLETED"
    HANDED_OFF = "HANDED_OFF"
