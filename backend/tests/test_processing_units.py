"""Pipeline stages without a website or a database."""

import socket
import struct
import threading

import pytest

from app.config import Settings
from app.domain import ExtractionStatus, FieldStatus, PageMethod, PageStatus, ScanStatus, TechnicalStatus
from app.processing import checks
from app.processing.documents import render_preview, sniff, technical_validate
from app.processing.errors import CATALOGUE, ErrorKind, ProcessingError
from app.processing.fields import FieldSpec, PageLines, extract_fields, normalise_text, parse_date
from app.processing.issues import ISSUE_TYPES, NOT_A_DEFICIENCY
from app.processing.ocr import OcrLine, OcrPage, parse_tsv
from app.processing.pipeline import run_pipeline
from app.processing.scanner import ClamdScanner, NoScanner, ScanResult
from tests.docs_helpers import FakeOcr, blurry_png, corrupt_pdf, digital_pdf, encrypted_pdf, jpeg_of, mixed_pdf, ocr_page, page_png, real_tesseract, scanned_pdf

S = Settings(environment="test", ocr_engine="none", scanner="none", require_malware_scan=False)
INST = FieldSpec("institution_name", "Institution name", "text", ("Name of the Institution",), compare_group="inst", compare_to_application="institution_name")
DATE = FieldSpec("document_date", "Document date", "date", ("Date",))
REF = FieldSpec("reference_number", "Reference number", "identifier", ("Reference No", "Ref No"))


class Clean:
    name = "fake-clean"

    def scan(self, data: bytes) -> ScanResult:
        return ScanResult(ScanStatus.CLEAN, "fake")


class Infected:
    name = "fake-infected"

    def scan(self, data: bytes) -> ScanResult:
        return ScanResult(ScanStatus.INFECTED, "Test-Signature")


def run(data: bytes, media: str = "application/pdf", specs=(), ocr=None, scanner=None, settings: Settings = S):
    return run_pipeline(data, media, list(specs), settings, ocr or FakeOcr(available=False), scanner or NoScanner())


# ---- technical validation ---------------------------------------------------------------------------------------


def test_sniffing_uses_content_not_names():
    assert sniff(b"%PDF-1.7 ...")[0] == "application/pdf"
    assert sniff(b"\x89PNG\r\n\x1a\n")[0] == "image/png"
    assert sniff(b"\xff\xd8\xff\xe0")[0] == "image/jpeg"
    assert sniff(b"MZ\x90\x00") is None and sniff(b"<html>") is None


def test_good_files_pass_technical_validation():
    assert technical_validate(digital_pdf([["a"], ["b"]]), "application/pdf", S).page_count == 2
    assert technical_validate(page_png(["x"]), "image/png", S).page_count == 1
    assert technical_validate(jpeg_of(["x"]), "image/jpeg", S).page_count == 1


@pytest.mark.parametrize(
    "data,media,code",
    [
        (b"", "application/pdf", "empty_file"),
        (b"plain text", "application/pdf", "unsupported_format"),
        (page_png(["x"]), "application/pdf", "unsupported_format"),  # content disagrees with the claimed type
        (corrupt_pdf(), "application/pdf", "pdf_corrupt"),
        (encrypted_pdf(), "application/pdf", "pdf_encrypted"),
        (b"\x89PNG\r\n\x1a\n" + b"junk" * 20, "image/png", "file_unreadable"),
    ],
    ids=["empty", "plain-text", "png-claimed-as-pdf", "truncated-pdf", "encrypted-pdf", "garbage-png"],
)
def test_bad_files_fail_with_a_specific_code(data, media, code):
    with pytest.raises(ProcessingError) as exc:
        technical_validate(data, media, S)
    assert exc.value.code == code
    assert exc.value.kind == ErrorKind.INPUT  # input problems never retry


def test_page_and_pixel_limits_are_enforced():
    with pytest.raises(ProcessingError) as exc:
        technical_validate(digital_pdf([["a"]] * 4), "application/pdf", Settings(environment="test", max_pdf_pages=3))
    assert exc.value.code == "too_many_pages"
    with pytest.raises(ProcessingError) as exc:
        technical_validate(page_png(["x"]), "image/png", Settings(environment="test", max_image_pixels=1000))
    assert exc.value.code == "image_too_large"


def test_every_error_code_has_a_safe_message_and_a_retry_class():
    for code, info in CATALOGUE.items():
        assert info.message and "/" not in info.message and "Traceback" not in info.message, code
        assert info.kind in ErrorKind


def test_previews_are_rasterised_pngs_and_range_checked():
    png = render_preview(digital_pdf([["hello"], ["world"]]), "application/pdf", 2, S)
    assert png.startswith(b"\x89PNG")
    with pytest.raises(ProcessingError):
        render_preview(digital_pdf([["hello"]]), "application/pdf", 2, S)


# ---- field extraction -------------------------------------------------------------------------------------------


def lines(*texts: str, method=PageMethod.PDF_TEXT, conf=None, page=1) -> PageLines:
    return PageLines(page, method, [OcrLine(t, conf) for t in texts], conf)


def test_value_after_label_on_same_line_and_on_next_line():
    r = extract_fields([INST], [lines("Name of the Institution: Test Institute of Technology")], 70)[0]
    assert (r.status, r.value, r.page_number, r.method) == (FieldStatus.EXTRACTED, "Test Institute of Technology", 1, PageMethod.PDF_TEXT)
    r = extract_fields([INST], [lines("Name of the Institution", "Test Institute of Technology")], 70)[0]
    assert r.status == FieldStatus.EXTRACTED and r.value == "Test Institute of Technology"


def test_labels_match_case_and_punctuation_insensitively():
    r = extract_fields([INST], [lines("NAME  OF THE  INSTITUTION - ACME College")], 70)[0]
    assert r.value == "ACME College"


def test_missing_field_is_not_found_and_nothing_is_invented():
    r = extract_fields([INST, DATE], [lines("Some unrelated text", "Another line")], 70)
    assert [x.status for x in r] == [FieldStatus.NOT_FOUND, FieldStatus.NOT_FOUND]
    assert all(x.value is None and x.normalised_value is None and x.page_number is None for x in r)


@pytest.mark.parametrize(
    "text,iso",
    [("Date: 12/03/2024", "2024-03-12"), ("Date: 12-03-2024", "2024-03-12"), ("Date: 2024-03-12", "2024-03-12"), ("Date: 12 March 2024", "2024-03-12"),
     ("Date: March 12, 2024", "2024-03-12"), ("Date: 12.3.24", "2024-03-12"), ("Date: 5th Sept 2023", "2023-09-05")],
)
def test_dates_are_normalised_to_iso(text, iso):
    r = extract_fields([DATE], [lines(text)], 70)[0]
    assert r.normalised_value == iso and r.status in (FieldStatus.EXTRACTED, FieldStatus.UNCERTAIN)


def test_an_unreadable_date_is_uncertain_not_a_guess():
    r = extract_fields([DATE], [lines("Date: sometime last spring")], 70)[0]
    assert r.status == FieldStatus.UNCERTAIN and r.normalised_value is None and "date" in r.note


def test_numeric_dates_are_read_day_first_and_say_so():
    r = extract_fields([DATE], [lines("Date: 04/05/2024")], 70)[0]
    assert r.normalised_value == "2024-05-04" and "day-first" in (r.note or "")


def test_identifier_extraction():
    r = extract_fields([REF], [lines("Ref No: ab/2024-117 (issued)")], 70)[0]
    assert r.value == "ab/2024-117" and r.normalised_value == "AB/2024-117"


def test_conflicting_values_in_one_document_are_uncertain():
    r = extract_fields([INST], [lines("Name of the Institution: Alpha College", page=1), lines("Name of the Institution: Beta College", page=2)], 70)[0]
    assert r.status == FieldStatus.UNCERTAIN and r.value == "Alpha College" and "2 different values" in r.note and r.alternatives == ["beta college"]


def test_same_value_repeated_is_fine():
    r = extract_fields([INST], [lines("Name of the Institution: Alpha College", page=1), lines("Name of the Institution: ALPHA  COLLEGE.", page=3)], 70)[0]
    assert r.status == FieldStatus.EXTRACTED and r.page_number == 1


def test_low_ocr_confidence_makes_a_value_uncertain_without_calling_it_wrong():
    r = extract_fields([INST], [lines("Name of the Institution: Alpha College", method=PageMethod.OCR, conf=41.0)], 70)[0]
    assert r.status == FieldStatus.UNCERTAIN and r.value == "Alpha College" and r.confidence == 41.0 and "low recognition confidence" in r.note
    ok = extract_fields([INST], [lines("Name of the Institution: Alpha College", method=PageMethod.OCR, conf=93.0)], 70)[0]
    assert ok.status == FieldStatus.EXTRACTED and ok.confidence == 93.0


def test_ocr_garbled_labels_still_match_but_are_flagged_uncertain():
    r = extract_fields([INST], [lines("Narne of the lnstitution: Alpha College", method=PageMethod.OCR, conf=95.0)], 70)[0]
    assert r.value == "Alpha College" and r.status == FieldStatus.UNCERTAIN and "approximately" in r.note
    digital = extract_fields([INST], [lines("Narne of the lnstitution: Alpha College")], 70)[0]
    assert digital.status == FieldStatus.NOT_FOUND  # digital text is exact: no fuzzy matching


def test_normalisation_is_transparent():
    assert normalise_text("ABC Institute of Technology & Management, Pvt. Ltd.") == "abc institute of technology and management pvt ltd"
    assert parse_date("nothing") == (None, None)


# ---- the pipeline ------------------------------------------------------------------------------------------------


def test_digital_pdf_yields_page_referenced_text_without_ocr():
    ocr = FakeOcr()
    out = run(digital_pdf([["Name of the Institution: Test Institute", "Date: 12/03/2024"], ["Reference No: AB-77 is here"]]), specs=[INST, DATE, REF], ocr=ocr)
    assert out.technical_status == TechnicalStatus.PASSED and out.extraction_status == ExtractionStatus.COMPLETE and out.page_count == 2
    assert [(p.page_number, p.method, p.status) for p in out.pages] == [(1, PageMethod.PDF_TEXT, PageStatus.OK), (2, PageMethod.PDF_TEXT, PageStatus.OK)]
    assert "Test Institute" in out.pages[0].text and "AB-77" in out.pages[1].text
    by = {f.field_key: f for f in out.fields}
    assert by["institution_name"].page_number == 1 and by["document_date"].normalised_value == "2024-03-12" and by["reference_number"].page_number == 2
    assert out.text_method == PageMethod.PDF_TEXT and ocr.calls == 0 and not out.review_recommended


def test_scanned_pdf_uses_ocr_per_page_with_confidence():
    ocr = FakeOcr([ocr_page(("Name of the Institution: Scan College", 92.0)), ocr_page(("Date: 01/02/2023", 88.0))])
    out = run(scanned_pdf([["page one"], ["page two"]]), specs=[INST, DATE], ocr=ocr)
    assert ocr.calls == 2 and out.text_method == PageMethod.OCR and out.pages_ocr == 2
    assert [(p.page_number, p.method, p.confidence) for p in out.pages] == [(1, PageMethod.OCR, 92.0), (2, PageMethod.OCR, 88.0)]
    by = {f.field_key: f for f in out.fields}
    assert by["institution_name"].method == PageMethod.OCR and by["institution_name"].confidence == 92.0 and by["document_date"].page_number == 2


def test_mixed_documents_report_both_methods():
    ocr = FakeOcr([ocr_page(("Date: 02/02/2022", 90.0))])
    out = run(mixed_pdf(["Name of the Institution: Mixed College"], ["scan"]), specs=[INST, DATE], ocr=ocr)
    assert out.text_method == "MIXED" and [p.method for p in out.pages] == [PageMethod.PDF_TEXT, PageMethod.OCR] and ocr.calls == 1


def test_low_confidence_and_empty_pages_stay_uncertain_and_request_review():
    ocr = FakeOcr([ocr_page(("Name of the Institution: Fuzzy College", 38.0)), OcrPage([], None)])
    out = run(scanned_pdf([["a"], ["b"]]), specs=[INST], ocr=ocr)
    assert [p.status for p in out.pages] == [PageStatus.LOW_CONFIDENCE, PageStatus.EMPTY]
    assert out.fields[0].status == FieldStatus.UNCERTAIN and out.review_recommended
    assert out.extraction_status == ExtractionStatus.COMPLETE  # reading finished; the uncertainty is carried, not hidden


def test_scanned_pages_without_an_ocr_engine_fail_visibly_rather_than_succeed_empty():
    out = run(scanned_pdf([["a"], ["b"]]), specs=[INST], ocr=FakeOcr(available=False))
    assert out.extraction_status == ExtractionStatus.FAILED and out.error_code == "ocr_unavailable" and out.review_recommended
    assert all(p.status == PageStatus.OCR_UNAVAILABLE and p.text == "" for p in out.pages) and out.fields == []
    assert CATALOGUE["ocr_unavailable"].kind == ErrorKind.CONFIG


def test_partial_when_only_some_pages_are_readable():
    out = run(mixed_pdf(["Name of the Institution: Part College"], ["scan"]), specs=[INST], ocr=FakeOcr(available=False))
    assert out.extraction_status == ExtractionStatus.PARTIAL and out.pages_unreadable == 1 and out.review_recommended
    assert out.fields[0].value == "Part College"


@pytest.mark.parametrize("data,code", [(corrupt_pdf(), "pdf_corrupt"), (encrypted_pdf(), "pdf_encrypted"), (b"%PDF-1.4\nnot really", "pdf_corrupt")], ids=["truncated", "encrypted", "header-only"])
def test_unreadable_pdfs_never_reach_ocr(data, code):
    ocr = FakeOcr()
    out = run(data, ocr=ocr, specs=[INST])
    assert out.technical_status == TechnicalStatus.FAILED and out.technical_code == code and out.extraction_status == ExtractionStatus.FAILED
    assert ocr.calls == 0 and out.pages == [] and out.fields == [] and out.scan is None


def test_image_files_are_ocr_input():
    ocr = FakeOcr([ocr_page(("Name of the Institution: Image College", 90.0))])
    out = run(page_png(["x"]), "image/png", specs=[INST], ocr=ocr)
    assert out.page_count == 1 and out.fields[0].value == "Image College" and ocr.calls == 1


def test_scanner_unavailable_is_reported_as_not_scanned_never_clean():
    out = run(digital_pdf([["hello world, enough text here"]]))
    assert out.scan.status == ScanStatus.SCANNER_UNAVAILABLE and out.scan.status != ScanStatus.CLEAN
    assert out.extraction_status == ExtractionStatus.COMPLETE  # development default: process, but flag


def test_scanning_required_holds_the_file_when_no_scanner_exists():
    out = run(digital_pdf([["hello world, enough text here"]]), settings=Settings(environment="test", require_malware_scan=True))
    assert out.extraction_status == ExtractionStatus.BLOCKED and out.error_code == "scan_required_unavailable" and out.pages == []
    assert CATALOGUE["scan_required_unavailable"].kind == ErrorKind.CONFIG


def test_infected_files_are_quarantined_and_not_processed():
    ocr = FakeOcr()
    out = run(digital_pdf([["hello world, enough text here"]]), scanner=Infected(), ocr=ocr)
    assert out.scan.status == ScanStatus.INFECTED and out.extraction_status == ExtractionStatus.BLOCKED and out.error_code == "malware_detected" and out.pages == []
    assert CATALOGUE["malware_detected"].kind == ErrorKind.INPUT  # permanent: never retried


def test_clean_scan_is_recorded_as_clean():
    assert run(digital_pdf([["hello world, enough text here"]]), scanner=Clean()).scan.status == ScanStatus.CLEAN


def test_transient_ocr_errors_propagate_for_the_job_layer_to_retry():
    ocr = FakeOcr(error=ProcessingError("ocr_timeout", "slow"))
    with pytest.raises(ProcessingError) as exc:
        run(scanned_pdf([["a"]]), ocr=ocr)
    assert exc.value.code == "ocr_timeout" and exc.value.kind == ErrorKind.TRANSIENT


# ---- OCR output and scanner protocol -----------------------------------------------------------------------------

TSV = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" + "\n".join([
    "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t",
    "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95.5\tName",
    "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t90.5\tof",
    "5\t1\t1\t1\t2\t1\t0\t0\t10\t10\t60\tTest",
    "5\t1\t1\t1\t2\t2\t0\t0\t10\t10\t-1\t",
])


def test_tesseract_tsv_parsing_gives_lines_and_mean_confidence():
    page = parse_tsv(TSV)
    assert [ln.text for ln in page.lines] == ["Name of", "Test"] and [ln.confidence for ln in page.lines] == [93.0, 60.0]
    assert page.mean_confidence == 82.0 and parse_tsv("").lines == []


def _fake_clamd(reply: bytes) -> tuple[int, threading.Thread, list[bytes]]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got: list[bytes] = []

    def serve() -> None:
        conn, _ = srv.accept()
        buf = b""
        while not buf.endswith(b"\x00\x00\x00\x00") or b"zINSTREAM\0" not in buf:
            part = conn.recv(65536)
            if not part:
                break
            buf += part
        got.append(buf)
        conn.sendall(reply)
        conn.close()
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv.getsockname()[1], t, got


def test_clamd_scanner_speaks_the_instream_protocol():
    """Verified against a fake clamd that implements INSTREAM framing; NOT against a real ClamAV."""
    port, t, got = _fake_clamd(b"stream: OK\0")
    r = ClamdScanner("127.0.0.1", port, 5).scan(b"hello" * 3000)
    t.join(2)
    assert r.status == ScanStatus.CLEAN and got[0].startswith(b"zINSTREAM\0") and got[0].endswith(struct.pack("!I", 0))
    port, t, _ = _fake_clamd(b"stream: Eicar-Test-Signature FOUND\0")
    r = ClamdScanner("127.0.0.1", port, 5).scan(b"x")
    assert r.status == ScanStatus.INFECTED and r.detail == "Eicar-Test-Signature"


def test_clamd_unreachable_or_confusing_replies_are_scanner_errors_not_clean():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    with pytest.raises(ProcessingError) as exc:
        ClamdScanner("127.0.0.1", closed_port, 1).scan(b"x")
    assert exc.value.code == "scanner_error" and exc.value.kind == ErrorKind.TRANSIENT
    port, t, _ = _fake_clamd(b"stream: size limit exceeded ERROR\0")
    with pytest.raises(ProcessingError):
        ClamdScanner("127.0.0.1", port, 5).scan(b"x")


# ---- preliminary checks ------------------------------------------------------------------------------------------


def doc(item: str, did: str, *, sha="a", tech="PASSED", scan="CLEAN", ext="COMPLETE", fields=(), pages=None, review=False, err=None, version=1) -> checks.DocFacts:
    return checks.DocFacts(did, version, sha, tech, "pdf_corrupt" if tech == "FAILED" else None, scan, ext, review, err,
                           pages if pages is not None else [checks.PageFact(1, "PDF_TEXT", "OK")], list(fields))


def fld(key, value, page=1, status="EXTRACTED", group=None, to_app=None, required=True) -> checks.FieldFact:
    return checks.FieldFact(key, key.replace("_", " ").title(), status, value, normalise_text(value) if value else None, page, required, group, to_app)


def item(key, d=None, required=True) -> checks.ItemFact:
    return checks.ItemFact(key, key.title(), required, "cl. 9" if key == "main" else None, True, d)


def app(items, submitted=True, inst="Test Institute") -> checks.AppFacts:
    return checks.AppFacts(inst, submitted, items)


def types(findings):
    return [f.issue_type for f in findings]


def test_missing_file_is_distinct_from_unreadable_file():
    f = checks.run_checks(app([item("main", None), item("extra", doc("extra", "d2", tech="FAILED"))]), 70)
    by = {x.item_key: x for x in f}
    assert by["main"].issue_type == "required_file_not_supplied" and by["extra"].issue_type == "unsupported_or_corrupt_file"
    assert "No file is attached" in by["main"].explanation and "could not be inspected" in by["extra"].explanation


def test_a_draft_with_no_file_yet_is_not_a_finding():
    assert checks.run_checks(app([item("main", None)], submitted=False), 70) == []


def test_extraction_failure_is_not_a_deficiency():
    f = checks.run_checks(app([item("main", doc("main", "d1", ext="FAILED", err="ocr_unavailable"))]), 70)
    assert types(f) == ["text_extraction_failed"]
    assert all("deficien" not in x.explanation.lower() for x in f)
    assert ISSUE_TYPES["text_extraction_failed"].priority == "medium"


def test_unfinished_documents_produce_nothing_yet():
    assert checks.run_checks(app([item("main", doc("main", "d1", ext="QUEUED"))]), 70) == []


def test_conflicting_values_across_documents_produce_a_finding_with_sources():
    a = doc("main", "d1", fields=[fld("institution_name", "Alpha College", page=2, group="inst")])
    b = doc("extra", "d2", sha="b", version=3, fields=[fld("institution_name", "Beta College", page=1, group="inst")])
    f = [x for x in checks.run_checks(app([item("main", a), item("extra", b)]), 70) if x.issue_type == "cross_document_inconsistency"]
    assert len(f) == 1 and f[0].check_key == "compare_group:inst" and f[0].pages == [1, 2]
    assert {(v["item_key"], v["document_id"], v["version"], v["page"], v["value"]) for v in f[0].values} == {("main", "d1", 1, 2, "Alpha College"), ("extra", "d2", 3, 1, "Beta College")}
    assert f[0].priority == "high" and f[0].review_recommended


def test_values_that_agree_after_normalisation_do_not_conflict():
    a = doc("main", "d1", fields=[fld("institution_name", "Alpha College & Co.", group="inst")])
    b = doc("extra", "d2", sha="b", fields=[fld("institution_name", "ALPHA COLLEGE AND CO", group="inst")])
    assert checks.run_checks(app([item("main", a), item("extra", b)]), 70) == []


def test_document_value_is_compared_with_the_application_record():
    d = doc("main", "d1", fields=[fld("institution_name", "Somewhere Else College", to_app="institution_name")])
    f = checks.run_checks(app([item("main", d)]), 70)
    assert types(f) == ["cross_document_inconsistency"] and f[0].check_key == "application_record"
    assert {v["source"] for v in f[0].values} == {"document", "application_record"}
    ok = doc("main", "d1", fields=[fld("institution_name", "test institute", to_app="institution_name")])
    assert checks.run_checks(app([item("main", ok)]), 70) == []


def test_uncertain_values_never_trigger_inconsistency():
    a = doc("main", "d1", fields=[fld("institution_name", "Alpha", group="inst", status="UNCERTAIN")])
    b = doc("extra", "d2", sha="b", fields=[fld("institution_name", "Beta", group="inst")])
    assert "cross_document_inconsistency" not in types(checks.run_checks(app([item("main", a), item("extra", b)]), 70))


def test_missing_expected_fields_and_uncertain_fields():
    d = doc("main", "d1", fields=[fld("document_date", None, page=None, status="NOT_FOUND"), fld("reference_number", None, status="NOT_FOUND", required=False), fld("institution_name", "X", status="UNCERTAIN")])
    f = checks.run_checks(app([item("main", d)]), 70)
    assert sorted(types(f)) == ["expected_field_not_found", "field_value_uncertain"]  # the optional missing field is not reported


def test_low_confidence_and_unreadable_pages_are_reported_with_page_numbers():
    pages = [checks.PageFact(1, "OCR", "OK", 90), checks.PageFact(2, "OCR", "LOW_CONFIDENCE", 40.0), checks.PageFact(3, "NONE", "OCR_UNAVAILABLE")]
    f = checks.run_checks(app([item("main", doc("main", "d1", pages=pages, ext="PARTIAL", review=True))]), 70)
    by = {x.issue_type: x for x in f}
    assert by["ocr_confidence_low"].pages == [2] and by["manual_inspection_recommended"].pages == [3]


def test_identical_files_on_different_requirements_are_flagged():
    f = checks.run_checks(app([item("main", doc("main", "d1", sha="same")), item("extra", doc("extra", "d2", sha="same"))]), 70)
    assert types(f) == ["potential_duplicate_file", "potential_duplicate_file"]


def test_scanner_unavailable_is_info_only_and_hidden_from_institutions():
    f = checks.run_checks(app([item("main", doc("main", "d1", scan="SCANNER_UNAVAILABLE"))]), 70)
    assert types(f) == ["scanner_unavailable"] and f[0].priority == "info" and not f[0].institute_visible and not f[0].review_recommended


def test_infected_files_get_a_single_high_priority_finding():
    f = checks.run_checks(app([item("main", doc("main", "d1", scan="INFECTED", ext="BLOCKED", err="malware_detected"))]), 70)
    assert types(f) == ["malware_detected"] and f[0].priority == "high"


def test_findings_are_deterministic_and_fingerprinted():
    mk = lambda: checks.run_checks(app([item("main", doc("main", "d1", fields=[fld("institution_name", "X", status="UNCERTAIN")]))]), 70)  # noqa: E731
    assert [f.fingerprint for f in mk()] == [f.fingerprint for f in mk()]


def test_findings_reference_the_checklist_item_and_clause():
    f = checks.run_checks(app([item("main", doc("main", "d1", tech="FAILED"))]), 70)
    assert f[0].rule_ref == "Main (cl. 9)" and f[0].item_key == "main" and f[0].document_id == "d1"


def test_issue_catalogue_is_complete_and_never_calls_anything_a_deficiency_notice():
    for t in ISSUE_TYPES.values():
        assert t.priority in ("info", "low", "medium", "high") and t.label and t.description
        assert "deficiency" not in (t.label + t.institute_text).lower()
        assert t.institute_text or not t.institute_visible
    assert "not a deficiency notice" in NOT_A_DEFICIENCY


# ---- real Tesseract (skipped when none is installed) ------------------------------------------------------------

REAL = real_tesseract()
needs_tesseract = pytest.mark.skipif(REAL is None, reason="no Tesseract executable (set TEST_TESSERACT_CMD / TEST_TESSDATA)")


@needs_tesseract
def test_real_tesseract_reads_a_scanned_pdf_and_extracts_fields_with_confidence():
    out = run_pipeline(
        scanned_pdf([["Name of the Institution: Riverdale Institute", "Date: 12/03/2024", "Reference No: RV-2024-117"]]), "application/pdf",
        [INST, DATE, REF], S, REAL, NoScanner(),
    )
    assert out.extraction_status == ExtractionStatus.COMPLETE and out.text_method == PageMethod.OCR and out.pages[0].confidence and out.pages[0].confidence > 60
    by = {f.field_key: f for f in out.fields}
    assert by["institution_name"].value and "Riverdale" in by["institution_name"].value
    assert by["document_date"].normalised_value == "2024-03-12" and by["reference_number"].normalised_value == "RV-2024-117"
    assert by["institution_name"].method == PageMethod.OCR and by["institution_name"].confidence is not None


@needs_tesseract
def test_real_tesseract_on_a_degraded_scan_flags_it_instead_of_inventing_a_value():
    bad = run_pipeline(blurry_png(["Name of the Institution: Riverdale Institute"]), "image/png", [INST], S, REAL, NoScanner())
    f = bad.fields[0]
    assert bad.page_count == 1 and bad.pages[0].status in (PageStatus.EMPTY, PageStatus.LOW_CONFIDENCE) and bad.review_recommended
    assert f.status in (FieldStatus.NOT_FOUND, FieldStatus.UNCERTAIN)  # never a confident EXTRACTED value from an unreadable page
    if f.status == FieldStatus.NOT_FOUND:
        assert f.value is None and f.normalised_value is None and f.page_number is None
    good = run_pipeline(page_png(["Name of the Institution: Riverdale Institute"]), "image/png", [INST], S, REAL, NoScanner())
    assert good.pages[0].confidence > 90 and good.fields[0].status == FieldStatus.EXTRACTED and not good.review_recommended

@needs_tesseract
def test_real_tesseract_mixed_pdf_uses_text_layer_where_present():
    out = run_pipeline(mixed_pdf(["Name of the Institution: Digital College"], ["Date: 05/06/2021"]), "application/pdf", [INST, DATE], S, REAL, NoScanner())
    assert out.text_method == "MIXED" and [p.method for p in out.pages] == [PageMethod.PDF_TEXT, PageMethod.OCR]
    by = {f.field_key: f for f in out.fields}
    assert by["institution_name"].method == PageMethod.PDF_TEXT and by["document_date"].normalised_value == "2021-06-05"
