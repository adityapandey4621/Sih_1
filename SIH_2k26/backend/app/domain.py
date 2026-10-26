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
