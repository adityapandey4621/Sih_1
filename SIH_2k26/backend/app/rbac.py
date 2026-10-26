"""Single source of truth for roles and what each role may do.

The frontend receives the permission list for the signed-in user and derives
navigation from it; it never carries its own role table. Every protected
endpoint re-checks permissions here.

A role never implies another role's permissions. In particular `rules_admin`
has no decision permissions and `auditor` has read-only permissions.
"""

from enum import StrEnum


class Role(StrEnum):
    INSTITUTE_USER = "institute_user"
    SCRUTINY_OFFICER = "scrutiny_officer"
    EVC_COORDINATOR = "evc_coordinator"
    EVC_MEMBER = "evc_member"
    SHC_MEMBER = "shc_member"
    EXECUTIVE_COMMITTEE = "executive_committee"
    SAC_MEMBER = "sac_member"
    RULES_ADMIN = "rules_admin"
    AUDITOR = "auditor"
    PLATFORM_ADMIN = "platform_admin"


class Permission(StrEnum):
    DASHBOARD_INSTITUTE = "dashboard:institute"
    DASHBOARD_SCRUTINY = "dashboard:scrutiny"
    DASHBOARD_EVC_COORDINATION = "dashboard:evc-coordination"
    DASHBOARD_EVC_MEMBER = "dashboard:evc-member"
    DASHBOARD_SHC = "dashboard:shc"
    DASHBOARD_EXECUTIVE = "dashboard:executive"
    DASHBOARD_APPEALS = "dashboard:appeals"
    DASHBOARD_RULES = "dashboard:rules"
    DASHBOARD_AUDIT = "dashboard:audit"
    DASHBOARD_ADMIN = "dashboard:admin"

    ADMIN_ACCESS_REQUESTS = "admin:access-requests"
    ADMIN_USERS = "admin:users"
    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.INSTITUTE_USER: frozenset({Permission.DASHBOARD_INSTITUTE}),
    Role.SCRUTINY_OFFICER: frozenset({Permission.DASHBOARD_SCRUTINY}),
    Role.EVC_COORDINATOR: frozenset({Permission.DASHBOARD_EVC_COORDINATION}),
    Role.EVC_MEMBER: frozenset({Permission.DASHBOARD_EVC_MEMBER}),
    Role.SHC_MEMBER: frozenset({Permission.DASHBOARD_SHC}),
    Role.EXECUTIVE_COMMITTEE: frozenset({Permission.DASHBOARD_EXECUTIVE}),
    Role.SAC_MEMBER: frozenset({Permission.DASHBOARD_APPEALS}),
    Role.RULES_ADMIN: frozenset({Permission.DASHBOARD_RULES}),
    Role.AUDITOR: frozenset({Permission.DASHBOARD_AUDIT, Permission.AUDIT_READ}),
    Role.PLATFORM_ADMIN: frozenset(
        {
            Permission.DASHBOARD_ADMIN,
            Permission.ADMIN_ACCESS_REQUESTS,
            Permission.ADMIN_USERS,
            Permission.AUDIT_READ,
        }
    ),
}

ROLE_LABELS: dict[Role, str] = {
    Role.INSTITUTE_USER: "Institution",
    Role.SCRUTINY_OFFICER: "Scrutiny officer",
    Role.EVC_COORDINATOR: "EVC coordinator",
    Role.EVC_MEMBER: "EVC member",
    Role.SHC_MEMBER: "SHC member",
    Role.EXECUTIVE_COMMITTEE: "Executive Committee",
    Role.SAC_MEMBER: "SAC member",
    Role.RULES_ADMIN: "Rules administrator",
    Role.AUDITOR: "Auditor",
    Role.PLATFORM_ADMIN: "Platform administrator",
}


def permissions_for(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()
