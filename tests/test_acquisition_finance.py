from decimal import Decimal
from src.services.acquisition_finance import purchase_totals


def test_cardmarket_total_includes_shipping_in_the_commission():
    totals = purchase_totals('cardmarket', Decimal('40'), Decimal('5'))
    assert totals == {'subtotal': Decimal('40.00'), 'shipping': Decimal('5.00'),
                      'fee': Decimal('2.55'), 'total': Decimal('47.55')}


def test_proportional_cost_distribution_preserves_every_cent_and_unknown_weights():
    from src.services.acquisition_finance import distribute_cost
    assert distribute_cost(Decimal('1.00'), [1, 1, 1]) == [Decimal('0.34'), Decimal('0.33'), Decimal('0.33')]
    assert distribute_cost(Decimal('40'), [10, 30]) == [Decimal('10.00'), Decimal('30.00')]
    assert distribute_cost(Decimal('40'), [None, 30]) == [None, None]


def test_budget_borrows_future_months_but_does_not_carry_between_years():
    from datetime import date
    from src.services.acquisition_finance import budget_summary
    rows = [dict(acquiredOn='2026-01-12', investment=100, costBasis='paid'),
            dict(acquiredOn='2025-12-31', investment=500, costBasis='paid'),
            dict(acquiredOn='2026-02-12', investment=501, costBasis='trend')]
    result = budget_summary(rows, 2026, date(2026, 2, 12))
    assert result['spent'] == 601
    assert result['remaining'] == -1
    assert result['excess'] == 1
    assert result['estimatedSpent'] == 501
    assert result['months'][0]['spent'] == 100
    assert result['months'][1]['balance'] == -501


def test_historical_purchase_requires_total_and_date_and_rejects_future_dates():
    import pytest
    from datetime import timedelta
    from pydantic import ValidationError
    from src.schemas.acquisitions import HistoricalQuoteInput, local_today
    base = dict(rawText='1 Sol Ring', name='Precon', selections=[dict(line=1, collectionCardId='c', quantity=1)])
    with pytest.raises(ValidationError):
        HistoricalQuoteInput(**base)
    with pytest.raises(ValidationError):
        HistoricalQuoteInput(**base, total=40, acquiredOn=local_today()+timedelta(days=1))
    assert HistoricalQuoteInput(**base, total=40, acquiredOn=local_today()).total == 40
