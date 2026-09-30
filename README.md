# Signal Scanner

Automated trade execution from Telegram trading signals.

Signal Scanner listens to Telegram signal groups in real time, uses an LLM to turn free-text signal
messages into structured trade data, and places risk-managed orders in MetaTrader 5. It then keeps
managing each position, trailing the stop loss as take-profit levels are hit and reporting every
event back through a Telegram bot.

## How it works

```
Telegram signal group ──► Keyword filter ──► LLM parser ──► Validation & risk checks ──► MetaTrader 5 ──► Trade manager
       (Telethon)          ("buy"/"sell")    (JSON out)      (symbol, daily loss, lots)    (split orders)    (trailing SL)
                                                                                                                  │
                                                         Telegram bot notifications ◄────────────────────────────┘
```

1. **Listen:** a Telethon client subscribes to new messages from each configured signal group.
2. **Filter:** only messages containing "buy" or "sell" reach the AI, which keeps API usage down.
3. **Parse:** an OpenAI-compatible LLM (via OpenRouter) extracts `symbol`, `side`, `entry`, `sl` and up to six
   take-profit levels as strict JSON. Several API keys can be set, and the scanner rotates through them
   when one is rate limited.
4. **Validate:** required fields are checked, symbols are normalised (for example `GOLD` becomes `XAUUSD`)
   and matched to the broker's symbol names, and the per-group symbol whitelist is enforced.
5. **Risk check:** today's realised P/L plus the live risk on open trades is compared against a maximum daily loss.
   Lot size is calculated from the group's fixed dollar risk and the free margin.
6. **Execute:** the position is split across the take-profit levels using a weighted distribution. Orders are
   placed at market or as pending limit orders that expire after a set time.
7. **Manage:** a background thread watches each position. When a take profit is hit, the stop loss trails to
   break-even and then to the previous TP. Closes are reported with their profit and the exit reason.

### Telegram bot commands

| Command         | Response                                              |
|-----------------|-------------------------------------------------------|
| `-balance`      | Account balance and open P/L                          |
| `-profit`       | Today's P/L and whether trading is still allowed      |
| `-open`         | Open positions and total open profit                  |
| `-history [n]`  | The last *n* closed trades (default 5)                |

## Setup

Requirements: Windows (the MetaTrader5 Python package is Windows only), Python 3.12+, and a MetaTrader 5 terminal
logged in to your broker.

```bash
pip install -r requirements.txt
cp config.example.py config.py   # then fill in config.py
```

1. Create a Telegram app at <https://my.telegram.org/> and copy the `api_id` and `api_hash` into `config.py`.
2. Message **@BotFather** with `/newbot` to create a notification bot and copy its token.
3. Send your bot a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat ID.
4. Add an OpenAI-compatible API key, for example from <https://openrouter.ai>, and pick a model.
5. In MT5, enable **Tools → Options → Expert Advisors → Allow algorithmic trading**, then enter your broker login.
6. Run `python main.py --list-chats` to find the chat ID of each signal group, then add each group to `SignalGroups`.
7. Run `python main.py`.

## Configuration per signal group

| Key                  | Description                                                                 |
|----------------------|-----------------------------------------------------------------------------|
| `signal_group`       | Display name used in notifications                                          |
| `signal_group_id`    | Telegram chat ID                                                            |
| `group_magic_number` | MT5 magic number base for the group's trades (use steps of 100000)          |
| `trading_symbols`    | Whitelist of symbols; an empty list allows all                              |
| `risk_amount`        | Dollar amount risked per signal                                             |
| `entry_condition`    | `market` enters immediately; `fixed` uses the signal's price as a limit order |
| `extra_tps`          | Fractions of the distance to TP1 at which to add extra TPs when the signal gives only one |

## Tech

Python · Telethon · OpenAI SDK (OpenRouter) · MetaTrader5 · requests

> **Disclaimer:** this is a personal project for educational purposes. Automated trading carries significant
> financial risk, so test on a demo account first.
