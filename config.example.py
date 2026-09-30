"""
Signal Scanner configuration.

Copy this file to config.py and fill in your own values.
config.py is git-ignored so your credentials are never committed.
"""

#region Telegram

TGapiId = "YOUR_TELEGRAM_API_ID"  # Telegram App api_id (https://my.telegram.org/)
TGapiHash = "YOUR_TELEGRAM_API_HASH"  # Telegram App api_hash

TGparams = {
    "chat_monitor": [],  # Telegram chat IDs to monitor (bot chat ID is added automatically in main.py)
    "bot_token": "YOUR_BOT_TOKEN",  # Telegram bot token from @BotFather
    "bot_chat_id": "YOUR_BOT_CHAT_ID",  # Chat ID used to send notifications and receive commands
}

#endregion


#region AI

AIurl = "https://openrouter.ai/api/v1"  # Any OpenAI-compatible endpoint
AIapiKey = ["YOUR_AI_API_KEY"]  # One or more API keys - multiple keys are tried in order to handle rate limits

AIparams = {
    "model": "google/gemma-3n-e4b-it:free"
}

#endregion


#region MetaTrader5

MT5params = {
    "path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",  # Path to MetaTrader5's terminal64.exe
    "login": 0,  # Broker's MT5 login
    "password": "YOUR_MT5_PASSWORD",  # Broker's MT5 password
    "server": "YOUR_BROKER_SERVER",  # Broker's MT5 server
    "max_daily_loss": -1000,  # Max permitted daily loss (0 if disabled, else a negative value) - no trades will be placed if the daily loss will be exceeded
    "trade_expiration": 20  # Minutes that a "fixed" entry price trade will remain pending/unfilled before cancelling (0 if disabled, minimum 5 mins)
}

#endregion


#region Signal Groups

SG_Example = {
    "signal_group": "Example Signals",  # The name of the signal group being monitored
    "signal_group_id": -1001234567890,  # The signal group's chat ID (run `python main.py --list-chats` to find it)
    "group_magic_number": 100000,  # Magic number assigned to the signal group (increment by 100000 per signal group)
    "trading_symbols": ["XAUUSD"],  # Symbols to trade (signals for other symbols are ignored; an empty list allows all symbols)
    "risk_amount": 200.0,  # Amount to risk per trade ($)
    "entry_condition": "market",  # Use the signal's "fixed" entry price, or the current "market" price
    "extra_tps": [(1/3), 0.5, (2/3)]  # Extra take profit points (only used if the signal gives 1 TP); empty list if not required
}

SignalGroups = [SG_Example]

#endregion

debug = False  # Output debug messages to Telegram
