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
        self.latest_bids: Dict[str, float] = {}
        self.latest_asks: Dict[str, float] = {}
        
        # Active asyncio tasks for streaming mapping (worker_key -> Task)
        self._tasks: Dict[str, asyncio.Task] = {}
        
        # Track active workers to prevent duplicates
        self._active_ticker_workers: Set[str] = set() # "symbol_futures"
        self._active_candle_workers: Set[str] = set() # "symbol_timeframe_futures"
        
        # Track last candle timestamps: symbol_timeframe -> last_timestamp
        self._last_candle_ts: Dict[str, int] = {}

    def register_engine(self, engine_name: str, callback: Callable, is_futures: bool = False):
        self._callbacks[engine_name] = callback
        if engine_name not in self.subscriptions:
            self.subscriptions[engine_name] = {'tickers': set(), 'candles': {}, 'is_futures': is_futures}

    def unregister_engine(self, engine_name: str):
        if engine_name in self._callbacks:
            del self._callbacks[engine_name]
        if engine_name in self.subscriptions:
            del self.subscriptions[engine_name]
            
        # --- Garbage Collection untuk Orphaned Tasks ---
        needed_ticker_workers = set()
        needed_candle_workers = set()
        
        for subs in self.subscriptions.values():
            is_fut = subs['is_futures']
            for sym in subs['tickers']:
                needed_ticker_workers.add(f"{sym}_{is_fut}")
            for sym, tf in subs['candles'].items():
                needed_candle_workers.add(f"{sym}_{tf}_{is_fut}")
                
        for worker_key in list(self._active_ticker_workers):
            if worker_key not in needed_ticker_workers:
                if worker_key in self._tasks:
                    self._tasks[worker_key].cancel()
                    del self._tasks[worker_key]
                self._active_ticker_workers.remove(worker_key)
                logger.info(f"DataStream: Cancelled orphaned ticker worker {worker_key}")
                
        for worker_key in list(self._active_candle_workers):
            if worker_key not in needed_candle_workers:
                if worker_key in self._tasks:
                    self._tasks[worker_key].cancel()
                    del self._tasks[worker_key]
                self._active_candle_workers.remove(worker_key)
                logger.info(f"DataStream: Cancelled orphaned candle worker {worker_key}")
                
        logger.info(f"DataStream: Engine '{engine_name}' unregistered.")

    def subscribe_ticker(self, engine_name: str, symbol: str):
        if engine_name in self.subscriptions:
            self.subscriptions[engine_name]['tickers'].add(symbol)
            is_futures = self.subscriptions[engine_name]['is_futures']
            logger.info(f"DataStream: Engine '{engine_name}' subscribed to ticker {symbol}")
            
            if self._running:
                self._ensure_ticker_worker(symbol, is_futures)

    def subscribe_candles(self, engine_name: str, symbol: str, timeframe: str):
        if engine_name in self.subscriptions:
            self.subscriptions[engine_name]['candles'][symbol] = timeframe
            is_futures = self.subscriptions[engine_name]['is_futures']
            logger.info(f"DataStream: Engine '{engine_name}' subscribed to candles {symbol} ({timeframe})")
            
            if self._running:
                self._ensure_candle_worker(symbol, timeframe, is_futures)

    def _ensure_ticker_worker(self, symbol: str, is_futures: bool):
        worker_key = f"{symbol}_{is_futures}"
        if worker_key not in self._active_ticker_workers:
            logger.info(f"DataStream: Spawning dynamic ticker worker for {symbol} (Futures: {is_futures})")
            task = asyncio.create_task(self._watch_ticker_loop(symbol, is_futures))
            self._tasks[worker_key] = task
            self._active_ticker_workers.add(worker_key)

    def _ensure_candle_worker(self, symbol: str, timeframe: str, is_futures: bool):
        worker_key = f"{symbol}_{timeframe}_{is_futures}"
        if worker_key not in self._active_candle_workers:
            logger.info(f"DataStream: Spawning dynamic candle worker for {symbol} {timeframe} (Futures: {is_futures})")
            task = asyncio.create_task(self._watch_ohlcv_loop(symbol, timeframe, is_futures))
            self._tasks[worker_key] = task
            self._active_candle_workers.add(worker_key)

    async def start(self):
        if self._running:
            return
        self._running = True
        
        # Collect unique subscriptions and start workers
        for subs in self.subscriptions.values():
            is_futures = subs['is_futures']
            for sym in subs['tickers']:
                self._ensure_ticker_worker(sym, is_futures)
            for sym, tf in subs['candles'].items():
                self._ensure_candle_worker(sym, tf, is_futures)
                
        logger.info("DataStream WebSocket workers started.")

    async def stop(self):
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._active_ticker_workers.clear()
        self._active_candle_workers.clear()
        logger.info("DataStream WebSocket workers stopped.")

    async def _watch_ticker_loop(self, symbol: str, is_futures: bool):
        client = kucoin_futures_client if is_futures else kucoin_client
        while self._running:
            try:
                # CCXT pro watch_ticker blocks until next update
                ticker = await client.exchange.watch_ticker(symbol)
                price = float(ticker.get('last', 0))
                bid = float(ticker.get('bid', 0))
                ask = float(ticker.get('ask', 0))
                if price == 0: continue
                
                self.latest_prices[symbol] = price
                if bid > 0: self.latest_bids[symbol] = bid
                if ask > 0: self.latest_asks[symbol] = ask
                
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
                        ohlcv = await client.exchange.fetch_ohlcv(symbol, timeframe, limit=200)
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
