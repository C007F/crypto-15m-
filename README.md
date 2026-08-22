# Crypto 15m mind

Lives on **Chester** at `/home/cam/projects/crypto-15m-bot`. Do not clone onto the Virtus PC.

Paper-first Kalshi 15-minute crypto system. **One codebase, one trader process
per coin.** Supersedes `zec-15m-bot` and `btc-15m-bot`, whose books and journals
were migrated into `data/<asset>/`.

| Coin | Series | Spot | Reference | Vol (15m) | Kalshi volume | Spread |
|---|---|---|---|---|---|---|
| BTC | KXBTC15M | BTC-USD | ETH-USD | 23 bps | 540k | $0.01 |
| ETH | KXETH15M | ETH-USD | BTC-USD | 32 bps | 24k | $0.01 |
| XRP | KXXRP15M | XRP-USD | BTC-USD | 125 bps | 15k | $0.01 |
| DOGE | KXDOGE15M | DOGE-USD | BTC-USD | 80 bps | 6k | $0.01 |
| SOL | KXSOL15M | SOL-USD | BTC-USD | 56 bps | 7k | $0.02 (idle) |
| ZEC | KXZEC15M | ZEC-USD | BTC-USD | 114 bps | 743 | **$0.07** |

ZEC is the thinnest book Kalshi lists for 15m crypto and its spread is seven
times everyone else's. It runs for continuity with its existing history, but
any edge there is fighting a 7c round trip. SOL is configured but not started.

## Run

```bash
systemctl --user start crypto15m@xrp        # one coin
systemctl --user status 'crypto15m@*'       # all of them
journalctl --user -u crypto15m@doge -f
```

Adding a coin is one entry in `src/crypto15m/assets.py` plus
`systemctl --user enable --now crypto15m@<key>`. The dashboard picks it up with
no further change.

Dashboard: `/crypto/` on Chester, coin switcher across the top, port 8800.

**Status: paper running** under `btc-paper.service`; dashboard on `btc-dash.service`
(127.0.0.1:8794). Live orders still refuse to send.

Cloned from `zec-15m-bot` and retargeted. Differences from that build:

| | ZEC bot | this |
|---|---|---|
| Series | `KXZEC15M` | `KXBTC15M` |
| Spot feed | Coinbase `ZEC-USD` | Coinbase `BTC-USD` |
| Macro reference | BTC (`btc_ret_*`) | ETH (`eth_ret_*`), with `btc_eth_5m` as the lead/lag |
| `DEFAULT_VOL_BPS` | 120 | 30 (measured 27 bps; ZEC realizes ~4x more) |
| Dashboard port | 8792 | 8794 |
| Equity per book | $1000 | $1000 |

Separate `data/`, separate journal, separate books. Nothing is shared between
the two bots.

```bash
ssh chester
cd /home/cam/projects/crypto-15m-bot
source .venv/bin/activate
python -m crypto15m probe     # one public snapshot, no orders
python -m unittest discover -s tests -v
```
