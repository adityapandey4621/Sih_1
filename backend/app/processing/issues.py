"""Catalogue of preliminary issue types.

One place defines what each issue is called, how urgently it should be looked at, what the
institution is told (if anything), and what a reviewer should consider. The interface renders
what the API returns; it contains no issue-type conditionals.

Priority is *review priority*, not legal severity. None of these is a deficiency: a formal
deficiency is a conclusion of the authorised scrutiny process.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IssueType:
    key: str
    label: str
    priority: str  # info | low | medium | high
    description: str  # for reviewers
    institute_visible: bool
    institute_text: str  # what the institution is told, in plain and non-accusatory words


ISSUE_TYPES: dict[str, IssueType] = {t.key: t for t in [
    IssueType("required_file_not_supplied", "Required file not supplied", "high",
              "No file is attached to a required checklist item.", True, "No file has been supplied for this requirement."),
    IssueType("unsupported_or_corrupt_file", "File could not be read", "high",
              "The file is unsupported, damaged, encrypted or otherwise could not be inspected reliably.", True,
              "We could not read this file. You may want to upload a clean, unprotected copy."),
    IssueType("text_extraction_failed", "Text could not be extracted", "medium",
              "Neither text extraction nor OCR produced usable text for this document.", True,
              "We could not extract text from this file, so its contents have not been read."),
    IssueType("ocr_confidence_low", "Low OCR confidence", "medium",
              "Some pages were recognised with low confidence. Extracted values from them are uncertain.", True,
              "Parts of this scan were hard to read. A reviewer may look at it more closely."),
    IssueType("expected_field_not_found", "Expected value not found", "medium",
              "A value the configuration expects in this document was not found by the extractor. This may be a limitation of the extractor, not a problem with the document.", True,
              "A value we looked for was not found in this document. This may simply reflect how the document is laid out."),
    IssueType("field_value_uncertain", "Extracted value uncertain", "low",
              "A value was found but could not be interpreted unambiguously (multiple different values, unreadable date, low confidence).", True,
              "A value in this document could not be read with confidence."),
    IssueType("cross_document_inconsistency", "Possible inconsistency between values", "high",
              "Two values that are expected to agree appear to differ after normalisation. Compare the source pages.", True,
              "A value in this document appears different from the same value elsewhere in your application. A reviewer will look at it."),
    IssueType("potential_duplicate_file", "Same file used more than once", "low",
              "The same file (identical content) is attached to more than one checklist item.", True,
              "The same file appears against more than one requirement."),
    IssueType("scanner_unavailable", "Malware scan not performed", "info",
              "No malware scanner is configured, so this file was not scanned. This is an environment limitation.", False, ""),
    IssueType("malware_detected", "Malware scanner flagged the file", "high",
              "The configured malware scanner reported a signature. The file has been quarantined.", True,
              "This file could not be accepted for processing."),
    IssueType("manual_inspection_recommended", "Manual inspection recommended", "medium",
              "Processing finished with limitations (for example unreadable pages) that a person should check.", True,
              "This document has parts that automated reading could not cover; a reviewer may check it."),
]}

NOT_A_DEFICIENCY = (
    "This is a preliminary, automated observation awaiting scrutiny review. "
    "It is not a deficiency notice and does not affect the status of the application."
)
