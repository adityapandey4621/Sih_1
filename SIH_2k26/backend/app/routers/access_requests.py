from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import csrf_protect
from app.models import AccessRequest, AuditEvent, utcnow
from app.schemas import AccessRequestIn, AccessRequestReceivedOut
from app.security import normalise_email

router = APIRouter(prefix="/api/access-requests", tags=["access-requests"], dependencies=[Depends(csrf_protect)])

SUBMISSIONS_PER_IP_PER_HOUR = 10
PENDING_PER_EMAIL = 3


@router.post("", response_model=AccessRequestReceivedOut, status_code=status.HTTP_201_CREATED)
def submit_access_request(body: AccessRequestIn, request: Request, db: Session = Depends(get_db)) -> AccessRequestReceivedOut:
    """Public. Records a *request* only; it never creates a user or grants a role."""
    ip = audit.client_ip(request)
    if ip:
        recent = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "access_request.submitted",
                AuditEvent.ip_address == ip,
                AuditEvent.occurred_at >= utcnow() - timedelta(hours=1),
            )
        )
        if (recent or 0) >= SUBMISSIONS_PER_IP_PER_HOUR:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please try again later.")

    email = normalise_email(body.applicant_email)
    pending = db.scalar(
        select(func.count())
        .select_from(AccessRequest)
        .where(AccessRequest.applicant_email == email, AccessRequest.status == "pending")
    )
    # Over the cap we still answer "received" so the form cannot be used to probe the queue.
    if (pending or 0) < PENDING_PER_EMAIL:
        req = AccessRequest(
            institution_name=body.institution_name,
            institution_identifier=body.institution_identifier,
            applicant_name=body.applicant_name,
            applicant_email=email,
            reason=body.reason,
        )
        db.add(req)
        db.flush()
        audit.record(db, "access_request.submitted", request=request, actor_label=email, target_type="access_request", target_id=req.id)
    db.commit()
    return AccessRequestReceivedOut()
