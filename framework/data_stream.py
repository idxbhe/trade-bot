import asyncio
import pandas as pd
from typing import Dict, Set, Callable
from core.logger import logger
from exchange.kucoin import kucoin_client, kucoin_futures_client

class DataStream:
    """
    Handles robust WebSocket data fetching using CCXT Pro.
    Streams tickers and OHLCV natively, pushing events to subscribed engines.
    """
    def __init__(self):
        # engine_name -> { 'tickers': set(), 'candles': {symbol: timeframe}, 'is_futures': bool }
        self.subscriptions: Dict[str, Dict[str, set|dict|bool]] = {}
        self._callbacks: Dict[str, Callable] = {}
        self._running = False
        self.latest_prices: Dict[str, float] = {}
        
        # Active asyncio tasks for streaming
        self._tasks: set = set()
        
        # Track last candle timestamps: symbol_timeframe -> last_timestamp
        self._last_candle_ts: Dict[str, int] = {}

    def register_engine(self, engine_name: str, callback: Callable, is_futures: bool = False):
        self._callbacks[engine_name] = callback
        if engine_name not in self.subscriptions:
            self.subscriptions[engine_name] = {'tickers': set(), 'candles': {}, 'is_futures': is_futures}

    def subscribe_ticker(self, engine_name: str, symbol: str):
        if engine_name in self.subscriptions:
            self.subscriptions[engine_name]['tickers'].add(symbol)
            logger.info(f"DataStream: Engine '{engine_name}' subscribed to ticker {symbol}")

    def subscribe_candles(self, engine_name: str, symbol: str, timeframe: str):
        if engine_name in self.subscriptions:
            self.subscriptions[engine_name]['candles'][symbol] = timeframe
            logger.info(f"DataStream: Engine '{engine_name}' subscribed to candles {symbol} ({timeframe})")

    async def start(self):
        if self._running:
            return
        self._running = True
        
        # Collect unique subscriptions
        spot_tickers = set()
        futures_tickers = set()
        spot_candles = {} # symbol -> set(timeframes)
        futures_candles = {}
        
        for eng_name, subs in self.subscriptions.items():
            is_futures = subs['is_futures']
            tickers = spot_tickers if not is_futures else futures_tickers
            candles = spot_candles if not is_futures else futures_candles
            
            tickers.update(subs['tickers'])
            for sym, tf in subs['candles'].items():
                if sym not in candles: candles[sym] = set()
                candles[sym].add(tf)

        # Start Ticker Streams
        for sym in spot_tickers:
            task = asyncio.create_task(self._watch_ticker_loop(sym, False))
            self._tasks.add(task)
        for sym in futures_tickers:
            task = asyncio.create_task(self._watch_ticker_loop(sym, True))
            self._tasks.add(task)
            
        # Start Candle Streams
        for sym, tfs in spot_candles.items():
            for tf in tfs:
                task = asyncio.create_task(self._watch_ohlcv_loop(sym, tf, False))
                self._tasks.add(task)
        for sym, tfs in futures_candles.items():
            for tf in tfs:
                task = asyncio.create_task(self._watch_ohlcv_loop(sym, tf, True))
                self._tasks.add(task)
                
        logger.info("DataStream WebSocket workers started.")

    async def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info("DataStream WebSocket workers stopped.")

    async def _watch_ticker_loop(self, symbol: str, is_futures: bool):
        client = kucoin_futures_client if is_futures else kucoin_client
        while self._running:
            try:
                # CCXT pro watch_ticker blocks until next update
                ticker = await client.exchange.watch_ticker(symbol)
                price = float(ticker.get('last', 0))
                if price == 0: continue
                
                self.latest_prices[symbol] = price
                
                # Push to subscribed engines
                for eng_name, subs in self.subscriptions.items():
                    if subs['is_futures'] == is_futures and symbol in subs['tickers']:
                        cb = self._callbacks.get(eng_name)
                        if cb:
                            asyncio.create_task(cb(symbol, ticker, 'tick'))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS Ticker Error ({symbol}): {e}")
                await asyncio.sleep(5)

    async def _watch_ohlcv_loop(self, symbol: str, timeframe: str, is_futures: bool):
        client = kucoin_futures_client if is_futures else kucoin_client
        tracker_key = f"{symbol}_{timeframe}"
        
        while self._running:
            try:
                candles = await client.exchange.watch_ohlcv(symbol, timeframe)
                if not candles or len(candles) == 0: continue
                
                latest_candle = candles[-1]
                timestamp = latest_candle[0]
                
                last_ts = self._last_candle_ts.get(tracker_key)
                
                # If timestamp changed, the previous candle has CLOSED.
                if last_ts is not None and timestamp > last_ts:
                    # Fetch historical data dynamically to pass a rich dataframe
                    # Since CCXT cache might not have 50 historical candles natively without fetch_ohlcv
                    try:
                        ohlcv = await client.exchange.fetch_ohlcv(symbol, timeframe, limit=50)
                        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)
                        
                        for eng_name, subs in self.subscriptions.items():
                            if subs['is_futures'] == is_futures and subs['candles'].get(symbol) == timeframe:
                                cb = self._callbacks.get(eng_name)
                                if cb:
                                    asyncio.create_task(cb(symbol, df, 'candle'))
                    except Exception as fe:
                        logger.error(f"Failed fetching historical for closed candle {symbol}: {fe}")
                
                self._last_candle_ts[tracker_key] = timestamp
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS OHLCV Error ({symbol}): {e}")
                await asyncio.sleep(5)
