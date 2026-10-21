from pydantic import BaseModel


class PriceHistoryStatistics(BaseModel):
    """Population statistics over actual daily EUR/Cardmarket observations."""
    mean: float | None = None
    standardDeviation: float | None = None
    observations: int = 0
    firstObservedOn: str | None = None
    lastObservedOn: str | None = None
    provider: str = "cardmarket"
    currency: str = "EUR"
