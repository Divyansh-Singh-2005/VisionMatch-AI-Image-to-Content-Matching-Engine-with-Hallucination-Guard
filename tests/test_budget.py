import pytest

from app.services import cost


def test_budget_guard_blocks_at_limit(monkeypatch):
    monkeypatch.setattr(cost, "calls_today", lambda session, tenant_id: 500)
    with pytest.raises(cost.BudgetExceeded):
        cost.ensure_budget(None, 1, 500)


def test_budget_guard_allows_below_limit(monkeypatch):
    monkeypatch.setattr(cost, "calls_today", lambda session, tenant_id: 499)
    cost.ensure_budget(None, 1, 500)


def test_estimate_cost_known_and_unknown_model():
    assert cost.estimate_cost("gemini-2.5-flash", 1_000_000, 0) == 0.30
    assert cost.estimate_cost("unknown-model", 1000, 1000) == 0.0