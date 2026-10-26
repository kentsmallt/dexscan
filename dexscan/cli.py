"""Command line for dexscan."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

from dexscan import __version__
from dexscan.client import DexscreenerClient, DexscreenerError
from dexscan.format import format_stats, render_table
from dexscan.summary import View, extract_pairs, flags_for, scan_rows, summarize_pair

_SORTS = ("vol24", "liq", "vol_liq", "age", "txns", "price")


def _finite(label: str, *, positive: bool = False):
    def parse(raw: str) -> float:
        try:
            value = float(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label} must be a number") from exc
        if not math.isfinite(value) or value < 0 or (positive and value == 0):
            bound = "> 0" if positive else ">= 0"
            raise argparse.ArgumentTypeError(f"{label} must be a finite number {bound}")
        return value

    return parse


def _limit(raw: str) -> int | None:
    if raw == "all":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer >= 0 or 'all'") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("limit must be an integer >= 0 or 'all'")
    return value


def _rpm(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--max-rpm must be an integer") from exc
    if value < 1 or value > 300:
        raise argparse.ArgumentTypeError("--max-rpm must be from 1 to 300")
    return value


def _add_view(parser: argparse.ArgumentParser, *, chain_required: bool) -> None:
    parser.add_argument(
        "--chain",
        required=chain_required,
        help="Chain id such as bsc, ethereum, solana, or base",
    )
    parser.add_argument(
        "--min-liq",
        type=_finite("min-liq"),
        default=0.0,
        metavar="USD",
        help="Drop pools below this USD liquidity (default 0)",
    )
    parser.add_argument(
        "--min-vol-liq",
        type=_finite("min-vol-liq"),
        default=None,
        metavar="RATIO",
        help="Drop pools below this 24h volume / liquidity ratio",
    )
    parser.add_argument(
        "--base",
        metavar="ADDRESS",
        help="Keep pools whose base token address matches",
    )
    parser.add_argument("--sort", choices=_SORTS, default="vol24")
    parser.add_argument(
        "--asc",
        action="store_true",
        help="Reverse the default order (default: largest first, newest first for age)",
    )
    parser.add_argument(
        "--limit",
        type=_limit,
        default=20,
        help="Rows to print (default 20, or 'all')",
    )
    parser.add_argument(
        "--thin-liq",
        type=_finite("thin-liq"),
        default=5000.0,
        metavar="USD",
        help="Flag liquidity below this (default 5000)",
    )
    parser.add_argument(
        "--hot-vol-liq",
        type=_finite("hot-vol-liq"),
        default=5.0,
        metavar="RATIO",
        help="Flag vol/liq at or above this (default 5)",
    )
    parser.add_argument(
        "--new-hours",
        type=_finite("new-hours"),
        default=1.0,
        metavar="HOURS",
        help="Flag pools younger than this (default 1)",
    )
    parser.add_argument("--json", action="store_true", help="Write JSON to stdout")
    parser.add_argument("--no-url", action="store_true", help="Omit the pair URL column")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dexscan",
        description=(
            "Summarize DEX Screener pools: price, liquidity, 24h volume, "
            "vol/liq, age, and buys/sells. No API key."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m dexscan scan samples/gicat.json\n"
            "  python -m dexscan scan samples/marscoin.json --chain bsc "
            "--min-liq 1000 --min-vol-liq 5 --sort vol_liq\n"
            "  python -m dexscan search GICAT --chain bsc --limit 10\n"
            "  python -m dexscan token 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c "
            "--chain bsc --min-liq 10000\n"
            "  python -m dexscan pair 0x3E86D0daC826504c4039D9D0a7005EcEDEB3E36c "
            "--chain bsc\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"dexscan {__version__}")
    commands = parser.add_subparsers(dest="command")

    scan = commands.add_parser("scan", help="Summarize saved DEX Screener JSON")
    scan.add_argument(
        "files",
        nargs="+",
        help="JSON files from the pair, search, or token-pair endpoints. Use - for stdin.",
    )
    _add_view(scan, chain_required=False)

    search = commands.add_parser("search", help="Search the public pair endpoint")
    search.add_argument("query", help="Symbol, name, or address")
    _add_view(search, chain_required=False)
    _add_live(search)

    token = commands.add_parser("token", help="List pools for one token address")
    token.add_argument("address")
    _add_view(token, chain_required=True)
    _add_live(token)

    pair = commands.add_parser("pair", help="Fetch one pair by chain and pair address")
    pair.add_argument("address")
    _add_view(pair, chain_required=True)
    _add_live(pair)
    return parser


def _add_live(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--timeout",
        type=_finite("timeout", positive=True),
        default=20.0,
        help="HTTP timeout in seconds (default 20)",
    )
    parser.add_argument(
        "--max-rpm",
        type=_rpm,
        default=250,
        help="Client cap under the documented 300/min pair limit (default 250)",
    )


def _view_from(args: argparse.Namespace) -> View:
    return View(
        chain=args.chain,
        min_liq=args.min_liq,
        min_vol_liq=args.min_vol_liq,
        base=args.base,
        sort=args.sort,
        ascending=args.asc,
        limit=args.limit,
        thin_liq=args.thin_liq,
        hot_vol_liq=args.hot_vol_liq,
        new_seconds=args.new_hours * 3600,
    )


def _load_file(path: str) -> list[dict]:
    if path == "-":
        payload = json.load(sys.stdin)
    else:
        file_path = Path(path)
        with file_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    return extract_pairs(payload)


def _emit(rows: list, view: View, *, as_json: bool, show_url: bool) -> None:
    result = scan_rows(rows, view)
    stats = format_stats(result)
    if as_json:
        payload = [row.to_json(flags_for(row, view)) for row in result.shown]
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        print(stats, file=sys.stderr)
        return
    if result.shown:
        print(render_table(result.shown, view, show_url=show_url))
    print(stats)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args_list = sys.argv[1:] if argv is None else argv
    if not args_list:
        parser.print_help()
        return 0
    args = parser.parse_args(args_list)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        now_ms = int(time.time() * 1000)
        if args.command == "scan":
            pairs: list[dict] = []
            for path in args.files:
                pairs.extend(_load_file(path))
        else:
            client = DexscreenerClient(max_calls=args.max_rpm, timeout=args.timeout)
            if args.command == "search":
                pairs, endpoint = client.search(args.query)
            elif args.command == "token":
                pairs, endpoint = client.token_pairs(args.chain, args.address)
            else:
                pairs, endpoint = client.pair(args.chain, args.address)
            noun = "pair" if len(pairs) == 1 else "pairs"
            print(f"fetched {len(pairs)} {noun} from {endpoint}", file=sys.stderr)
        rows = [summarize_pair(pair, now_ms) for pair in pairs]
        _emit(rows, _view_from(args), as_json=args.json, show_url=not args.no_url)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 0
    except DexscreenerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
