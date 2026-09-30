from telethon import TelegramClient
from openai import OpenAI
from signalscanner import SignalScanner
import MetaTrader5 as MT5client
import sys

"""
Signal Scanner provides automatic trade placement from signals provided from a Telegram chat.

Usage:
    python main.py               Start the scanner
    python main.py --list-chats  Print the name and ID of every Telegram chat (to find signal group IDs)

See README.md for full setup instructions.
"""

try:
    import config
except ImportError:
    sys.exit("config.py not found - copy config.example.py to config.py and fill in your details.")


TGclient = TelegramClient('telegram_session', config.TGapiId, config.TGapiHash)


async def get_chat_ids():
    await TGclient.start()
    async for dialog in TGclient.iter_dialogs():
        print(f"Chat Name: {dialog.name:32} | Chat ID: {dialog.id}")


if __name__ == "__main__":
    if "--list-chats" in sys.argv:
        with TGclient:
            TGclient.loop.run_until_complete(get_chat_ids())
        sys.exit(0)

    # Build the bot URL from the token
    TGparams = dict(config.TGparams)
    TGparams["bot_url"] = f"https://api.telegram.org/bot{TGparams['bot_token']}/sendMessage"

    # Monitor the bot chat (for commands) and every signal group
    TGparams["chat_monitor"] = list(TGparams["chat_monitor"])
    TGparams["chat_monitor"].append(int(TGparams["bot_chat_id"]))
    for sg in config.SignalGroups:
        TGparams["chat_monitor"].append(sg["signal_group_id"])

    AIclients = [OpenAI(base_url=config.AIurl, api_key=key) for key in config.AIapiKey]

    SignalScanner(TGclient, AIclients, MT5client, TGparams, config.AIparams, config.MT5params, config.SignalGroups, config.debug)
