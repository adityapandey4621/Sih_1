"""The EVC and SHC processes end to end through the real API, and what the route engine does at each step."""

import pytest

from tests.committee_world import make_cworld

pytestmark = pytest.mark.filterwarnings("ignore")


def test_evc_then_conditional_shc_happy_path():
    c = make_cworld("evc_then_shc_conditional")
    c.finish_scrutiny()
    assert c.row("EVC") is not None and c.row("SHC") is None
    d = c.case("EVC")
    assert d["state"] == "SELECTION_PENDING" and d["selection"]["availability"]["demo"] is True
    c.evc_select()
    c.schedule()
    d = c.confirm()
    assert d["state"] == "SCHEDULED"
    c.start_visit()
    c.complete_checklist()
    c.save_report()
    d = c.submit_report()
    assert d["state"] == "REPORT_SUBMITTED"
    d = c.complete_evc()
    assert d["state"] == "COMPLETED" and d["next"]["status"] == "AWAITING_DETERMINATION"
