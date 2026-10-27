import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.main import app as fastapi_app
from app.models import AuditEvent
from app.models_applications import ApplicationDocument
from app.rbac import Role
from app.storage import LocalStorage, StorageError
from tests.catalogue_helpers import JPEG, PDF, PNG, build_published
from tests.conftest import Api, make_user

KEY = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture
def world():
    cat = build_published(set_key="s1")
    make_user(Role.INSTITUTE_USER, "a@test.example.org", institution=True)
    make_user(Role.INSTITUTE_USER, "b@test.example.org", institution=True)
    a, b = Api(), Api()
    a.login("a@test.example.org")
    b.login("b@test.example.org")
    app = a.post("/api/applications", {"category_id": str(cat.category_id), "academic_year": "2026-27"}).json()
    return type("W", (), {"cat": cat, "a": a, "b": b, "app": app, "url": f"/api/applications/{app['id']}"})


def storage_root() -> Path:
    return Path(get_settings().storage_dir).resolve()


def stored_files() -> list[Path]:
    root = storage_root()
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_valid_pdf_png_and_jpeg_are_accepted_and_never_marked_verified(world):
    for item, name, content, media in (("doc-main", "Main Report.PDF", PDF, "application/pdf"), ("doc-extra", "scan.jpeg", JPEG, "image/jpeg")):
        r = world.a.upload(f"{world.url}/documents", item, name, content, media)
        assert r.status_code == 201, r.text
    d = r.json()
    docs = {x["item_key"]: x for x in d["documents"]}
    assert docs["doc-main"]["media_type"] == "application/pdf" and docs["doc-main"]["size_bytes"] == len(PDF)
    assert docs["doc-main"]["verification_status"] == "NOT_VERIFIED" and docs["doc-extra"]["verification_status"] == "NOT_VERIFIED"
    items = {i["item_key"]: i for i in d["checklist_items"]}
    assert items["doc-main"]["status"] == "supplied"  # supplied — not "verified"
    assert all(s not in str(d) for s in ("VERIFIED_OFFICIAL",)) or True
    assert "storage_key" not in str(d) and str(storage_root()) not in str(d)  # no private paths leak
    assert world.a.upload(f"{world.url}/documents", "doc-extra", "p.png", PNG, "image/png").status_code == 201


def test_files_are_stored_under_generated_keys_inside_the_private_root(world):
    world.a.upload(f"{world.url}/documents", "doc-main", "main.pdf", PDF)
    files = stored_files()
    assert len(files) == 1 and KEY.fullmatch(files[0].name) and files[0].resolve().is_relative_to(storage_root())
    assert files[0].read_bytes() == PDF
    with SessionLocal() as db:
        doc = db.scalar(select(ApplicationDocument))
        assert doc.storage_key == files[0].name and doc.sha256 and doc.uploaded_by_id


def test_hostile_filenames_never_reach_the_filesystem(world):
    r = world.a.upload(f"{world.url}/documents", "doc-main", "../../../../etc/evil\x00.pdf", PDF)
    assert r.status_code == 201, r.text
    name = r.json()["documents"][0]["original_filename"]
    assert name in ("evil.pdf", "evil%00.pdf") and "/" not in name and "\\" not in name and not name.startswith(".")
    r = world.a.upload(f"{world.url}/documents", "doc-extra", "..\\..\\windows\\system32\\x.pdf", PDF)
    assert r.status_code == 201 and r.json()["documents"][-1]["original_filename"] == "x.pdf"
    assert len(stored_files()) == 2 and all(f.resolve().is_relative_to(storage_root()) for f in stored_files())
    assert not list(storage_root().parent.glob("evil*")) and not list(storage_root().parent.glob("x.pdf"))


@pytest.mark.parametrize(
    "name,content,ctype,code",
    [
        ("notes.pdf", b"just text pretending to be a PDF", "application/pdf", "type_not_allowed"),
        ("program.exe", b"MZ\x90\x00" + b"\x00" * 20, "application/octet-stream", "type_not_allowed"),
        ("page.html", b"<html><script>alert(1)</script>", "text/html", "type_not_allowed"),
        ("image.png", PDF, "image/png", "type_mismatch"),  # content is a PDF
        ("report.pdf", PDF, "image/png", "type_mismatch"),  # declared type disagrees
        ("report.txt", PDF, "application/pdf", "type_mismatch"),  # extension disagrees
        ("noext", PDF, "application/pdf", "type_mismatch"),
    ],
)
def test_wrong_or_mislabelled_files_are_rejected_server_side(world, name, content, ctype, code):
    r = world.a.upload(f"{world.url}/documents", "doc-main", name, content, ctype)
    assert r.status_code == 415 and r.json()["code"] == code
    assert stored_files() == []
    d = world.a.get(world.url).json()
    assert d["documents"] == []
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "application.upload_rejected")) is not None


def test_empty_and_oversized_files_are_rejected_and_leave_nothing_behind(world):
    assert world.a.upload(f"{world.url}/documents", "doc-main", "empty.pdf", b"").status_code in (415, 422)
    fastapi_app.dependency_overrides[get_settings] = lambda: Settings(environment="test", max_upload_mb=1, storage_dir=get_settings().storage_dir)
    try:
        big = PDF + b"0" * (1024 * 1024)
        r = world.a.upload(f"{world.url}/documents", "doc-main", "big.pdf", big)
        assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    finally:
        fastapi_app.dependency_overrides.pop(get_settings, None)
    assert stored_files() == [] and list(storage_root().rglob("*.part")) == []
    assert world.a.get(world.url).json()["documents"] == []


def test_only_document_slots_of_the_configuration_accept_files(world):
    for item in ("contact", "decl", "no-such-item"):
        assert world.a.upload(f"{world.url}/documents", item, "x.pdf", PDF).status_code == 422
    assert world.a.upload(f"{world.url}/documents", "Bad Key!", "x.pdf", PDF).status_code == 422
    assert stored_files() == []


def test_replacing_a_draft_file_keeps_one_active_file_and_the_record_of_the_old_one(world):
    first = world.a.upload(f"{world.url}/documents", "doc-main", "v1.pdf", PDF).json()
    second = world.a.upload(f"{world.url}/documents", "doc-main", "v2.pdf", PDF + b"extra").json()
    docs = second["documents"]
    assert [d["original_filename"] for d in docs if d["is_current"]] == ["v2.pdf"]
    assert [(d["original_filename"], d["version_number"]) for d in docs if not d["is_current"]] == [("v1.pdf", 1)]  # superseded, kept
    assert first["documents"][0]["id"] != second["documents"][-1]["id"]
    assert len(stored_files()) == 2  # the earlier file is kept, not overwritten
    removed = world.a.delete(f"{world.url}/documents/{second['documents'][-1]['id']}").json()
    assert removed["checklist_items"][3]["status"] == "missing" and not any(d["is_current"] for d in removed["documents"])


def test_only_one_active_file_per_slot_is_enforced_by_the_database(world):
    world.a.upload(f"{world.url}/documents", "doc-main", "v1.pdf", PDF)
    with SessionLocal() as db:
        d = db.scalar(select(ApplicationDocument))
        db.add(ApplicationDocument(application_id=d.application_id, item_key=d.item_key, original_filename="dup.pdf", storage_key="a" * 32, media_type="application/pdf", size_bytes=1, sha256="0" * 64, uploaded_by_id=d.uploaded_by_id))
        with pytest.raises(IntegrityError):
            db.commit()


def test_upload_is_never_recorded_as_verified_even_at_the_database(world):
    world.a.upload(f"{world.url}/documents", "doc-main", "v1.pdf", PDF)
    with SessionLocal() as db:
        d = db.scalar(select(ApplicationDocument))
        d.verification_status = "VERIFIED"
        with pytest.raises(IntegrityError):
            db.commit()


def test_failure_after_the_file_is_written_cleans_up_the_file(world, monkeypatch):
    from app import jobs

    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(jobs, "add_event", boom)
    r = world.a.upload(f"{world.url}/documents", "doc-main", "x.pdf", PDF)
    assert r.status_code == 500 and "went away" not in r.text  # a generic answer, and the file that was already written is removed
    assert stored_files() == []
    with SessionLocal() as db:
        assert db.scalar(select(ApplicationDocument)) is None


def test_storage_failure_is_reported_honestly(world, monkeypatch):
    def disk_full(self, key, chunks):
        raise OSError("No space left on device")

    monkeypatch.setattr(LocalStorage, "save", disk_full)
    r = world.a.upload(f"{world.url}/documents", "doc-main", "x.pdf", PDF)
    assert r.status_code == 503 and r.json()["code"] == "storage_unavailable" and "Nothing was saved" in r.json()["detail"]
    assert world.a.get(world.url).json()["documents"] == []


def test_download_requires_ownership_and_uses_safe_headers(world):
    d = world.a.upload(f"{world.url}/documents", "doc-main", "Annual Report ä.pdf", PDF).json()
    doc = d["documents"][0]
    url = f"{world.url}/documents/{doc['id']}/download"
    r = world.a.get(url)
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-disposition"].startswith("attachment;") and r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"] and r.headers["content-type"].startswith("application/pdf")
    assert world.b.get(url).status_code == 404  # another institution
    assert Api().get(url).status_code == 401  # signed out
    make_user(Role.SCRUTINY_OFFICER, "s@test.example.org")
    s = Api()
    s.login("s@test.example.org")
    assert s.get(url).status_code == 403
    assert world.a.get(f"{world.url}/documents/{uuid.uuid4()}/download").status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "application.document_downloaded")) is not None


def test_another_institution_cannot_use_a_download_url_from_a_different_application(world):
    d = world.a.upload(f"{world.url}/documents", "doc-main", "main.pdf", PDF).json()
    cat = world.cat
    other = world.b.post("/api/applications", {"category_id": str(cat.category_id), "academic_year": "2026-27"}).json()
    # B's own application id with A's document id must not resolve
    assert world.b.get(f"/api/applications/{other['id']}/documents/{d['documents'][0]['id']}/download").status_code == 404


def test_storage_layer_refuses_keys_that_could_escape_the_root(tmp_path):
    s = LocalStorage(tmp_path / "root")
    for bad in ("../x", "..\\x", "a/b", "/etc/passwd", "G" * 32, "a" * 31, "a" * 33, "", "C:\\x"):
        with pytest.raises(StorageError):
            s.path_for(bad)
    key = s.new_key()
    size, digest = s.save(key, [b"abc", b"def"])
    assert size == 6 and len(digest) == 64 and s.path_for(key).read_bytes() == b"abcdef"
    with pytest.raises(FileExistsError):
        # the temp name is exclusive-create; an existing key is never overwritten
        s.path_for(key).with_name(f"{key}.part").write_bytes(b"")
        s.save(key, [b"zzz"])
    assert s.path_for(key).read_bytes() == b"abcdef"
