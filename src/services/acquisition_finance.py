"""Public monetary rules for acquisition previews and the expense ledger."""
from decimal import Decimal, ROUND_HALF_UP


def cents(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def purchase_totals(origin: str, subtotal, shipping=0, *, charge_fixed_fee=True):
    subtotal, shipping = cents(subtotal), cents(shipping)
    fee = cents((subtotal + shipping) * Decimal('0.05')) if origin == 'cardmarket' else Decimal('0.00')
    if origin == 'cardmarket' and charge_fixed_fee:
        fee += Decimal('0.30')
    return dict(subtotal=subtotal, shipping=shipping, fee=fee, total=subtotal + shipping + fee)


def distribute_cost(total, weights):
    """Largest remainders distribute exact cents, with stable input-order ties."""
    if not weights or any(w is None for w in weights):
        return [None for _ in weights]
    amounts = [Decimal(str(w)) for w in weights]
    denominator = sum(amounts)
    if denominator <= 0:
        return [None for _ in weights]
    raw = [cents(total) * 100 * w / denominator for w in amounts]
    parts = [int(w) for w in raw]
    rest = int(cents(total) * 100) - sum(parts)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - parts[i]), i))
    for i in order[:rest]:
        parts[i] += 1
    return [Decimal(p) / 100 for p in parts]


def budget_summary(rows, year, today):
    months = [dict(month=i, allowance=50, spent=0.0, estimatedSpent=0.0) for i in range(1, 13)]
    for row in rows:
        acquired = str(row['acquiredOn'])[:10]
        if int(acquired[:4]) != year or row.get('annulledAt') or row.get('currency', 'EUR') != 'EUR':
            continue
        month = months[int(acquired[5:7]) - 1]
        month['spent'] += float(row['investment'])
        if row.get('costBasis') == 'trend':
            month['estimatedSpent'] += float(row['investment'])
    cumulative = Decimal(0)
    for month in months:
        month['spent'] = float(cents(month['spent']))
        month['estimatedSpent'] = float(cents(month['estimatedSpent']))
        cumulative += Decimal(str(month['spent']))
        month['balance'] = float(cents(month['month'] * 50 - cumulative))
        month['overMonthlyReference'] = month['spent'] > 50
    spent = cents(sum(Decimal(str(m['spent'])) for m in months))
    return dict(year=year, annualLimit=600, monthlyReference=50, spent=float(spent),
                estimatedSpent=float(cents(sum(m['estimatedSpent'] for m in months))),
                remaining=float(600-spent), excess=float(max(Decimal(0), spent-600)), months=months)


def valuation_revision(analysis):
    """A revision covers exactly the quantities, prints and values visible in an offer."""
    import hashlib
    import json
    rows = [(c.cardName, c.quantity, c.selectedPrintingId, c.unitPrice, c.totalPrice)
            for c in analysis.cards]
    purchase = [(c.cardName, c.quantity, c.referencePrintingId, c.isFoil, c.purchaseUnitPrice, c.marketUnitPrice)
                for c in analysis.purchaseAnalysis.cards] if analysis.purchaseAnalysis else []
    return hashlib.sha256(json.dumps([analysis.rawText, analysis.origin, analysis.printingOverrides,
                                      rows, purchase], sort_keys=True).encode()).hexdigest()
