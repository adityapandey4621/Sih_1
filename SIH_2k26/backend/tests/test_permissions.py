import pytest

from app.rbac import ROLE_PERMISSIONS, Permission, Role

ADMIN_ENDPOINTS = [
    ("/api/admin/access-requests", Permission.ADMIN_ACCESS_REQUESTS),
    ("/api/admin/users", Permission.ADMIN_USERS),
    ("/api/admin/invitations", Permission.ADMIN_USERS),
    ("/api/audit/events", Permission.AUDIT_READ),
]


def test_every_role_has_a_permission_entry():
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_rules_admin_has_no_decision_or_administration_permissions():
    perms = ROLE_PERMISSIONS[Role.RULES_ADMIN]
    assert perms == {Permission.DASHBOARD_RULES}


def test_auditor_is_read_only():
    perms = ROLE_PERMISSIONS[Role.AUDITOR]
    assert perms == {Permission.DASHBOARD_AUDIT, Permission.AUDIT_READ}


def test_each_role_has_exactly_one_dashboard_permission():
    for role, perms in ROLE_PERMISSIONS.items():
        assert len([p for p in perms if p.startswith("dashboard:")]) == 1, role


@pytest.mark.parametrize("path,needed", ADMIN_ENDPOINTS)
def test_admin_endpoints_require_authentication(api, path, needed):
    assert api.get(path).status_code == 401


@pytest.mark.parametrize("path,needed", ADMIN_ENDPOINTS)
@pytest.mark.parametrize("role", list(Role))
def test_admin_endpoints_enforce_permissions_server_side(api, make, path, needed, role):
    make(role, f"{role.value}@test.example.org", institution=role is Role.INSTITUTE_USER)
    assert api.login(f"{role.value}@test.example.org").status_code == 200
    expected = 200 if needed in ROLE_PERMISSIONS[role] else 403
    assert api.get(path).status_code == expected


def test_client_supplied_role_is_ignored(api, make):
    make(Role.INSTITUTE_USER, "inst@test.example.org", institution=True)
    r = api.post(
        "/api/auth/login",
        {"email": "inst@test.example.org", "password": "correct horse battery staple", "role": "platform_admin"},
    )
    assert r.json()["role"] == "institute_user"
    assert api.get("/api/admin/users", headers={"X-Role": "platform_admin"}).status_code == 403


def test_me_exposes_institution_scope_from_stored_record(api, make):
    make(Role.INSTITUTE_USER, "inst@test.example.org", institution=True)
    me = api.login("inst@test.example.org").json()
    assert me["institution"]["name"] == "Test Institute"
