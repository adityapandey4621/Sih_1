"""Immutability guards enforced at flush time.

The services already refuse these edits; this is the second line of defence so that a
future code path (or a bug) cannot silently rewrite a reviewed rule version or a
submitted application. Raw SQL bypasses ORM hooks, so these guards do not replace
careful service code — they catch the mistakes the services are most likely to make.
"""

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.models_applications import Application, ApplicationDocument
from app.models_catalogue import ChecklistItem, DeadlinePolicy, RouteStage, RuleSource, RuleVersion, RuleVersionSource


class ImmutableRecordError(RuntimeError):
    """Raised when code tries to modify a record that has been frozen by the workflow."""


# Content that is frozen as soon as a version leaves DRAFT.
_VERSION_CONTENT = ("rule_set_id", "version_number", "academic_years", "effective_from", "effective_to", "change_summary", "supersedes_version_id")
# Frozen once published (they are written during the in_review -> published transition).
_VERSION_FROZEN_AFTER_PUBLISH = ("snapshot", "content_hash")
_CHILDREN = (ChecklistItem, RouteStage, DeadlinePolicy, RuleVersionSource)


def _original(obj, attr: str):
    hist = inspect(obj).attrs[attr].history
    if hist.deleted:
        return hist.deleted[0]
    return hist.unchanged[0] if hist.unchanged else None


def _changed(obj, attr: str) -> bool:
    return inspect(obj).attrs[attr].history.has_changes()


@event.listens_for(Session, "before_flush")
def _guard(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, _CHILDREN):
            version = obj.version if obj.version is not None else (session.get(RuleVersion, obj.rule_version_id) if obj.rule_version_id else None)
            if version is not None and version not in session.new and version.status != "draft":
                raise ImmutableRecordError(f"Rule version is {version.status}; its content cannot be changed. Create a new version.")

        elif isinstance(obj, RuleVersion):
            if obj in session.new:
                continue
            if obj in session.deleted:
                if _original(obj, "status") != "draft":
                    raise ImmutableRecordError("Only draft rule versions can be deleted.")
                continue
            original_status = _original(obj, "status")
            if original_status != "draft" and any(_changed(obj, f) for f in _VERSION_CONTENT):
                raise ImmutableRecordError(f"Rule version is {original_status}; its content cannot be changed. Create a new version.")
            if original_status in ("published", "retired") and any(_changed(obj, f) for f in _VERSION_FROZEN_AFTER_PUBLISH):
                raise ImmutableRecordError("A published rule version's snapshot cannot be changed.")

        elif isinstance(obj, RuleSource):
            if obj in session.new:
                continue
            used_by_reviewed = session.scalar(
                select(RuleVersion.id)
                .join(RuleVersionSource, RuleVersionSource.rule_version_id == RuleVersion.id)
                .where(RuleVersionSource.source_id == obj.id, RuleVersion.status != "draft")
                .limit(1)
            )
            if used_by_reviewed is not None:
                raise ImmutableRecordError("This source is cited by a rule version that is in review or published; add a new source instead.")

        elif isinstance(obj, Application):
            if obj in session.new:
                continue
            if _original(obj, "status") == "SUBMITTED":
                if obj in session.deleted or any(inspect(obj).attrs[a.key].history.has_changes() for a in inspect(obj).mapper.column_attrs):
                    raise ImmutableRecordError("A submitted application cannot be modified.")

        elif isinstance(obj, ApplicationDocument):
            app = session.get(Application, obj.application_id)
            if app is not None and _original(app, "status") == "SUBMITTED":
                raise ImmutableRecordError("Documents of a submitted application cannot be changed.")
