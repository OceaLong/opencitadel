"""Published phase counts preserve unknowns and the purpose/summary separation."""

from decimal import Decimal

from app.application.dto.execution_view import UsagePurposeSummary
from app.infrastructure.execution.postgres_execution_usage import usage_summary


def test_summary_costs_are_separate_and_unknowns_do_not_disappear():
    def fact(kind, cost, purpose="production"):
        return {
            "purpose": purpose,
            "input_tokens": 10 if cost is not None else None,
            "output_tokens": 2 if cost is not None else None,
            "cost_usd": Decimal(cost) if cost is not None else None,
            "coverage": {"call_kind": kind},
        }

    records = [
        fact("main_answer", "0.12"),
        fact("context_summary", "0.003"),
        fact("context_summary", None),
        fact("other", "0"),
        fact("context_summary", "0.2", "evaluation_subject"),
    ]
    before = usage_summary(records)
    product = UsagePurposeSummary.model_validate(before["production"])
    assert product.known_cost_usd == "0.123"
    assert product.calls == 4
    assert product.unknown_cost_calls == 1
    assert product.groups["main_answer"].calls == 1
    assert product.groups["context_summary"].calls == 2
    assert product.groups["context_summary"].known_cost_usd == "0.003"
    assert product.groups["context_summary"].unknown_usage_calls == 1
    assert product.groups["other"].unknown_cost_calls == 0
    assert before["evaluation_subject"]["groups"]["context_summary"]["known_cost_usd"] == "0.2"
    records[2] = fact("context_summary", "0.004")
    assert usage_summary(records)["production"]["known_cost_usd"] == "0.127"
    assert before["production"]["known_cost_usd"] == "0.123"
