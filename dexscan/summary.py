"""Turn DEX Screener pair payloads into comparable rows."""

from __future__ import annotations

from dataclasses import dataclass


def mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def as_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_ms(value: object) -> int | None:
    """DEX Screener sends pairCreatedAt in milliseconds. Accept seconds too."""
    number = as_float(value)
    if number is None or number <= 0:
        return None
    if number < 10**12:
        number *= 1000
    return int(number)


def extract_pairs(payload: object) -> list[dict]:
    """Accept the envelopes the public API actually returns.

    Search and pair-by-address use ``{"pairs": [...]}``. The pair endpoint
    also includes a singular ``pair`` object; when both are present the list
    is the source of truth so the same pool is not counted twice. Token-pair
    responses are a bare list. A single pair object is accepted for hand-saved
    fixtures.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object or array of pairs")
    pairs = payload.get("pairs", _MISSING)
    if pairs is not _MISSING:
        if pairs is None:
            single = payload.get("pair")
            return [single] if isinstance(single, dict) else []
        if isinstance(pairs, list):
            return [item for item in pairs if isinstance(item, dict)]
        raise ValueError("'pairs' must be a list or null")
    pair = payload.get("pair", _MISSING)
    if pair is not _MISSING:
        if pair is None:
            return []
        if isinstance(pair, dict):
            return [pair]
        raise ValueError("'pair' must be an object or null")
    if "pairAddress" in payload and "baseToken" in payload:
        return [payload]
    raise ValueError(
        "unrecognized DEX Screener payload "
        "(expected 'pairs', 'pair', or a pair object)"
    )


class _Missing:
    pass


_MISSING = _Missing()


def format_age(seconds: float) -> str:
    whole = int(max(0, seconds))
    days, rem = divmod(whole, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def addresses_equal(left: str, right: str) -> bool:
    if left.lower().startswith("0x") and right.lower().startswith("0x"):
        return left.lower() == right.lower()
    return left == right


@dataclass(frozen=True)
class PairRow:
    chain: str
    dex: str
    symbol: str
    name: str
    quote: str
    pair_address: str
    base_address: str
    price_usd: float | None
    price_usd_text: str | None
    liquidity_usd: float | None
    volume_h24: float | None
    vol_liq: float | None
    buys_h24: int
    sells_h24: int
    age: str
    age_seconds: float | None
    pair_created_at: int | None
    url: str
    price_change_h24: float | None

    def to_json(self, flags: list[str]) -> dict:
        if self.price_usd_text is not None:
            price: object = self.price_usd_text
        else:
            price = self.price_usd
        return {
            "symbol": self.symbol,
            "name": self.name,
            "quote": self.quote,
            "chain": self.chain,
            "dex": self.dex,
            "priceUsd": price,
            "liquidityUsd": self.liquidity_usd,
            "volumeH24": self.volume_h24,
            "volLiq": self.vol_liq,
            "buysH24": self.buys_h24,
            "sellsH24": self.sells_h24,
            "age": self.age,
            "ageSeconds": self.age_seconds,
            "pairCreatedAt": self.pair_created_at,
            "pairAddress": self.pair_address,
            "baseAddress": self.base_address,
            "url": self.url,
            "priceChangeH24": self.price_change_h24,
            "flags": flags,
        }


def summarize_pair(pair: dict, now_ms: int) -> PairRow:
    base = mapping(pair.get("baseToken"))
    quote = mapping(pair.get("quoteToken"))
    liquidity = mapping(pair.get("liquidity"))
    volume = mapping(pair.get("volume"))
    txns = mapping(mapping(pair.get("txns")).get("h24"))
    change = mapping(pair.get("priceChange"))
    liq = as_float(liquidity.get("usd"))
    vol = as_float(volume.get("h24"))
    ratio = vol / liq if liq is not None and liq > 0 and vol is not None else None
    created = as_ms(pair.get("pairCreatedAt"))
    if created is None:
        age, age_seconds = "n/a", None
    else:
        age_seconds = max(0.0, (now_ms - created) / 1000)
        age = format_age(age_seconds)
    price_text = pair.get("priceUsd")
    text = price_text.strip() if isinstance(price_text, str) else None
    if text == "":
        text = None
    return PairRow(
        chain=str(pair.get("chainId") or ""),
        dex=str(pair.get("dexId") or ""),
        symbol=(str(base.get("symbol") or "").strip() or "?"),
        name=str(base.get("name") or "").strip(),
        quote=(str(quote.get("symbol") or "").strip() or "?"),
        pair_address=str(pair.get("pairAddress") or ""),
        base_address=str(base.get("address") or ""),
        price_usd=as_float(price_text),
        price_usd_text=text,
        liquidity_usd=liq,
        volume_h24=vol,
        vol_liq=ratio,
        buys_h24=as_int(txns.get("buys")),
        sells_h24=as_int(txns.get("sells")),
        age=age,
        age_seconds=age_seconds,
        pair_created_at=created,
        url=str(pair.get("url") or ""),
        price_change_h24=as_float(change.get("h24")),
    )


@dataclass(frozen=True)
class View:
    chain: str | None = None
    min_liq: float = 0.0
    min_vol_liq: float | None = None
    base: str | None = None
    sort: str = "vol24"
    ascending: bool = False
    limit: int | None = 20
    thin_liq: float = 5000.0
    hot_vol_liq: float = 5.0
    new_seconds: float = 3600.0


@dataclass(frozen=True)
class ScanResult:
    shown: list[PairRow]
    loaded: int
    matched: int
    sum_liq: float
    sum_vol: float

    @property
    def blended_vol_liq(self) -> float | None:
        if self.sum_liq > 0:
            return self.sum_vol / self.sum_liq
        return None


def flags_for(row: PairRow, view: View) -> list[str]:
    found: list[str] = []
    if row.liquidity_usd is not None and row.liquidity_usd < view.thin_liq:
        found.append("thin")
    if row.vol_liq is not None and row.vol_liq >= view.hot_vol_liq:
        found.append("hot")
    if row.age_seconds is not None and row.age_seconds < view.new_seconds:
        found.append("new")
    return found


def _sort_key(row: PairRow, view: View) -> tuple:
    if view.sort == "age":
        missing = row.age_seconds is None
        value = 0.0 if row.age_seconds is None else row.age_seconds
        # Default is newest first. --asc shows the oldest first.
        ordered = value if not view.ascending else -value
        return (missing, ordered)
    if view.sort == "txns":
        value = float(row.buys_h24 + row.sells_h24)
        missing = False
    else:
        attr = {
            "vol24": "volume_h24",
            "liq": "liquidity_usd",
            "vol_liq": "vol_liq",
            "price": "price_usd",
        }[view.sort]
        raw = getattr(row, attr)
        missing = raw is None
        value = 0.0 if raw is None else float(raw)
    ordered = value if view.ascending else -value
    return (missing, ordered)


def scan_rows(rows: list[PairRow], view: View) -> ScanResult:
    matched: list[PairRow] = []
    chain = view.chain.lower() if view.chain else None
    for row in rows:
        if chain and row.chain.lower() != chain:
            continue
        if view.base and not addresses_equal(row.base_address, view.base):
            continue
        if view.min_liq and (row.liquidity_usd is None or row.liquidity_usd < view.min_liq):
            continue
        if view.min_vol_liq is not None and (
            row.vol_liq is None or row.vol_liq < view.min_vol_liq
        ):
            continue
        matched.append(row)
    matched.sort(key=lambda row: _sort_key(row, view))
    shown = matched if view.limit is None else matched[: view.limit]
    sum_liq = sum(row.liquidity_usd or 0.0 for row in matched)
    sum_vol = sum(row.volume_h24 or 0.0 for row in matched)
    return ScanResult(
        shown=shown,
        loaded=len(rows),
        matched=len(matched),
        sum_liq=sum_liq,
        sum_vol=sum_vol,
    )
