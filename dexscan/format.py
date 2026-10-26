"""Plain-text table and one-line scan stats. Standard library only."""

from __future__ import annotations

import unicodedata

from dexscan.summary import PairRow, ScanResult, View, flags_for


def _width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _width(text) <= width:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + char_width > width - 1:
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + "…"


def _pad(text: str, width: int, align: str) -> str:
    gap = width - _width(text)
    if gap <= 0:
        return text
    if align == "right":
        return (" " * gap) + text
    return text + (" " * gap)


def fmt_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        body = f"{magnitude / 1_000_000_000:.2f}B"
    elif magnitude >= 1_000_000:
        body = f"{magnitude / 1_000_000:.2f}M"
    elif magnitude >= 1_000:
        body = f"{magnitude / 1_000:.2f}k"
    elif magnitude >= 1:
        body = f"{magnitude:.2f}"
    else:
        body = f"{magnitude:.4f}".rstrip("0").rstrip(".") or "0"
    return f"{sign}${body}"


def fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1000 or value <= -1000:
        return fmt_usd(value)
    magnitude = abs(value)
    if magnitude >= 1:
        body = f"{magnitude:.4f}".rstrip("0").rstrip(".")
    elif magnitude == 0:
        body = "0"
    else:
        body = f"{magnitude:.6g}"
    sign = "-" if value < 0 else ""
    return f"{sign}${body}"


def fmt_ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 100:
        body = f"{magnitude:.0f}"
    elif magnitude >= 10:
        body = f"{magnitude:.1f}"
    elif magnitude >= 0.01:
        body = f"{magnitude:.2f}"
    else:
        body = f"{magnitude:.4f}".rstrip("0").rstrip(".") or "0"
    return f"{sign}{body}"


def fmt_ratio_detail(value: float | None) -> str:
    """Two decimals for the footer, with extra digits when the ratio is tiny."""
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 0.01 or magnitude == 0:
        body = f"{magnitude:.2f}"
    else:
        body = f"{magnitude:.4f}".rstrip("0").rstrip(".") or "0"
    return f"{sign}{body}"


def format_stats(result: ScanResult) -> str:
    blended = fmt_ratio_detail(result.blended_vol_liq)
    return (
        f"{len(result.shown)} shown of {result.matched} matched "
        f"({result.loaded} loaded). "
        f"matched liquidity {fmt_usd(result.sum_liq)}, "
        f"24h volume {fmt_usd(result.sum_vol)}, "
        f"blended vol/liq {blended}."
    )


def render_table(rows: list[PairRow], view: View, *, show_url: bool = True) -> str:
    headers = [
        "SYMBOL/QUOTE",
        "CHAIN",
        "DEX",
        "PRICE",
        "LIQ",
        "VOL24",
        "V/L",
        "AGE",
        "BUYS/SELLS",
        "FLAGS",
    ]
    caps = [24, 12, 14, 14, 12, 12, 8, 10, 18, 14]
    numeric = {3, 4, 5, 6}
    body: list[list[str]] = []
    urls: list[str] = []
    for row in rows:
        marks = flags_for(row, view)
        body.append(
            [
                f"{row.symbol}/{row.quote}",
                row.chain or "-",
                row.dex or "-",
                fmt_price(row.price_usd),
                fmt_usd(row.liquidity_usd),
                fmt_usd(row.volume_h24),
                fmt_ratio(row.vol_liq),
                row.age,
                f"{row.buys_h24}/{row.sells_h24}",
                ",".join(marks) if marks else "-",
            ]
        )
        urls.append(row.url or "-")
    widths: list[int] = []
    for index, header in enumerate(headers):
        width = _width(header)
        for cells in body:
            width = max(width, _width(cells[index]))
        widths.append(min(width, caps[index]))

    def line(cells: list[str]) -> str:
        padded = []
        for index, cell in enumerate(cells):
            clipped = _clip(cell, widths[index])
            align = "right" if index in numeric else "left"
            padded.append(_pad(clipped, widths[index], align))
        return "  ".join(padded)

    lines = [line(headers), "  ".join("-" * width for width in widths)]
    for index, cells in enumerate(body):
        row_text = line(cells)
        if show_url:
            row_text = f"{row_text}  {urls[index]}"
        lines.append(row_text.rstrip())
    if show_url:
        lines[0] = f"{lines[0]}  URL"
    else:
        lines[0] = lines[0].rstrip()
    return "\n".join(lines)
