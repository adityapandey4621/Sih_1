"""Single source of truth for roles and what each role may do.

The frontend receives the permission list for the signed-in user and derives
navigation from it; it never carries its own role table. Every protected
endpoint re-checks permissions here.

A role never implies another role's permissions. In particular `rules_admin`
(author) cannot publish and `rules_approver` cannot edit drafts; neither has
decision permissions, and `auditor` has read-only permissions.
"""

from enum import StrEnum


class Role(StrEnum):
    INSTITUTE_USER = "institute_user"
    SCRUTINY_OFFICER = "scrutiny_officer"  # the assigned reviewer / committee member (application capability, not an AICTE designation)
    SCRUTINY_COORDINATOR = "scrutiny_coordinator"  # manages queue metadata and the authorised assignment boundary
    NOTICE_ISSUER = "notice_issuer"  # may issue a formal deficiency notice; never granted implicitly
    EVC_COORDINATOR = "evc_coordinator"
    EVC_MEMBER = "evc_member"
    SHC_COORDINATOR = "shc_coordinator"  # prepares SHC cases, requests compliance, coordinates hearings (application capability)
    SHC_MEMBER = "shc_member"
    EXECUTIVE_COMMITTEE = "executive_committee"
    SAC_COORDINATOR = "sac_coordinator"  # arranges the SAC review of an appeal (application capability)
    SAC_MEMBER = "sac_member"
    REGULATION_BUREAU = "regulation_bureau"  # records Bureau observations, issues the official outcome (application capability, not a claimed AICTE designation)
    COUNCIL_MEMBER = "council_member"  # records Council ratification and Council decisions on appeals (application capability)
    RULES_ADMIN = "rules_admin"
    RULES_APPROVER = "rules_approver"
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

    # Phase 2 — institution applications (always scoped to the user's own institution)
    APPLICATION_CREATE = "application:create"
    APPLICATION_READ_OWN = "application:read_own"
    APPLICATION_UPDATE_OWN = "application:update_own"
    APPLICATION_SUBMIT = "application:submit"

    # Phase 3 — evidence (read-only for reviewers; no scrutiny actions exist yet)
    EVIDENCE_READ = "evidence:read"  # view submitted applications' documents, extraction results and preliminary findings
    DOCUMENT_RETRY = "document:retry"  # ask for a failed processing job to be tried again (only where retrying can help)

    # Phase 4 — scrutiny. Every one of these is further limited by case scope (assignment) in app/scrutiny_access.py.
    SCRUTINY_QUEUE = "scrutiny:queue"  # see the queue entries within one's scope
    SCRUTINY_ASSIGN = "scrutiny:assign"  # record an authorised assignment (coordinator)
    SCRUTINY_WORK = "scrutiny:work"  # record findings, prepare notices, complete a round (assigned reviewer)
    SCRUTINY_ROUTE_DETERMINE = "scrutiny:route_determine"  # choose among the next stages the frozen route allows (coordinator)
    NOTICE_ISSUE = "notice:issue"  # issue / return a formal deficiency notice (authorised issuer only)
    NOTICE_READ_OWN = "notice:read_own"  # institution: read notices addressed to its own applications
    NOTICE_RESPOND = "notice:respond"  # institution: respond to its own issued notices
    NOTIFICATIONS_READ = "notifications:read"  # read one's own in-app notifications

    # Phase 5 - committee processes. Each is further limited by case scope in app/committee_access.py.
    EVC_QUEUE = "evc:queue"  # see EVC cases within one's scope
    EVC_COORDINATE = "evc:coordinate"  # selection requests, scheduling, amendments, completing the stage, next-stage determination
    EVC_WORK = "evc:work"  # an assigned member: visit, observations, evidence, report
    SHC_QUEUE = "shc:queue"
    SHC_COORDINATE = "shc:coordinate"  # prepare requirements, request compliance, schedule hearings, complete the stage, determination
    SHC_WORK = "shc:work"  # an assigned member: findings, proceedings, recommendation
    SHC_RESPOND = "shc:respond"  # institution: answer a compliance request for its own application
    COMMITTEE_STATUS_OWN = "committee:status_own"  # institution: see EVC / SHC status of its own applications

    # Phase 6 - decisions and appeals. No role holds all of them; none is granted to an administrator.
    DECISION_QUEUE = "decision:queue"  # see decision cases (read-only records)
    DECISION_OBSERVE = "decision:observe"  # Regulation Bureau: record observations
    DECISION_RECORD = "decision:record"  # Executive Committee: record or correct a decision
    DECISION_ISSUE = "decision:issue"  # Regulation Bureau: issue the official outcome and close the case
    COUNCIL_RECORD = "council:record"  # Council: record ratification and Council decisions on appeals
    DECISION_READ_OWN = "decision:read_own"  # institution: official status of its own applications
    APPEAL_SUBMIT = "appeal:submit"  # institution: submit one appeal for its own application
    SAC_QUEUE = "sac:queue"
    SAC_COORDINATE = "sac:coordinate"
    SAC_WORK = "sac:work"

    # Phase 2 — rule catalogue
    RULES_READ = "rules:read"
    RULES_EDIT = "rules:edit"  # author: create/edit drafts, submit for review
    RULES_APPROVE = "rules:approve"  # approver: request changes, publish, retire


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.INSTITUTE_USER: frozenset(
        {
            Permission.DASHBOARD_INSTITUTE,
            Permission.APPLICATION_CREATE,
            Permission.APPLICATION_READ_OWN,
            Permission.APPLICATION_UPDATE_OWN,
            Permission.APPLICATION_SUBMIT,
            Permission.DOCUMENT_RETRY,
            Permission.NOTICE_READ_OWN,
            Permission.NOTICE_RESPOND,
            Permission.SHC_RESPOND,
            Permission.COMMITTEE_STATUS_OWN,
            Permission.DECISION_READ_OWN,
            Permission.APPEAL_SUBMIT,
            Permission.NOTIFICATIONS_READ,
        }
    ),
    # The reviewer works assigned cases only. They can prepare a notice for issuance but cannot issue one.
    Role.SCRUTINY_OFFICER: frozenset(
        {
            Permission.DASHBOARD_SCRUTINY, Permission.EVIDENCE_READ, Permission.DOCUMENT_RETRY,
            Permission.SCRUTINY_QUEUE, Permission.SCRUTINY_WORK, Permission.NOTIFICATIONS_READ,
        }
    ),
    # Manages the queue and the assignment boundary; no document contents, no findings, no notices.
    Role.SCRUTINY_COORDINATOR: frozenset(
        {
            Permission.DASHBOARD_SCRUTINY, Permission.SCRUTINY_QUEUE, Permission.SCRUTINY_ASSIGN,
            Permission.SCRUTINY_ROUTE_DETERMINE, Permission.NOTIFICATIONS_READ,
        }
    ),
    # Inspects a prepared notice (and the evidence behind it) before deciding to issue it.
    Role.NOTICE_ISSUER: frozenset(
        {
            Permission.DASHBOARD_SCRUTINY, Permission.EVIDENCE_READ, Permission.SCRUTINY_QUEUE,
            Permission.NOTICE_ISSUE, Permission.NOTIFICATIONS_READ,
        }
    ),
    # Committee coordinators arrange the process; members work only the cases they are assigned. Neither can decide anything.
    Role.EVC_COORDINATOR: frozenset({Permission.DASHBOARD_EVC_COORDINATION, Permission.EVC_QUEUE, Permission.EVC_COORDINATE, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.EVC_MEMBER: frozenset({Permission.DASHBOARD_EVC_MEMBER, Permission.EVC_QUEUE, Permission.EVC_WORK, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.SHC_COORDINATOR: frozenset({Permission.DASHBOARD_SHC, Permission.SHC_QUEUE, Permission.SHC_COORDINATE, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.SHC_MEMBER: frozenset({Permission.DASHBOARD_SHC, Permission.SHC_QUEUE, Permission.SHC_WORK, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.EXECUTIVE_COMMITTEE: frozenset({Permission.DASHBOARD_EXECUTIVE, Permission.DECISION_QUEUE, Permission.DECISION_RECORD, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.REGULATION_BUREAU: frozenset({Permission.DASHBOARD_EXECUTIVE, Permission.DECISION_QUEUE, Permission.DECISION_OBSERVE, Permission.DECISION_ISSUE, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.COUNCIL_MEMBER: frozenset({Permission.DASHBOARD_EXECUTIVE, Permission.DECISION_QUEUE, Permission.COUNCIL_RECORD, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.SAC_COORDINATOR: frozenset({Permission.DASHBOARD_APPEALS, Permission.SAC_QUEUE, Permission.SAC_COORDINATE, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.SAC_MEMBER: frozenset({Permission.DASHBOARD_APPEALS, Permission.SAC_QUEUE, Permission.SAC_WORK, Permission.EVIDENCE_READ, Permission.NOTIFICATIONS_READ}),
    Role.RULES_ADMIN: frozenset({Permission.DASHBOARD_RULES, Permission.RULES_READ, Permission.RULES_EDIT}),
    Role.RULES_APPROVER: frozenset({Permission.DASHBOARD_RULES, Permission.RULES_READ, Permission.RULES_APPROVE}),
    Role.AUDITOR: frozenset({Permission.DASHBOARD_AUDIT, Permission.AUDIT_READ, Permission.RULES_READ}),
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
    Role.SCRUTINY_COORDINATOR: "Scrutiny coordinator",
    Role.NOTICE_ISSUER: "Notice issuer",
    Role.EVC_COORDINATOR: "EVC coordinator",
    Role.EVC_MEMBER: "EVC member",
    Role.SHC_COORDINATOR: "SHC coordinator",
    Role.SHC_MEMBER: "SHC member",
    Role.EXECUTIVE_COMMITTEE: "Executive Committee",
    Role.SAC_COORDINATOR: "SAC coordinator",
    Role.SAC_MEMBER: "SAC member",
    Role.REGULATION_BUREAU: "Regulation Bureau",
    Role.COUNCIL_MEMBER: "Council member",
    Role.RULES_ADMIN: "Rules administrator",
    Role.RULES_APPROVER: "Rules approver",
    Role.AUDITOR: "Auditor",
    Role.PLATFORM_ADMIN: "Platform administrator",
}


def permissions_for(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()
