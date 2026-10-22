"""Purchase counts derived from live rows, without files or network access."""

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class PurchaseHistory:
    totals: Counter[str]
    by_live: dict[str, Counter[str]]
    incomplete: bool


def normalize_customer(value):
    if not isinstance(value, str):
        return ""
    return value.strip().lstrip("@").strip().casefold()


def count_customer_rows(rows):
    counts = Counter()
    if not isinstance(rows, (list, tuple)):
        return counts
    for row in rows:
        if not isinstance(row, dict):
            continue
        customer = normalize_customer(row.get("cliente"))
        if customer:
            counts[customer] += 1
    return counts


def build_purchase_history(lives):
    totals = Counter()
    by_live = {}
    incomplete = False
    seen = set()
    if not isinstance(lives, (list, tuple)):
        return PurchaseHistory(totals, by_live, True)

    for live in lives:
        if not isinstance(live, dict):
            incomplete = True
            continue
        live_id = live.get("id")
        if not isinstance(live_id, str) or not live_id.strip():
            live_id = None
            incomplete = True
        elif live_id in seen:
            continue
        else:
            seen.add(live_id)

        rows = live.get("rows")
        if not isinstance(rows, list):
            incomplete = True
            continue
        if any(not isinstance(row, dict)
               or (row.get("cliente") is not None and not isinstance(row.get("cliente"), str))
               for row in rows):
            incomplete = True
        counts = count_customer_rows(rows)
        totals.update(counts)
        if live_id is not None:
            by_live[live_id] = counts

    return PurchaseHistory(totals, by_live, incomplete)


def effective_purchase_counts(history, current_live_id, current_rows):
    counts = history.totals.copy()
    if isinstance(current_live_id, str):
        counts.subtract(history.by_live.get(current_live_id, {}))
    counts.update(count_customer_rows(current_rows))
    return +counts
