# dexscan

A local CLI that reads [DEX Screener](https://dexscreener.com) pair JSON and prints price, liquidity, 24h volume, volume/liquidity, pool age, and buy/sell counts. It uses the public API. No API key, no wallet, and no trading.

Same idea as [hl-funding-dashboard](https://github.com/kentsmallt/hl-funding-dashboard): a small tool on a free public market API. This one is a CLI because the useful input is often a pair JSON file you already saved, plus an optional live fetch.

High volume relative to liquidity, or a pool with only a few thousand dollars in it, is a reason to look closer before treating that market as deep. The `hot` and `thin` flags are that screen.

## Install

Python 3.10 or newer. No third-party packages.

```bash
git clone https://github.com/kentsmallt/dexscan.git
cd dexscan
python -m dexscan scan samples/gicat.json
```

If the `python` command is missing, use `python3` instead. Both run the same module.

Optional console script:

```bash
pip install -e .
dexscan scan samples/gicat.json
```

## Usage

Saved search or token JSON (`{"pairs": [...]}`), a bare list, or a single pair object:

```bash
python -m dexscan scan samples/gicat.json

python -m dexscan scan samples/marscoin.json \
  --chain bsc \
  --base 0xFe189E97832DA1573e4e4Ff034F4fFC3a15c7777 \
  --min-liq 1000 \
  --min-vol-liq 5 \
  --sort vol_liq

python -m dexscan scan - --json < samples/gicat.json
```

Live reads, still with no key:

```bash
python -m dexscan search "SOL/USDC" --chain solana --min-liq 100000 --limit 10
python -m dexscan token 0x118179FC98F245e59b2eCb702652C2355b723364 --chain bsc
python -m dexscan pair 0x3E86D0daC826504c4039D9D0a7005EcEDEB3E36c --chain bsc
```

| Flag | Effect |
|---|---|
| `--chain` | Keep one chain id (`bsc`, `ethereum`, `solana`, `base`, …). Required for `token` and `pair`. |
| `--base ADDRESS` | Keep pools whose base token matches. `0x` addresses are case-insensitive. |
| `--min-liq USD` | Drop pools below this liquidity. Default `0`. |
| `--min-vol-liq N` | Drop pools below this 24h volume / liquidity ratio. |
| `--sort` | `vol24` (default), `liq`, `vol_liq`, `age`, `txns`, `price`. Largest first. `age` is newest first. |
| `--asc` | Reverse that order. |
| `--limit N` | Rows to print. Default `20`. Use `all` for every match. |
| `--json` | JSON array on stdout. The summary line stays on stderr. |
| `--thin-liq USD` | Flag liquidity below this. Default `5000` (`thin`). |
| `--hot-vol-liq N` | Flag vol/liq at or above this. Default `5` (`hot`). |
| `--new-hours H` | Flag pools younger than this. Default `1` (`new`). |
| `--max-rpm N` | Live-request cap, from 1 to 300. Default `250`. |
| `--timeout SEC` | HTTP timeout. Default `20`. |

`scan` reads files or stdin (`-`). Several files are concatenated.

## Sample output

Age is measured from `pairCreatedAt` to the moment you run the command, so that column moves as a saved fixture gets older. Prices, liquidity, and volume come from the file.

```bash
python -m dexscan scan samples/marscoin.json \
  --chain bsc \
  --base 0xFe189E97832DA1573e4e4Ff034F4fFC3a15c7777 \
  --min-liq 1000 \
  --min-vol-liq 5 \
  --sort vol_liq
```

```
SYMBOL/QUOTE    CHAIN  DEX             PRICE       LIQ    VOL24   V/L  AGE     BUYS/SELLS   FLAGS  URL
--------------  -----  -----------  --------  --------  -------  ----  ------  -----------  -----
MarsCoin/USDT   bsc    pancakeswap  $0.06793  $281.24k  $20.45M  72.7  87d22h  33663/25718  hot    https://dexscreener.com/bsc/0x14ca9d552ed2cf0d5dfd1f89bc6793a464e2ed16
MarsCoin/USDT   bsc    pancakeswap  $0.06804  $592.59k   $8.34M  14.1  89d15h  6800/4350    hot    https://dexscreener.com/bsc/0x4a4bea953813c118c260be9a26b2321e57aa62e5
MarsCoin/USDT   bsc    uniswap      $0.06803  $112.97k   $1.16M  10.3  87d19h  2976/1982    hot    https://dexscreener.com/bsc/0xeee6c8b83be11e53388227f161ba9c0962e785df
MarsCoin/牛来   bsc    pancakeswap  $0.06764    $6.37k  $56.53k  8.87  66d13h  877/769      hot    https://dexscreener.com/bsc/0x9e9b5d76a6a324f1df69d879b8602e4824d90c06
MarsCoin/SPCXB  bsc    pancakeswap  $0.06837    $1.27M   $9.44M  7.46  90d16h  8364/5249    hot    https://dexscreener.com/bsc/0x94f3ed36706c746ad59fadcaf271b7431ab1d8f1
5 shown of 5 matched (21 loaded). matched liquidity $2.26M, 24h volume $39.44M, blended vol/liq 17.46.
```

`V/L` is 24h volume divided by USD liquidity. The footer ratio is the sum of matched volume divided by the sum of matched liquidity.

`samples/gicat.json` is a one-pool search response. `samples/marscoin.json` is a 21-pool token response. Profile images, boosts, and social links are omitted; the market fields are the public payload.

## API

No key. Docs: [DEX Screener API reference](https://docs.dexscreener.com/api/reference).

| Command | Endpoint | Documented limit |
|---|---|---|
| `search` | `GET /latest/dex/search?q=` | 300 requests / minute |
| `pair` | `GET /latest/dex/pairs/{chainId}/{pairId}` | 300 requests / minute |
| `token` | `GET /token-pairs/v1/{chainId}/{tokenAddress}` | 300 requests / minute |

The client stays at 250 requests per minute unless you set `--max-rpm` (never above 300). A single HTTP 429 is retried after `Retry-After`, capped at 30 seconds. The User-Agent is `dexscan/0.1`. Redirects that leave `https://api.dexscreener.com` are refused.

Pair responses carry `cache-control: max-age=30`. Polling faster than that returns the same cached body. This CLI does not call the 60/minute profile, boost, ad, or order endpoints.

`python -m dexscan` with no arguments prints help and exits 0. A bad file, rejected query, or HTTP error exits 1.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests are offline. They load `samples/` and stub the HTTP client. Nothing in the suite calls the network.

## Layout

```
dexscan/     parser, urllib client, table, CLI
samples/     two small public fixtures
tests/       unittest
```

## Disclaimer

For information only. Not investment advice. A high volume/liquidity ratio can be real interest, a new pool, or wash trading. Liquidity and volume are DEX Screener's figures, not an execution quote, and they can be wrong or stale. This program does not place orders.

## License

[MIT](LICENSE) © 2026 Kent
