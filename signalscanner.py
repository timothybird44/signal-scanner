from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta, timezone, time
from telethon import events
import threading
import requests
import time as TimeModule
import math
import json
import pytz
import re

#region General
"""
Signal Scanner provides automatic trade placement from signals provided from a Telegram chat.
"""
class SignalScanner:
    """
    method  __init__
    desc    Initialise required parameters, MetaTrader5, and Telegram.
    input   [TelegramClient]TGclient: The configured Telegram client
            [array[OpenAI]]AIclients: An array of the configured AI clients
            [MetaTrader5]MT5client: The MetaTrader5 class
            [dict]TGparams: The required parameters for Telegram usage
            [dict]AIparams: The required parameters for AI usage
            [dict]MT5params: The required parameters for MetaTrader5 usage
            [array[dict]]SignalGroups: The required parameters for each signal group to be monitored
            [bool]debug: Determines if debug messages will be output
    output  None
    """
    def __init__(self, TGclient, AIclients, MT5client, TGparams, AIparams, MT5params, SignalGroups, debug):
        self.debug = debug

        # Telegram parameters
        self.TGclient = TGclient
        self.TGsession = None
        self.TGchats = TGparams['chat_monitor']
        self.TGbotURL = TGparams['bot_url']
        self.TGbotToken = TGparams['bot_token']
        self.TGbotChatId = TGparams['bot_chat_id']

        # AI parameters
        self.AIclients = AIclients
        self.AImodel = AIparams['model']

        # MetaTrader5 parameters
        self.MT5Client = MT5client
        self.MT5path = MT5params['path']
        self.MT5login = MT5params['login']
        self.MT5password = MT5params['password']
        self.MT5server = MT5params['server']
        self.MT5maxDailyLoss = MT5params['max_daily_loss']
        self.MT5expiration = MT5params['trade_expiration']
        self.MT5liveRisk = 0.0

        # Signal Group parameters
        self.SignalGroups = SignalGroups

        # Initialise elements
        self.initialise_signal_groups()
        self.initialise_metatrader5()
        self.initialise_telegram()

    """
    method  initialise_signal_groups
    desc    Initialises the signal groups - getting the original magic number and setting the current magic number
    input   None
    output  None
    """
    def initialise_signal_groups(self):
        for sg in self.SignalGroups:
            sg['current_magic_number'] = sg['group_magic_number']
            
        return None

#endregion

#region Telegram

    """
    method  initialise_telegram
    desc    Initialises the Telegram event handler to listen for new messages in the provided signal groups
    input   None
    output  None
    """
    def initialise_telegram(self):
        self.setup_telegram_session()
        self.send_notification("<b>SignalScanner Started!</b>")
        
        # Initialise the chat listener
        self.TGclient.start()
        self.TGclient.add_event_handler(self.on_new_message, events.NewMessage(chats=self.TGchats))
        self.TGclient.run_until_disconnected()

    """
    method  setup_telegram_session
    desc    Sets up the telegram session with the bot, ready to send messages
    input   None
    output  None
    """
    def setup_telegram_session(self):
        # Initialise the Telegram chat session
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trading-bot/1.0",
            "Accept": "application/json"
        })
        
        retry_strategy = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )

        session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
        self.TGsession = session

        thread = threading.Thread(target=self.telegram_heartbeat, daemon=True)
        thread.start()

    """
    method  telegram_heartbeat
    desc    Sends a harmless heartbeat message to telegram to keep the connection alive
    input   None
    output  None
    """
    def telegram_heartbeat(self):
        while True:
            try:
                # GetMe is the lightest possible request to keep the socket open
                self.TGsession.get(f"https://api.telegram.org/bot{self.TGbotToken}/getMe", timeout=5)
            except:
                # If heartbeat fails, it's okay, we'll fix it in the next notification send
                pass
            TimeModule.sleep(120) # Send every 2 minutes

    """
    method  on_new_message
    desc    Reads a new message from the Telegram group, passes the information to AI, places an automated trade.
    input   [events]event: The new message event from Telegram
    output  None
    """
    async def on_new_message(self, event):
        try:
            TGmessage = event.raw_text
            TGmessageLower = event.raw_text.lower()
            TGchatId = event.chat_id
            AIsignal = None
            signalGroup = None

            if str(TGchatId) == str(self.TGbotChatId):
                self.execute_command(TGmessageLower)
                return None

            # Get the signal group details for the chat's ID
            for sg in self.SignalGroups:
                if sg["signal_group_id"] == TGchatId:
                    signalGroup = sg

            # Validate a signal group was found with the chat ID
            if not signalGroup:
                self.send_notification(f"<b>Error on_new_message:</b> Could not determine the signal group the message originated from")
                return None
            
            # Attempt to filter messages to only pass signals to limit API usage
            if ("buy" in TGmessageLower) or ("sell" in TGmessageLower):  
                AIsignal = self.analyze_with_ai(TGmessage)
            else:
                self.send_notification(f"<b>Debug on_new_message:</b> Not a buy or sell signal", True)
            
            if AIsignal:
                # Validate the required fields have been returned
                self.send_notification(f"<b>Debug on_new_message:</b> AI signal - {AIsignal}", True)
                requiredFields = ["symbol", "side", "entry", "sl", "tp1"]
                
                for item in requiredFields:
                    if AIsignal.get(item) == None:
                        self.send_notification(f"<b>Debug on_new_message:</b> Not all required fields have been returned by AI for the message from {signalGroup["signal_group"]}", True)
                        return None
                AIsignal["symbol"] = AIsignal["symbol"].replace("/", "")

                if AIsignal["symbol"].lower() == "gold":
                    AIsignal["symbol"] = "XAUUSD"

                # Ensure the symbol is allowed to be traded by the signal group
                if (AIsignal["symbol"] in signalGroup["trading_symbols"]) or (signalGroup["trading_symbols"] == []):
                    # Check the MT5 symbol name
                    AIsignal["symbol"] = self.get_mt5_symbol(AIsignal["symbol"])
                    # Ensure the max daily loss is not exceeded
                    allowTrade, _ = self.check_daily_profit(AIsignal["symbol"])

                    if allowTrade:
                        self.place_trade(signalGroup, AIsignal)
                    else:
                        self.send_notification(f"<b>Debug on_new_message:</b> Max daily loss exceeded", True)
                else:
                    self.send_notification(f"<b>Debug on_new_message:</b> Trading symbol {AIsignal["symbol"]} not allowed for {signalGroup["signal_group"]}", True)
        
        except Exception as e:
            self.send_notification(f"<b>Error on_new_message:</b> {e}")

    """
    method  send_notification
    desc    Sends a notification message via the Telegram bot
    input   [string]message: The message to send on Telegram
    output  None
    """
    def send_notification(self, message, debug=False):
        try:
            # Filter out blank messages
            if (message is None) or (message == ""):
                return None
            
            payload = {
                "chat_id": self.TGbotChatId,
                "text": message,
                "parse_mode": "HTML"
            }
        
            if (debug == False) or ((debug == True) and (self.debug == True)):
                response = self.TGsession.post(self.TGbotURL, data=payload, timeout=15)
                response.raise_for_status()

        except (requests.exceptions.ConnectionError, ConnectionResetError) as e:
            TimeModule.sleep(2)

            try:
                response = requests.post(self.TGbotURL, data=payload, timeout=15)
                response.raise_for_status()

            except Exception as retryE:
                print(f"Error send_notification: Failed to retry &gt {e} \n{message}")
        
        except Exception as e:
            print(f"Error send_notification: {e} \n{message}")

            """
    method  execute_command
    desc    Executes a command given to the chatbot and returns response in the Telegram chat
    input   [string]message: The chat message received from Telegram
    output  None
    """
    def execute_command(self, message):
        try:
            response = ""

            # Get daily profit command
            if message == "-profit":
                allowTrade, dailyProfit = self.check_daily_profit()
                    
                if allowTrade:
                    response = "Trades are <b>allowed</b>, "
                else:
                    response = "Trades are <b>not allowed</b>, "
                    
                response += f"as the daily profit is: <b>${dailyProfit}</b>."

            # Get balance command
            if message == "-balance":
                balance, openProfit = self.get_balance()
                if (balance is not None) and (openProfit is not None):
                    response = f"The current account balance is <b>${balance}</b> with current open profit of <b>${openProfit}</b>."

            # Get history commant
            if message.startswith("-history"):
                messageParts = message.split(" ")
                amount = 5
                
                if (len(messageParts) == 2) and (messageParts[0] == "-history"):
                    if messageParts[1].isdigit():
                        amount = int(messageParts[1])

                previousDeals = self.get_trade_history(amount)

                if previousDeals is None:
                    response = "Unable to retreive trade history."

                else:
                    response = f"Here are the last {amount} trades:"
                    dealCount = 1

                    for deal in previousDeals:
                        side = "Buy"

                        if deal.type == 0:
                            side = "Sell"

                        response += f"\n{dealCount}. {side} {deal.volume} {deal.symbol} &gt <b>${deal.profit}</b>"
                        dealCount +=  1

            # Get open positions command
            if message == "-open":
                openPositions = self.get_open_trades()
                
                if openPositions is None:
                    response = "Unable to retreive open positions."
                
                else:
                    if len(openPositions) == 0:
                        response = "There are currently 0 open positions."
                    
                    else:
                        response = f"Here are the {len(openPositions)} open positions:"
                        openCount = 1
                        totalOpenPofit = 0

                        for pos in openPositions:
                            side = "Buy"

                            if pos.type == 1:
                                side = "Sell"

                            response += f"\n{openCount}. {side} {pos.volume} {pos.symbol} &gt <b>${pos.profit}</b>"
                            totalOpenPofit += pos.profit
                            openCount +=  1
                        
                        response += f"\n<b>Total Open Profit:</b> ${totalOpenPofit}"

            # Send the command response
            self.send_notification(response)
        
        except Exception as e:
            self.send_notification(f"<b>Error execute_command:</b> {e}")

        return None
    
#endregion

#region AI

    """
    method  analyze_with_ai
    desc    Sends text to AI to retreive signal details
    input   [string]text: The text for AI to interperate
    output  [dict]AIjsonSignal: The json response from AI containing the signal details
    """
    def analyze_with_ai(self, text):
        for AIclient in self.AIclients:
            try:
                systemContent = "You are a trading signal parser. Return ONLY a valid JSON object. If a value is missing, use null. If a range is given, use the highest point. Use this exact structure: {\"symbol\": \"string\", \"side\": \"BUY/SELL\", \"entry\": number, \"sl\": number, \"tp1\": number, \"tp2\": number, \"tp3\": number, \"tp4\": number, \"tp5\": number, \"tp6\": number}. If no signal, return {\"status\": \"none\"}."
                AIresponse = AIclient.chat.completions.create(
                    model=self.AImodel,
                    messages=[
                        # {"role": "system", "content": systemContent},
                        {"role": "user", "content": systemContent + "\n" + text}
                    ]
                )

                if AIresponse and hasattr(AIresponse, 'choices') and AIresponse.choices and len(AIresponse.choices) > 0:
                    AIcontent = AIresponse.choices[0].message.content
                    AIcleanContent = AIcontent.replace("```json", "").replace("```", "").strip()
                    AIsignal = re.search(r'\{.*\}', AIcleanContent, re.DOTALL)

                    if AIsignal:
                        try:
                            jsonStr = AIsignal.group()
                            AIjsonSignal = json.loads(jsonStr)

                            self.send_notification(f"<b>Info analyze_with_ai:</b> returned AI signal &gt {AIjsonSignal}")
                            
                            # Check if it's the "None" signal we defined in the prompt
                            if AIjsonSignal.get("status") == "none":
                                return None
                                
                            return AIjsonSignal
                        
                        except json.JSONDecodeError as e:
                            self.send_notification(f"<b>AI JSON Error:</b> {e}\n<b>Raw Content:</b> <code>{AIcleanContent[:100]}</code>")
                            return None
                    
                    if AIclient == self.AIclients[-1]:
                        self.send_notification(f"<b>Error analyze_with_ai:</b> unable to return a signal from AI response &gt {AIresponse}")

                else:
                    if AIclient == self.AIclients[-1]:
                        self.send_notification(f"<b>Error analyze_with_ai:</b> unable to receive an AI response")

            except Exception as e:
                if AIclient == self.AIclients[-1]:
                    self.send_notification(f"<b>Error analyze_with_ai:</b> {e}")
    
        return None
    
#endregion

#region MetaTrader5
    """
    method  initialise_metatrader5
    desc    Initialises MetaTrader5 to launch and await trades
    input   None
    output  None
    """
    def initialise_metatrader5(self):
        try:
            # 1. Attempt to initialize
            # Tip: Try calling initialize() without the path first if the terminal is already open
            success = self.MT5Client.initialize(
                path=self.MT5path, 
                server=self.MT5server, 
                login=self.MT5login, 
                password=self.MT5password,
                timeout=10000 # Increase timeout to 10 seconds to avoid -10005
            )

            if not success:
                # Note: No 'e' here because no exception occurred yet
                err_code, err_desc = self.MT5Client.last_error()
                self.send_notification(
                    f"<b>Init Failed:</b> {err_desc} (Code: {err_code})\n"
                    f"Check if MT5 is open and 'Algo Trading' is enabled."
                )
                return False
                
            return True
            
        except Exception as e:
            # 'e' is valid here because we are inside the except block
            self.send_notification(f"<b>System Error:</b> {e}")
            return False
        
    """
    method  get_mt5_symbol
    desc    Finds the symbol name in the MT5 broker for the base symbol provided by the signal
    input   [string]symbol: The symbol being traded
    output  [string]symbol: The symbol being traded
    """
    def get_mt5_symbol(self, symbol):
        # Get all symbols from MT5
        all_symbols = self.MT5Client.symbols_get()
        
        if all_symbols is None:
            return symbol # Fallback to original if MT5 is down

        # Iterate and look for a match
        for s in all_symbols:
            if s.name.startswith(symbol):
                self.MT5Client.symbol_select(s.name, True)
                symbol = s.name
                return symbol
                
        # If no match found, return the original
        return symbol

    """
    method  calculate_lot_size
    desc    Calculates the maximum lot size for a trade, given the defined risk amount and trade details
    input   [dict]signalGroup: The required parameters for the signal group
            [string]symbol: The symbol being traded
            [float]entryPrice: The entry price of the trade
            [float]stopLossPrice: The price stop loss is set to for the trade
    output  [float]finalLot: The maximum lot size for the given trade
            [float]tradeRisk: The amount of $ being risked on the trade hitting SL (will return max allowed risk if error)
    """
    def calculate_lot_size(self, signalGroup, symbol, entryPrice, stopLossPrice):
        try:
            # Confirm the symbol is selected
            if not self.MT5Client.symbol_select(symbol, True):
                return None, None
            
            # Get symbol and account info
            symbolInfo = self.MT5Client.symbol_info(symbol)
            accountInfo = self.MT5Client.account_info()
            
            # Validate symbol and account info has been returned
            if not symbolInfo or not accountInfo:
                return None, None

            # Determine the order type and simulated profit for the trade
            orderType = self.MT5Client.ORDER_TYPE_BUY if entryPrice > stopLossPrice else self.MT5Client.ORDER_TYPE_SELL
            simProfit = self.MT5Client.order_calc_profit(orderType, symbol, 1.0, entryPrice, stopLossPrice)
            
            # Validate simulated profit was calculated, else return the minimum lot for the symbol
            if not simProfit: 
                return symbolInfo.volume_min, signalGroup['risk_amount'] # Assume the full risk amount to hedge
            
            # Calculate the lot risk and margin for a single lot
            rawLotRisk = signalGroup["risk_amount"] / abs(simProfit)
            marginForOneLot = self.MT5Client.order_calc_margin(orderType, symbol, 1.0, entryPrice)
            
            # Validate margin for a single lot was calculated, else return the minimum lot for the symbol
            if not marginForOneLot or marginForOneLot == 0:
                return symbolInfo.volume_min, signalGroup['risk_amount'] # Assume the full risk amount to hedge

            # Calculate the final lot size based on the allowed risk and available capital
            availableCapital = accountInfo.margin_free * 0.9
            rawLotMargin = availableCapital / marginForOneLot
            rawLot = min(rawLotRisk, rawLotMargin)
            step = symbolInfo.volume_step
            finalLot = math.floor(rawLot / step) * step
            finalLot = round(float(max(symbolInfo.volume_min, min(finalLot, symbolInfo.volume_max))), 2)
            tradeRisk = self.MT5Client.order_calc_profit(orderType, symbol, finalLot, entryPrice, stopLossPrice)
            
            return finalLot, tradeRisk
        
        except Exception as e:
            self.send_notification(f"<b>Error get_open_price:</b> {e}")
            return None, None
    
    """
    method  get_open_price
    desc    Gets the price of a symbol at NY open (9:30AM ET)
    input   [string]symbol: The symbol the get the open price for
            [int]digits: The number of digits the broker uses to display price for the symbol
    output  [float]price: The opening price for the given symbol
    """
    def get_open_price(self, symbol, digits):
        try:
            nyTz = pytz.timezone("America/New_York")
            utcTz = pytz.utc

            now = datetime.now(nyTz)
            nyOpenLocal = nyTz.localize(datetime(now.year, now.month, now.day, 9, 30))
            nyOpenUtc = nyOpenLocal.astimezone(utcTz)

            rates = self.MT5Client.copy_rates_from(symbol, self.MT5Client.TIMEFRAME_M1, nyOpenUtc, 1)
            price = None

            if (rates is not None) and (len(rates) > 0):
                price = round(float(rates[0]['open']), digits)
            else:
                self.send_notification(f"<b>Error get_open_price:</b> Unable to determine open price for {symbol}.")
                
            return price
        
        except Exception as e:
            self.send_notification(f"<b>Error get_open_price:</b> {e}")
            return None
        
    """
    method  modify_tps
    desc    Adds TPs before the SGs TP based on "extra_tps" param. Only valid if 1 TP is provided by the SG 
    input   [dict]signalGroup: The required parameters for the signal group
            [dict]signalData: The data for the trade to place
    output  [dict]newSignalData: The updated data for the trade to place
    """
    def modify_tps(self, signalGroup, signalData):
        try:
            newSignalData = signalData

            # If no extra TPs provided, exit
            if signalGroup['extra_tps'] == []:
                self.send_notification(f"<b>Debug modify_tps:</b> No extra TPs required for {signalGroup['signal_group']}", True)
                return newSignalData
            
            symbol = signalData['symbol']
            self.MT5Client.symbol_select(symbol, True)
            symbolInfo = self.MT5Client.symbol_info(symbol)

            # Verify the symbol info was returned
            if symbolInfo is None:
                self.send_notification(f"<b>Error modify_tps:</b> Symbol info could not be found for {symbol}")
                return None
            
            # Get the TPs given by the SG
            tpKeys = ['tp1', 'tp2', 'tp3', 'tp4', 'tp5', 'tp6']
            activeTps = []

            for k in tpKeys:
                if signalData.get(k) is not None:
                    if signalData[k] == "open":
                        activeTps.append(self.get_open_price(symbol, symbolInfo.digits))
                    else:
                        activeTps.append(round(float(signalData[k]), symbolInfo.digits))

            # Only add extra TPs if 1 TP is provided by the SG
            if len(activeTps) == 1:
                givenTp = activeTps[0]

                for i in range(len(signalGroup['extra_tps'])):
                    # Calculate new TP as entry + ((TP1 - entry) * extra TP)
                    calcTp = newSignalData['entry'] + ((givenTp - newSignalData['entry']) * signalGroup['extra_tps'][i])
                    newSignalData[tpKeys[i]] = round(calcTp, 2)

                newSignalData[tpKeys[len(signalGroup['extra_tps'])]] = givenTp
                self.send_notification(f"<b>Debug modify_tps:</b> Updated signal data &gt {newSignalData}", True)

            return newSignalData          
        
        except Exception as e:
            self.send_notification(f"<b>Error modify_tps:</b> {e}")
            return None

    """
    method  place_trade
    desc    Places a trade in MT5 using the signal data from a signal group
    input   [dict]signalGroup: The required parameters for the signal group
            [dict]signalData: The data for the trade to place
    output  None
    """
    def place_trade(self, signalGroup, signalData):
        try:
            self.send_notification(f"<b>Debug place_trade:</b> Begining trade placement", True)

            # Modify TPs as required by SG params
            signalData = self.modify_tps(signalGroup, signalData)

            # Get the signal's trade side, symbol, and symbol info
            side = signalData['side']
            symbol = signalData['symbol']
            self.MT5Client.symbol_select(symbol, True)
            symbolInfo = self.MT5Client.symbol_info(symbol)
            digits = symbolInfo.digits

            # Verify the symbol info was returned
            if symbolInfo is None:
                self.send_notification(f"<b>Error place_trade:</b> Symbol info could not be found for {symbol}")
                return None
            
            # Get the symbol's tick
            tick = None
            for _ in range(5):
                tick = self.MT5Client.symbol_info_tick(symbol)

                # Check if we have a valid price (not None and price > 0)
                if tick is not None and tick.ask > 0 and tick.bid > 0:
                    break

                TimeModule.sleep(0.2)

            # Verify the tick was returned and is valid
            if tick is None or tick.ask <= 0:
                # If still 0, try the absolute last-resort: getting price from the latest Bar
                rates = self.MT5Client.copy_rates_from_pos(symbol, self.MT5Client.TIMEFRAME_M1, 0, 1)
                if rates is not None and len(rates) > 0:
                    currentAsk = rates[0]['close'] # Use the last closed minute price
                    currentBid = rates[0]['close']
                else:
                    self.send_notification(f"<b>Error place_trade:</b> Price feed for {symbol} is inactive.")
                    return None
            else:
                currentAsk = tick.ask
                currentBid = tick.bid
            
            # Define the TPs that were provided in the signal
            tpKeys = ['tp1', 'tp2', 'tp3', 'tp4', 'tp5', 'tp6']
            activeTps = []

            for k in tpKeys:
                if signalData.get(k) is not None:
                    if signalData[k] == "open":
                        activeTps.append(self.get_open_price(symbol, digits))
                    else:
                        activeTps.append(round(float(signalData[k]), digits))
            
            # Verify that at least 1 TP exists
            if not activeTps:
                self.send_notification("<b>Error place_trade:</b> no TP values provided")
                return None
            
            # Get the signals entry and stop loss, and calculate the total lot for the position
            entry = round(float(signalData['entry']), digits)
            sl = round(float(signalData['sl']), digits)

            # Determine order parameters based on entry condition and trade side
            if signalGroup["entry_condition"] == "market":
                tradeAction = self.MT5Client.TRADE_ACTION_DEAL
                
                if side == 'BUY':
                    entry = currentAsk
                    orderType = self.MT5Client.ORDER_TYPE_BUY
                else:
                    entry = currentBid
                    orderType = self.MT5Client.ORDER_TYPE_SELL
            else:
                tradeAction = self.MT5Client.TRADE_ACTION_PENDING

                if side == 'BUY':
                    if entry < currentAsk:
                        orderType = self.MT5Client.ORDER_TYPE_BUY_LIMIT  
                    else:
                        entry = currentAsk
                        orderType = self.MT5Client.ORDER_TYPE_BUY
                else:
                    if entry > currentBid:
                        orderType = self.MT5Client.ORDER_TYPE_SELL_LIMIT  
                    else:
                        entry = currentBid
                        orderType = self.MT5Client.ORDER_TYPE_SELL

            totalLot, tradeRisk = self.calculate_lot_size(signalGroup, symbol, entry, sl)

            if totalLot == None:
                self.send_notification("<b>Error place_trade:</b> could not determine total lot size")
                return None

            # Determine if the the risk can be afforded
            _, dailyProfit = self.check_daily_profit(symbol)
            currentRisk = self.MT5liveRisk + dailyProfit + tradeRisk

            if currentRisk < self.MT5maxDailyLoss:
                self.send_notification("<b>Info place_trade:</b> trade cannot be placed as it would exceed the allowed daily loss")
                return None
            
            # Define the weight distribution of lots for each TP based on how many were defined
            weightMap = {
                1: [1.0],
                2: [0.6, 0.4],
                3: [0.5, 0.3, 0.2],
                4: [0.4, 0.3, 0.2, 0.1],
                5: [0.3, 0.25, 0.2, 0.15, 0.1],
                6: [0.25, 0.3, 0.25, 0.2, 0.15, 0.05, 0.05]
            }
            weights = weightMap[len(activeTps)]

            # Calculate the individual lot sizes for each TP
            step = symbolInfo.volume_step
            minLot = symbolInfo.volume_min
            splitLots = []
            lotsUsed = 0.00
            
            for w in weights:
                l = math.floor((totalLot * w) / step) * step
                tpLot = max(l, minLot)
                if (lotsUsed + tpLot) <= totalLot:
                    splitLots.append(tpLot)
                    lotsUsed += tpLot

            # Ensure the sum of split lots is equal to the total lots. Add any difference to TP1
            diff = round(totalLot - sum(splitLots), 2)
            if diff > 0:
                splitLots[0] = round(splitLots[0] + diff, 2)

            tradeTaken = False
            tradeDetails = ""
            completedOrders = []
            # Execute the trades for each TP
            for i in range(len(splitLots)):
                request = {
                    "action": tradeAction,
                    "symbol": symbol,
                    "volume": splitLots[i],
                    "type": orderType,
                    "price": entry,
                    "sl": sl,
                    "tp": activeTps[i],
                    "deviation": 2,
                    "magic": signalGroup["current_magic_number"],
                    "comment": f"SS TP{i+1}",
                    "type_time": self.MT5Client.ORDER_TIME_GTC,
                    "type_filling": self.MT5Client.ORDER_FILLING_IOC,
                }

                # Terminate the "fixed" entry trade if the position is unfilled after expiration
                if (signalGroup["entry_condition"] == "fixed") and (self.MT5expiration != 0):
                    # Ensure a minimum of 5 minute expiration is set to prevent errors
                    if self.MT5expiration < 5:
                        self.MT5expiration = 5

                    brokerTime = tick.time
                    expirationTime = brokerTime + (self.MT5expiration * 60) + 20
                    request["type_time"] = self.MT5Client.ORDER_TIME_SPECIFIED
                    request["expiration"] = expirationTime

                # Check that the order is valid and the account has sufficient funds
                checkOrderResult = self.MT5Client.order_check(request)

                if (checkOrderResult is None) or (checkOrderResult.retcode != 0):
                    errorCode = checkOrderResult.retcode if checkOrderResult else "None"
                    errorMsg = checkOrderResult.comment if checkOrderResult else "Unknown Check Error"
                    self.send_notification(f"<b>Debug place_trade:</b> Unable to place order: {errorCode} {errorMsg}", True)
                    break

                orderResult = self.MT5Client.order_send(request)
        
                if orderResult:
                    if orderResult.retcode != self.MT5Client.TRADE_RETCODE_DONE:
                        self.send_notification(f"<b>{symbol} TP{i+1} Order Failed:</b> {orderResult.retcode} {orderResult.comment} \n<b>Last MT5 Error:</b> {self.MT5Client.last_error()}")
                    else:
                        tradeDetails += f"\n<b>Take Profit {i+1}:</b> {activeTps[i]} ({splitLots[i]} lots)"
                        tradeTaken = True
                        # Get the order number for the taken trades
                        completedOrders.append(orderResult.order)
                else:
                    self.send_notification(f"<b>Error place_trade:</b> Unable to place order. \n<b>Last MT5 Error:</b> {self.MT5Client.last_error()}")

            if tradeTaken:
                self.MT5liveRisk += tradeRisk
                tradeMagicNumber = signalGroup['current_magic_number']
                self.send_notification(f"<b>Trade Placed!</b> \n<b>{side} {symbol}</b> \n<b>Signal Group:</b> {signalGroup["signal_group"]} \n<b>Entry:</b> {entry} \n<b>Stop Loss:</b> {sl} {tradeDetails} \n<b>Trade Risk</b>: {tradeRisk}")
                self.start_position_monitor(completedOrders, signalGroup, symbol, entry, activeTps, side, tradeRisk, tradeMagicNumber)

                # Add 1 to the current magic number for each new trade, incase of multiple open trades by the same group
                for sg in self.SignalGroups:
                    if sg['signal_group_id'] == signalGroup['signal_group_id']:
                        sg['current_magic_number'] = ((signalGroup['current_magic_number'] + 1) % 100000) + signalGroup['group_magic_number']
            else:
                self.send_notification(f"<b>Debug place_trade:</b> No trades have been placed", True)
            
        except Exception as e:
            self.send_notification(f"<b>Error place_trade:</b> {e} \n<b>Last MT5 Error:</b> {self.MT5Client.last_error()}")
            return None
    
    """
    method  start_position_monitor
    desc    Initialises a thread that will monitor the trade using manage_trade
    input   [array[int]]completedOrders: The order number for each of the taken trades
            [dict]signalGroup: The required parameters for the signal group
            [string]symbol: The symbol being traded
            [float]entry: The entry price of the trade
            [array[float]]activeTps: The active take profit prices for the trade
            [string]side: The side of the trade - "BUY" or "SELL"
            [float]tradeRisk: The amount of $ being risked in the trade
            [int]tradeMagicNumber: The magic number for the given trade
    output  None
    """
    def start_position_monitor(self, completedOrders, signalGroup, symbol, entry, activeTps, side, tradeRisk, tradeMagicNumber):
        thread = threading.Thread(target=self.manage_trade, args=(completedOrders, signalGroup, symbol, entry, activeTps, side, tradeRisk, tradeMagicNumber))
        thread.daemon = True # Thread dies when main program exits
        thread.start()
    
    """
    method  manage_trade
    desc    Manages the stop loss of the open trades, using a trailing stop loss method.
    input   [array[int]]completedOrders: The order number for each of the taken trades
            [dict]signalGroup: The required parameters for the signal group
            [string]symbol: The symbol being traded
            [float]entry: The entry price of the trade
            [array[float]]activeTps: The active take profit prices for the trade
            [string]side: The side of the trade - "BUY" or "SELL"
            [float]tradeRisk: The amount of $ being risked in the trade
            [int]tradeMagicNumber: The magic number for the given trade
    output  None
    """
    def manage_trade(self, completedOrders, signalGroup, symbol, entry, activeTps, side, tradeRisk, tradeMagicNumber):
        try:
            executionStatus = self.wait_for_execution(completedOrders)
            if executionStatus != 0:
                if executionStatus == 1:
                    self.send_notification(f"<b>Orders Filled!</b> \n<b>{side} {symbol}</b> \n<b>Signal Group:</b> {signalGroup["signal_group"]} \n<b>Entry Price:</b> {entry}")
                else:
                    self.send_notification(f"<b>Partial Orders Filled!</b> \n<b>{side} {symbol}</b> \n<b>Signal Group:</b> {signalGroup["signal_group"]} \n<b>Entry Price:</b> {entry}")
               
                lastTarget = -1
                newSLString = ""
                trackedTickets = set()

                while True:
                    # Get all open positions for this bot
                    positions = self.MT5Client.positions_get(symbol=symbol)
                    filteredPositions = [p for p in positions if p.magic == tradeMagicNumber]

                    # Create a set of current tickets to identify closures
                    currentTickets = {pos.ticket for pos in filteredPositions} if filteredPositions else set()

                    # Initialize trackedTickets on the first run
                    if not trackedTickets and currentTickets:
                        trackedTickets = currentTickets

                    # 2. Check for closed positions
                    closed_tickets = trackedTickets - currentTickets
                    for ticket in closed_tickets:
                        self.handle_closed_position(ticket, symbol, side, signalGroup)

                    trackedTickets = currentTickets
                    
                    # If there are no active positions, there is nothing to manage
                    if not filteredPositions:
                        self.MT5liveRisk -= tradeRisk
                        break

                    # Get current tick to see where the market is
                    tick = self.MT5Client.symbol_info_tick(symbol)
                    if not tick:
                        continue

                    # Get the current price of the symbol
                    isBuy = filteredPositions[0].type == self.MT5Client.POSITION_TYPE_BUY
                    currentPrice = tick.bid if isBuy else tick.ask

                    # Define the new SL if the TP levels are hit
                    newSL = None

                    # Check if the TP levels have been hit
                    for i, tpPrice in enumerate(activeTps):
                        targetCrossed = (isBuy and currentPrice >= tpPrice) or (not isBuy and currentPrice <= tpPrice)
                        
                        if targetCrossed and i > lastTarget:
                            lastTarget = i

                            # Move SL to the previous TP level
                            if lastTarget == 0:
                                newSL = entry
                                newSLString = "entry"
                            else:
                                newSL = activeTps[i-1]
                                newSLString = f"TP{lastTarget}"

                    # Apply the SL modification if a new SL is triggered
                    if newSL is not None:
                        self.send_notification(f"<b>TP{lastTarget+1} Hit!</b> \n<b>{side} {symbol}</b> \n<b>Signal Group:</b> {signalGroup["signal_group"]} \nTrailing SL to {newSLString} ({newSL})")
                        for pos in filteredPositions:
                            request = {
                                "action": self.MT5Client.TRADE_ACTION_SLTP,
                                "position": pos.ticket,
                                "sl": float(newSL),
                                "tp": pos.tp,
                            }

                            self.MT5Client.order_send(request)

                    TimeModule.sleep(5)
            else:
                self.send_notification(f"<b>Orders Cancelled!</b> \n<b>{side} {symbol}</b> \n<b>Signal Group:</b> {signalGroup["signal_group"]} \n<b>Reason:</b> Not filled before {self.MT5expiration} min expiration")

        except Exception as e:
            self.send_notification(f"<b>Error manage_trade:</b> {e}")
            return None
        
    """
    method  wait_for_execution
    desc    Waits and checks if the trades are executed before expiring.
    input   [array[int]]completedOrders: The order number for each of the taken trades
    output  [int]: 0 if orders expired, 1 if all orders filled, 2 if partial orders filled
    """
    def wait_for_execution(self, completedOrders):
        self.send_notification(f"<b>Debug wait_for_execution:</b> Completed orders &gt {completedOrders}", True)
        startTime = TimeModule.time()
        AllOrdersCompleted = False
        SingleOrderCompleted = False

        while (TimeModule.time() - startTime) < ((self.MT5expiration * 60) + 40):
            currentPositions = self.MT5Client.positions_get()

            if currentPositions:
                activeTickets = {p.ticket for p in currentPositions}
                SingleOrderCompleted = False
                AllOrdersCompleted = True

                self.send_notification(f"<b>Debug wait_for_execution:</b> Currently active positions &gt {activeTickets}", True)

                for ticket in completedOrders:
                    if ticket in activeTickets:
                        SingleOrderCompleted = True

                    if ticket not in activeTickets:
                        AllOrdersCompleted = False

            else:
                self.send_notification(f"<b>Debug wait_for_execution:</b> No currently active positions &gt {currentPositions}", True)
            
            allPending = self.MT5Client.orders_get()
            pendingSet = {o.ticket for o in allPending} if allPending else set()
            allOrdersExpired = True

            self.send_notification(f"<b>Debug wait_for_execution:</b> Currently pending positions &gt {pendingSet}", True)

            for ticket in completedOrders:
                if ticket in pendingSet:
                    allOrdersExpired = False

            if AllOrdersCompleted:
                return 1
            elif allOrdersExpired and SingleOrderCompleted:
                return 2
            elif allOrdersExpired:
                return 0
                    
            TimeModule.sleep(5)
        
        return False
    
    """
    method  handle_closed_position
    desc    Determines the reason for a closed ticket and sends notification
    input   [Ticket]ticket: The closed position ticket from the broker
            [string]symbol: The symbol being traded
            [string]side: The side of the trade - "BUY" or "SELL"
            [dict]signalGroup: The required parameters for the signal group
    output  None
    """
    def handle_closed_position(self, ticket, symbol, side, signalGroup):
        try:
            # Get the ticket details from MT5
            deals = self.MT5Client.history_deals_get(position=ticket)
            
            if deals:
                # The last deal in the position history is usually the 'Out' (closing) deal
                closingDeal = deals[-1]
                profit = closingDeal.profit
                
                # Determine the reason
                reasonMap = {3: "Manual/System", 4: "Stop Loss", 5: "Take Profit"}
                reason = reasonMap.get(closingDeal.reason, f"Other ({closingDeal.reason})")

                self.send_notification(f"<b>Position Closed!</b> \n<b>{side} {symbol}</b> \n<b>Signal Group:</b> {signalGroup["signal_group"]} \n<b>Trade Profit:</b> {profit:.2f} \n<b>Exit Reason:</b> {reason}")
        
        except Exception as e:
            self.send_notification(f"<b>Error manage_trade:</b> {e}")
            return None
    
    """
    method  check_daily_profit
    desc    Checks the daily profit and disables trading for the day if the maximum daily loss is exceeded
    input   [string]symbol: The symbol being traded 
    output  [bool]allowTrades: True if 80% of the max loss is not exceeded, else False
            [float]totalProfit: The total daily profit
    """
    def check_daily_profit(self, symbol="XAUUSD"):
        try:
            # if set to 0, bypass max daily loss check and allow all trades
            if self.MT5maxDailyLoss == 0:
                return True
            
            allowTrades = False

            # Ensure the symbol is selected
            if not self.MT5Client.symbol_select(symbol, True):
                self.send_notification(f"<b>Error check_daily_profit:</b> {symbol} not found in broker list.")
                return False

            # Get all trades from the start of today until now
            tick = self.MT5Client.symbol_info_tick(symbol)
            if tick is None:
                self.send_notification(f"<b>Error check_daily_profit:</b> Could not determine the tick for {symbol}")
                return False
            
            brokerTime = tick.time
            todayStart = brokerTime - (brokerTime % 86400)
            historyDeals = self.MT5Client.history_deals_get(todayStart, int(brokerTime))
            self.send_notification(f"<b>Debug check_daily_profit:</b> Today's deals &gt {historyDeals}", True)

            if historyDeals is None:
                self.send_notification(f"<b>Error check_daily_profit:</b> Failed to fetch history, error code: {self.MT5Client.last_error()}")
                return 0.0
            
            totalProfit = 0.0

            # Calculate total profit from all trades today
            for deal in historyDeals:
                if deal.type in [self.MT5Client.DEAL_TYPE_BUY, self.MT5Client.DEAL_TYPE_SELL]:
                    # Net Profit = Gross Profit + Commission + Swap
                    netDealProfit = deal.profit + deal.commission + deal.swap
                    totalProfit += netDealProfit

            if totalProfit > (self.MT5maxDailyLoss * 0.8):
                allowTrades = True

            self.send_notification(f"<b>Debug check_daily_profit:</b> Today's profit &gt {totalProfit}", True)

            return allowTrades, totalProfit
        
        except Exception as e:
            self.send_notification(f"<b>Error check_daily_profit:</b> {e}")
            return None, None
        
    """
    method  get_balance
    desc    Checks the daily profit and disables trading for the day if the maximum daily loss is exceeded
    input   None
    output  [float]balance: The current account balance
            [float]openProfit: The proit/loss for open positions
    """
    def get_balance(self):
        try:
            accountInfo = self.MT5Client.account_info()

            if accountInfo is not None:
                # Access the balance property
                balance = accountInfo.balance
                openProfit = accountInfo.profit
                return balance, openProfit
            
            else:
                self.send_notification(f"<b>Error get_balance:</b> unable to get account information &gt {self.MT5Client.last_error()}")

        except Exception as e:
            self.send_notification(f"<b>Error get_balance:</b> {e}")
            return None, None
    
    """
    method  get_trade_history
    desc    Gets the history of the previous trades
    input   [int]amount: The amount of previous trades to return (default 5)
    output  [array[dict]]previousDeals: The details of the last trades
    """
    def get_trade_history(self, amount=5):
        try:
            fromDate = datetime.now() - timedelta(days=30)
            toDate = datetime.now()
            deals = self.MT5Client.history_deals_get(fromDate, toDate)

            if deals is None:
                return None
            
            elif len(deals) > 0:
                # Get the last 5 deals from the trade history
                previousDeals = [d for d in deals if d.entry == self.MT5Client.DEAL_ENTRY_OUT]
                previousDeals = previousDeals[-1*amount:]
                previousDeals = previousDeals[::-1]
                return previousDeals
            
            return None
        
        except Exception as e:
            self.send_notification(f"<b>Error get_trade_history:</b> {e}")
            return None
        
    """
    method  get_open_trades
    desc    Gets the currently open trades
    input   None
    output  [array[dict]]openPositions: The details of the currently open trades
    """
    def get_open_trades(self):
        try:
            openPositions = self.MT5Client.positions_get()
            return openPositions
        
        except Exception as e:
            self.send_notification(f"<b>Error get_open_trades:</b> {e}")
            return None
                
#endregion