"""Offline tests against the bundled DEX Screener fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dexscan.format import fmt_ratio, fmt_usd  # noqa: E402
from dexscan.client import (  # noqa: E402
    MAX_BODY,
    DexscreenerClient,
    DexscreenerError,
    _PinHost,
)
from dexscan.summary import (  # noqa: E402
    View,
    extract_pairs,
    flags_for,
    scan_rows,
    summarize_pair,
)

SAMPLES = ROOT / "samples"
MARS = "0xFe189E97832DA1573e4e4Ff034F4fFC3a15c7777"


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self._body = body
        self._off = 0
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = len(self._body) - self._off
        chunk = self._body[self._off : self._off + n]
        self._off += len(chunk)
        return chunk

    def close(self) -> None:
        return None

    def getcode(self) -> int:
        return self.status


def _load(name: str) -> list[dict]:
    return extract_pairs(json.loads((SAMPLES / name).read_text(encoding="utf-8")))


class SummaryTests(unittest.TestCase):
    def test_gicat_ratio_and_txns(self) -> None:
        created = 1767008392000
        now = created + (86400 + 2 * 3600) * 1000
        row = summarize_pair(_load("gicat.json")[0], now)
        self.assertEqual(row.symbol, "GICAT")
        self.assertEqual(row.quote, "WBNB")
        self.assertEqual(row.chain, "bsc")
        self.assertEqual(row.price_usd_text, "0.002729")
        self.assertEqual(row.buys_h24, 1247)
        self.assertEqual(row.sells_h24, 753)
        self.assertAlmostEqual(row.vol_liq, 22403.79 / 42289.23)
        self.assertEqual(row.age, "1d2h")
        self.assertEqual(flags_for(row, View()), [])

    def test_age_accepts_unix_seconds(self) -> None:
        created = 1_700_000_000
        row = summarize_pair(
            {"pairAddress": "p", "baseToken": {"symbol": "A"}, "pairCreatedAt": created},
            (created + 90) * 1000,
        )
        self.assertEqual(row.age, "1m")

    def test_zero_liquidity_has_no_ratio(self) -> None:
        row = summarize_pair(
            {
                "pairAddress": "p",
                "baseToken": {},
                "liquidity": {"usd": 0},
                "volume": {"h24": 10},
            },
            0,
        )
        self.assertIsNone(row.vol_liq)
        self.assertEqual(row.symbol, "?")

    def test_envelopes(self) -> None:
        pair = {"pairAddress": "a", "baseToken": {"symbol": "A"}}
        self.assertEqual(extract_pairs({"pairs": None}), [])
        self.assertEqual(len(extract_pairs({"pairs": [pair], "pair": pair})), 1)
        self.assertEqual(extract_pairs({"pairs": None, "pair": pair}), [pair])
        self.assertEqual(extract_pairs([pair, "skip"])[0]["pairAddress"], "a")
        self.assertEqual(extract_pairs(pair)[0]["pairAddress"], "a")
        with self.assertRaises(ValueError):
            extract_pairs({"nope": True})

    def test_marscoin_hot_pools(self) -> None:
        rows = [summarize_pair(pair, 1_798_000_000_000) for pair in _load("marscoin.json")]
        view = View(
            chain="BSC",
            base=MARS.lower(),
            min_liq=1000,
            min_vol_liq=5,
            sort="vol_liq",
            limit=None,
        )
        result = scan_rows(rows, view)
        self.assertEqual(result.matched, 5)
        self.assertEqual(result.shown[0].quote, "USDT")
        self.assertGreater(result.shown[0].vol_liq, result.shown[1].vol_liq)
        self.assertTrue(all(row.vol_liq >= 5 and row.liquidity_usd >= 1000 for row in result.shown))
        self.assertIn("hot", flags_for(result.shown[0], view))
        self.assertLess(result.shown[0].liquidity_usd, 300_000)

    def test_chain_filter_excludes_other_chains(self) -> None:
        rows = [summarize_pair(pair, 0) for pair in _load("gicat.json")]
        result = scan_rows(rows, View(chain="solana"))
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.loaded, 1)

    def test_compact_numbers(self) -> None:
        self.assertEqual(fmt_usd(42289.23), "$42.29k")
        self.assertEqual(fmt_usd(20_445_330.88), "$20.45M")
        self.assertEqual(fmt_ratio(72.696), "72.7")
        self.assertEqual(fmt_ratio(0.00323), "0.0032")
        self.assertEqual(fmt_ratio(None), "n/a")

    def test_samples_have_no_secret_markers(self) -> None:
        for path in SAMPLES.glob("*.json"):
            text = path.read_text(encoding="utf-8").lower()
            for marker in ("private key", "mnemonic", "seed phrase", "api_key", "secret"):
                self.assertNotIn(marker, text, path.name)


class ClientTests(unittest.TestCase):
    def test_search_url_and_pace(self) -> None:
        class Clock:
            def __init__(self) -> None:
                self.t = 100.0

            def __call__(self) -> float:
                return self.t

        clock = Clock()
        slept: list[float] = []

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            clock.t += seconds

        seen: list[object] = []

        def opener(req, timeout=None):
            seen.append(req)
            return FakeResponse(b'{"pairs":[]}')

        client = DexscreenerClient(
            opener=opener, sleep=sleep, now=clock, max_calls=1, period=60
        )
        client.search("SOL/USDC")
        client.search("b")
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 60.0)
        self.assertIn("q=SOL%2FUSDC", seen[0].full_url)
        self.assertTrue(seen[0].full_url.startswith("https://api.dexscreener.com/latest/dex/search?"))
        self.assertIn("dexscan/", seen[0].get_header("User-agent"))

    def test_retries_429_once(self) -> None:
        seq = [
            FakeResponse(b"", status=429, headers={"Retry-After": "100"}),
            FakeResponse(
                b'{"pairs":[{"pairAddress":"p","baseToken":{"symbol":"A"}}]}'
            ),
        ]

        def opener(req, timeout=None):
            return seq.pop(0)

        slept: list[float] = []
        client = DexscreenerClient(opener=opener, sleep=slept.append, max_calls=100)
        pairs, _endpoint = client.search("A")
        self.assertEqual(slept, [30.0])
        self.assertEqual(pairs[0]["baseToken"]["symbol"], "A")

    def test_http_error_and_oversize(self) -> None:
        def opener(req, timeout=None):
            return FakeResponse(b"no", status=500)

        client = DexscreenerClient(opener=opener, sleep=lambda _s: None, max_calls=10)
        with self.assertRaises(DexscreenerError):
            client.search("A")

        class Big:
            def __init__(self) -> None:
                self.done = False
                self.status = 200
                self.headers: dict = {}

            def read(self, _n: int = -1) -> bytes:
                if self.done:
                    return b""
                self.done = True
                return b"x" * (MAX_BODY + 1)

            def close(self) -> None:
                return None

            def getcode(self) -> int:
                return 200

        client = DexscreenerClient(opener=lambda req, timeout=None: Big(), max_calls=10)
        with self.assertRaises(DexscreenerError):
            client.pair("bsc", "0xabc")

    def test_rejects_path_injection_and_redirects(self) -> None:
        calls: list[str] = []

        def opener(req, timeout=None):
            calls.append(req.full_url)
            return FakeResponse(b'{"pairs":[]}')

        client = DexscreenerClient(opener=opener, max_calls=10)
        with self.assertRaises(ValueError):
            client.pair("bsc", "../etc/passwd")
        with self.assertRaises(ValueError):
            client.token_pairs("bsc", "not an address")
        self.assertEqual(calls, [])
        with self.assertRaises(DexscreenerError):
            _PinHost().redirect_request(None, None, 302, "moved", {}, "https://evil.example/x")


class CliTests(unittest.TestCase):
    def _run(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "dexscan", *args],
            cwd=ROOT,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_module_scan_prints_table(self) -> None:
        proc = self._run(
            "scan",
            "samples/marscoin.json",
            "--chain",
            "bsc",
            "--base",
            MARS,
            "--min-liq",
            "1000",
            "--min-vol-liq",
            "5",
            "--sort",
            "vol_liq",
            "--limit",
            "3",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SYMBOL/QUOTE", proc.stdout)
        self.assertIn("MarsCoin/USDT", proc.stdout)
        self.assertIn("hot", proc.stdout)
        self.assertIn("3 shown of 5 matched", proc.stdout)
        self.assertNotIn("error", proc.stderr.lower())

    def test_json_and_stdin(self) -> None:
        raw = (SAMPLES / "gicat.json").read_text(encoding="utf-8")
        proc = self._run("scan", "-", "--json", "--min-liq", "1000", stdin=raw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload[0]["symbol"], "GICAT")
        self.assertEqual(payload[0]["buysH24"], 1247)
        self.assertIn("1 shown of 1 matched", proc.stderr)

    def test_bad_file_exits_nonzero(self) -> None:
        proc = self._run("scan", "samples/missing.json")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("error:", proc.stderr)

    def test_help_without_args(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("No API key", proc.stdout)


if __name__ == "__main__":
    unittest.main()
