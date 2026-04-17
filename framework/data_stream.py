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
        self.private_callbacks: Dict[str, Callable] = {}
        self._running = False
        self.latest_prices: Dict[str, float] = {}
        self.latest_bids: Dict[str, float] = {}
        self.latest_asks: Dict[str, float] = {}
        
        # Active asyncio tasks for streaming mapping (worker_key -> Task)
        self._tasks: Dict[str, asyncio.Task] = {}
        
        # Track active workers to prevent duplicates
        self._active_ticker_workers: Set[str] = set() # "symbol_futures"
        self._active_candle_workers: Set[str] = set() # "symbol_timeframe_futures"
        self._active_orderbook_workers: Set[str] = set() # "symbol_futures"
        self._active_private_workers: Set[str] = set() # "spot", "futures"
        
        # Track last candle timestamps: symbol_timeframe -> last_timestamp
        self._last_candle_ts: Dict[str, int] = {}
        self._ohlcv_cache: Dict[str, pd.DataFrame] = {}
        self._orderbook_cache: Dict[str, dict] = {}

    def register_engine(self, engine_name: str, callback: Callable, is_futures: bool = False):
        self._callbacks[engine_name] = callback
        if engine_name not in self.subscriptions:
            self.subscriptions[engine_name] = {'tickers': set(), 'candles': {}, 'orderbooks': set(), 'is_futures': is_futures}

    def unregister_engine(self, engine_name: str):
        if engine_name in self._callbacks:
            del self._callbacks[engine_name]
        if engine_name in self.subscriptions:
            del self.subscriptions[engine_name]
            
        if engine_name in self.private_callbacks:
            del self.private_callbacks[engine_name]
            
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
                
        for worker_key in list(self._active_orderbook_workers):
            needed = False
            for subs in self.subscriptions.values():
                is_fut = subs['is_futures']
                for sym in subs['orderbooks']:
                    if f"{sym}_{is_fut}" == worker_key:
                        needed = True
                        break
            if not needed:
                if worker_key in self._tasks:
                    self._tasks[worker_key].cancel()
                    del self._tasks[worker_key]
                self._active_orderbook_workers.remove(worker_key)
                logger.info(f"DataStream: Cancelled orphaned orderbook worker {worker_key}")
                
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

    def subscribe_orderbook(self, engine_name: str, symbol: str):
        if engine_name in self.subscriptions:
            self.subscriptions[engine_name]['orderbooks'].add(symbol)
            is_futures = self.subscriptions[engine_name]['is_futures']
            logger.info(f"DataStream: Engine '{engine_name}' subscribed to orderbook {symbol}")
            
            if self._running:
                self._ensure_orderbook_worker(symbol, is_futures)

    def get_orderbook(self, symbol: str) -> dict | None:
        return self._orderbook_cache.get(symbol)

    def register_private_callback(self, engine_name: str, callback: Callable):
        self.private_callbacks[engine_name] = callback
        logger.info(f"DataStream: Private callback registered for '{engine_name}'")

    def unregister_private_callback(self, engine_name: str):
        if engine_name in self.private_callbacks:
            del self.private_callbacks[engine_name]
            logger.info(f"DataStream: Private callback unregistered for '{engine_name}'")

    def _ensure_private_workers(self, is_futures: bool):
        worker_type = "futures" if is_futures else "spot"
        if worker_type not in self._active_private_workers:
            logger.info(f"DataStream: Spawning {worker_type.upper()} private stream workers (orders & balance)")
            
            # Start order watcher
            order_task = asyncio.create_task(self._watch_orders_loop(is_futures))
            self._tasks[f"orders_{worker_type}"] = order_task
            
            # Start balance watcher
            balance_task = asyncio.create_task(self._watch_balance_loop(is_futures))
            self._tasks[f"balance_{worker_type}"] = balance_task
            
            self._active_private_workers.add(worker_type)

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

    def _ensure_orderbook_worker(self, symbol: str, is_futures: bool):
        worker_key = f"ob_{symbol}_{is_futures}"
        if worker_key not in self._active_orderbook_workers:
            logger.info(f"DataStream: Spawning dynamic orderbook worker for {symbol} (Futures: {is_futures})")
            task = asyncio.create_task(self._watch_orderbook_loop(symbol, is_futures))
            self._tasks[worker_key] = task
            self._active_orderbook_workers.add(worker_key)

    async def _watch_orderbook_loop(self, symbol: str, is_futures: bool, limit: int = 20):
        client = kucoin_futures_client if is_futures else kucoin_client
        while self._running:
            try:
                ob = await client.exchange.watch_order_book(symbol, limit)
                self._orderbook_cache[symbol] = ob
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS OrderBook Error ({symbol}): {e}")
                await asyncio.sleep(5)

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
            for sym in subs['orderbooks']:
                self._ensure_orderbook_worker(sym, is_futures)
                
        logger.info("DataStream WebSocket workers started.")

    async def stop(self):
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._active_ticker_workers.clear()
        self._active_candle_workers.clear()
        self._active_private_workers.clear()
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
        needs_backfill = False
        
        # 1. Warm-up Cache (One-time REST call to populate historical data)
        if tracker_key not in self._ohlcv_cache:
            try:
                logger.info(f"DataStream: Warming up OHLCV cache for {symbol} {timeframe}")
                ohlcv = await client.exchange.fetch_ohlcv(symbol, timeframe, limit=200)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                self._ohlcv_cache[tracker_key] = df
            except Exception as e:
                logger.error(f"DataStream: Failed to warm up cache for {symbol}: {e}")
                # We continue anyway, the first tick will attempt to fill it or the next loop will retry
        
        while self._running:
            try:
                # REST Backfill Mechanism: Sync cache if reconnection occurred
                if needs_backfill:
                    try:
                        logger.info(f"DataStream: [Backfill] Re-syncing OHLVC cache for {symbol} {timeframe}")
                        ohlcv = await client.exchange.fetch_ohlcv(symbol, timeframe, limit=200)
                        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)
                        
                        self._ohlcv_cache[tracker_key] = df
                        # Reset timestamp tracker to ensure the next WS tick primes correctly without duplication
                        if tracker_key in self._last_candle_ts:
                            del self._last_candle_ts[tracker_key]
                        needs_backfill = False # Successfully backfilled
                    except Exception as e:
                        logger.error(f"DataStream: [Backfill] Failed to resync {symbol}: {e}. Retrying...")
                        await asyncio.sleep(5)
                        continue # Retry backfill before starting watch loop

                # CCXT watch_ohlcv returns the full candle list cached in memory
                candles = await client.exchange.watch_ohlcv(symbol, timeframe)
                if not candles or len(candles) < 2: continue
                
                # The last candle in the list is the one CURRENTLY open (live)
                current_candle = candles[-1]
                timestamp = current_candle[0]
                
                last_ts = self._last_candle_ts.get(tracker_key)
                
                # If timestamp changed, the previous candle has CLOSED.
                if last_ts is not None and timestamp > last_ts:
                    # The finalized candle data is now at the second-to-last position
                    closed_candle_raw = candles[-2]
                    
                    # 2. Update In-Memory Cache (Event-based, Zero REST calls)
                    if tracker_key in self._ohlcv_cache:
                        df = self._ohlcv_cache[tracker_key]
                        
                        # Create a new row with proper datetime index
                        new_ts = pd.to_datetime(closed_candle_raw[0], unit='ms')
                        new_row = pd.DataFrame([{
                            'open': float(closed_candle_raw[1]),
                            'high': float(closed_candle_raw[2]),
                            'low': float(closed_candle_raw[3]),
                            'close': float(closed_candle_raw[4]),
                            'volume': float(closed_candle_raw[5])
                        }], index=[new_ts])
                                             
                        # Append and maintain a rolling window of 200 bars
                        df = pd.concat([df, new_row])
                        df = df[~df.index.duplicated(keep='last')]
                        if len(df) > 200:
                            df = df.iloc[-200:]
                        
                        self._ohlcv_cache[tracker_key] = df
                        
                        # 3. Synchronously push to all subscribed engines
                        for eng_name, subs in self.subscriptions.items():
                            if subs['is_futures'] == is_futures and subs['candles'].get(symbol) == timeframe:
                                cb = self._callbacks.get(eng_name)
                                if cb:
                                    # Send a copy to prevent engines from mutating shared cache
                                    asyncio.create_task(cb(symbol, df.copy(), 'candle'))
                
                self._last_candle_ts[tracker_key] = timestamp
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS OHLCV Error ({symbol}): {e}")
                needs_backfill = True # Trigger backfill on reconnection
                await asyncio.sleep(5)

    async def _watch_orders_loop(self, is_futures: bool):
        client = kucoin_futures_client if is_futures else kucoin_client
        while self._running:
            try:
                # CCXT watch_orders returns an array of incrementing order updates
                orders = await client.exchange.watch_orders()
                if not orders: continue
                
                for order in orders:
                    # Broadcast to all registered private callbacks
                    for eng_name, cb in list(self.private_callbacks.items()):
                        # The Kernel-level router will handle filtering by symbol/engine
                        asyncio.create_task(cb(order, 'order'))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS Private Orders Error ({'Futures' if is_futures else 'Spot'}): {e}")
                await asyncio.sleep(5)

    async def _watch_balance_loop(self, is_futures: bool):
        client = kucoin_futures_client if is_futures else kucoin_client
        while self._running:
            try:
                balance = await client.exchange.watch_balance()
                if not balance: continue
                
                # Broadcast to all registered private callbacks
                for eng_name, cb in list(self.private_callbacks.items()):
                    asyncio.create_task(cb(balance, 'balance'))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WS Private Balance Error ({'Futures' if is_futures else 'Spot'}): {e}")
                await asyncio.sleep(5)
