import asyncio
from typing import Dict, Any, Optional
import time
from core.logger import logger
from framework.state_manager import StateManager
from framework.data_stream import DataStream
from framework.context import TradingContext
from execution.order_manager import order_manager
from data.fetcher import market_collector

class Kernel:
    """
    The Main Orchestrator.
    Bridges Engines, Data Streams, DB State, and UI safely.
    It isolates the engine from the messy world of database I/O and API limits.
    """
    def __init__(self):
        self.state_manager = StateManager()
        self.data_stream = DataStream()
        self.engines = {} # active engine instances
        self.contexts = {} # engine_name -> TradingContext

    async def start(self):
        """Start background services (DB syncing, Data Streaming)."""
        self.state_manager.start_sync_loop()
        await self.data_stream.start()
        logger.info("Framework Kernel started.")

    async def stop(self):
        """Stop background services safely."""
        for eng in self.engines.values():
            if hasattr(eng, 'shutdown'):
                await eng.shutdown()
        await self.data_stream.stop()
        await self.state_manager.stop_sync_loop()
        logger.info("Framework Kernel stopped.")

    async def load_engine(self, engine_instance, mode: str, market: str, initial_equity: float):
        """Phase 1: Load state from DB and init memory. No data streaming yet."""
        name = engine_instance.name
        self.engines[name] = engine_instance
        
        # 1. Init state memory
        self.state_manager.initialize_engine_state(name, initial_equity, mode, market)
        self.state_manager.state[name]['ui']['phase'] = 'STANDBY'
        self.state_manager.state[name]['ui']['latest_activity'] = 'Engine Loaded (Standby)'
        
        # 2. Load from DB
        await self.state_manager.load_state_from_db(name)
        
        # Subscribe to tickers for existing positions so UI gets live prices while in standby
        # We need to make sure the DataStream has registered the engine first
        self.data_stream.register_engine(name, lambda s, d, e: None, market.lower() == 'futures')
        for symbol in self.state_manager.state[name]['positions'].keys():
            self.data_stream.subscribe_ticker(name, symbol)
        
        # 3. Create Context DI
        ctx = TradingContext(self, name, mode, market)
        self.contexts[name] = ctx
        
        logger.info(f"Engine '{name}' state loaded. Standing by.")

    async def start_engine(self, engine_name: str):
        """Phase 2: Bind data streams and execute on_start."""
        if engine_name not in self.engines or engine_name not in self.contexts:
            logger.error(f"Cannot start engine '{engine_name}', it is not loaded.")
            return

        engine_instance = self.engines[engine_name]
        ctx = self.contexts[engine_name]
        
        self.state_manager.state[engine_name]['ui']['phase'] = 'IDLE'
        self.state_manager.state[engine_name]['ui']['latest_activity'] = 'Engine starting...'
        
        # Bind data stream to engine's events
        async def event_router(symbol: str, data: dict, event_type: str):
            if event_type == 'tick' and hasattr(engine_instance, 'on_tick'):
                await engine_instance.on_tick(symbol, data)
            elif event_type == 'candle' and hasattr(engine_instance, 'on_candle_closed'):
                await engine_instance.on_candle_closed(symbol, data)
                
        is_futures = ctx.market.lower() == 'futures'
        self.data_stream.register_engine(engine_name, event_router, is_futures)
        
        # Start Engine
        if hasattr(engine_instance, 'on_start'):
            await engine_instance.on_start(ctx)
            
        logger.info(f"Engine '{engine_name}' started and hooked into Kernel. Awaiting data...")

    async def stop_engine(self, engine_name: str):
        """Stop the engine logic but keep UI prices active for open positions."""
        if engine_name in self.engines:
            engine_instance = self.engines[engine_name]
            if hasattr(engine_instance, 'shutdown'):
                await engine_instance.shutdown()
            
            # PAUSE: We replace the event router with a no-op so strategy doesn't execute
            # But we DON'T unregister from DataStream yet, to keep floating PnL updating
            async def no_op_router(symbol: str, data: dict, event_type: str):
                pass
                
            ctx = self.contexts.get(engine_name)
            if ctx:
                is_futures = ctx.market.lower() == 'futures'
                self.data_stream.register_engine(engine_name, no_op_router, is_futures)
            
            self.state_manager.state[engine_name]['ui']['phase'] = 'STANDBY'
            self.state_manager.state[engine_name]['ui']['latest_activity'] = 'Engine paused (Standby).'
            logger.info(f"Engine '{engine_name}' logic stopped. Prices still tracking.")

    def unload_engine(self, engine_name: str):
        """Clean up all resources (DataStream workers and RAM state)."""
        # 1. Unregister from DataStream (cancels WebSocket tasks)
        self.data_stream.unregister_engine(engine_name)
        
        # 2. Clear from StateManager (frees RAM)
        self.state_manager.clear_engine_state(engine_name)
        
        # 3. Remove local references
        if engine_name in self.engines:
            del self.engines[engine_name]
        if engine_name in self.contexts:
            del self.contexts[engine_name]
            
        logger.info(f"Engine '{engine_name}' fully unloaded from memory and network.")

    def update_engine_mode(self, engine_name: str, mode: str):
        """Change the execution mode (TEST/LIVE) for an engine at runtime."""
        if engine_name in self.contexts:
            self.contexts[engine_name].mode = mode
        if engine_name in self.state_manager.state:
            self.state_manager.state[engine_name]['mode'] = mode
        logger.info(f"Engine '{engine_name}' mode updated to: {mode}")

    # --- Engine API Implementations (Called by TradingContext) ---
    
    def subscribe_ticker(self, engine_name: str, symbol: str):
        self.data_stream.subscribe_ticker(engine_name, symbol)

    def subscribe_candles(self, engine_name: str, symbol: str, timeframe: str):
        self.data_stream.subscribe_candles(engine_name, symbol, timeframe)

    async def get_historical_data(self, symbol: str, timeframe: str, limit: int):
        return await market_collector.get_historical_data(symbol, timeframe, limit)

    async def get_order_book(self, engine_name: str, symbol: str, limit: int):
        ctx = self.contexts.get(engine_name)
        if not ctx: return {}
        
        if ctx.market.lower() == 'futures':
            from exchange.kucoin import kucoin_futures_client
            return await kucoin_futures_client.exchange.fetch_order_book(symbol, limit=limit)
        else:
            from exchange.kucoin import kucoin_client
            return await kucoin_client.exchange.fetch_order_book(symbol, limit=limit)

    async def place_order(self, engine_name: str, symbol: str, side: str, amount: float, price: float, sl: float, tp: float, post_only: bool) -> bool:
        """Execute order and synchronously update Memory State (DB is async)."""
        ctx = self.contexts.get(engine_name)
        if not ctx: return False
        
        if ctx.mode == 'LIVE':
            ccxt_side = 'buy' if side == 'BUY' else 'sell'
            leverage = getattr(self.engines[engine_name], 'leverage', 1)
            
            order = await order_manager.execute_limit_order(
                symbol=symbol, side=ccxt_side, amount=amount, price=price,
                market=ctx.market, leverage=leverage, post_only=post_only
            )
            if not order: return False
                
        # Update Memory State Instantly
        current_eq = self.state_manager.get_equity(engine_name)
        
        # EQUITY LOGIC: Spot vs Futures
        if ctx.market.lower() == 'futures':
            # Futures: Margin is part of equity, we only track PnL on close
            pass
        else:
            # Spot: Subtract full cost from cash balance
            cost = price * amount
            self.state_manager.update_equity(engine_name, current_eq - cost)
        
        pos_data = {
            'entry_price': price,
            'amount': amount,
            'stop_loss': sl,
            'take_profit': tp,
            'max_pnl': 0.0,
            'min_pnl': 0.0
        }
        pos_side = 'LONG' if side == 'BUY' else 'SHORT'
        self.state_manager.add_position(engine_name, symbol, pos_data, pos_side)
        
        # Log execution
        self.report_status(engine_name, symbol, "EXEC", f"OPEN {pos_side} {amount:.4f} @ ${price:,.2f}")
        
        return True

    async def manual_close_position(self, engine_name: str, symbol: str) -> bool:
        """Robustly close a position manually, fetching the latest price first."""
        ctx = self.contexts.get(engine_name)
        if not ctx:
            logger.error(f"Manual Close failed: Engine '{engine_name}' not loaded.")
            return False

        pos = self.state_manager.get_position(engine_name, symbol)
        if not pos:
            logger.warning(f"Manual Close failed: No active position found for {symbol} in '{engine_name}'.")
            return False

        # Get the most recent price from DataStream for a precise exit
        price = self.data_stream.latest_prices.get(symbol)
        if not price:
            # Fallback to entry price if no tick data yet (rare)
            price = pos['entry_price']
            logger.warning(f"Manual Close: No tick data for {symbol}, using entry price as fallback.")

        self.report_status(engine_name, symbol, "EXEC", "Manual Close requested...")
        
        success = await self.close_position(engine_name, symbol, price, "MANUAL_CLOSE")
        
        if success:
            logger.info(f"Manual Close successful for {symbol} @ ${price:,.2f}")
        else:
            logger.error(f"Manual Close FAILED for {symbol} via Exchange API.")
            self.report_status(engine_name, symbol, "ERROR", "Manual Close Failed!")
            
        return success

    async def close_position(self, engine_name: str, symbol: str, exit_price: float, reason: str) -> bool:
        ctx = self.contexts.get(engine_name)
        if not ctx: return False
        
        pos = self.state_manager.get_position(engine_name, symbol)
        if not pos: return False
        
        if ctx.mode == 'LIVE':
            ccxt_side = 'sell' if pos['side'] == 'LONG' else 'buy'
            leverage = getattr(self.engines[engine_name], 'leverage', 1)
            
            order = await order_manager.execute_limit_order(
                symbol=symbol, side=ccxt_side, amount=pos['amount'], price=exit_price,
                market=ctx.market, leverage=leverage, post_only=True
            )
            if not order:
                logger.warning(f"[{engine_name}] Failed to close {symbol} via {ctx.market} API")
                return False
            
        # Update Memory Equity
        revenue = exit_price * pos['amount']
        current_eq = self.state_manager.get_equity(engine_name)
        
        # PnL Calculation (simulate 0.1% fee)
        fee_rate = 0.001
        simulated_fees = (pos['entry_price'] * pos['amount'] * fee_rate) + (exit_price * pos['amount'] * fee_rate)
        
        if pos['side'] == 'LONG':
            pnl = (exit_price - pos['entry_price']) * pos['amount'] - simulated_fees
        else:
            pnl = (pos['entry_price'] - exit_price) * pos['amount'] - simulated_fees

        if ctx.market.lower() == 'futures':
            self.state_manager.update_equity(engine_name, current_eq + pnl)
        else:
            self.state_manager.update_equity(engine_name, current_eq + revenue - simulated_fees)
            
        # Aggregate Stats Fast
        if engine_name in self.state_manager.state:
            stats = self.state_manager.state[engine_name]['stats']
            stats['total_pnl'] += pnl
            stats['trade_count'] += 1
            if pnl > 0:
                wins = (stats['win_rate'] / 100) * (stats['trade_count'] - 1)
                stats['win_rate'] = ((wins + 1) / stats['trade_count']) * 100
            else:
                wins = (stats['win_rate'] / 100) * (stats['trade_count'] - 1)
                stats['win_rate'] = (wins / stats['trade_count']) * 100

        self.state_manager.remove_position(engine_name, symbol)
        
        # Add to DB queue history
        self.state_manager.record_history(engine_name, {
            'symbol': symbol,
            'side': pos['side'],
            'amount': pos['amount'],
            'entry': pos['entry_price'],
            'exit': exit_price,
            'pnl': pnl,
            'max_pnl': pos.get('max_pnl', 0.0),
            'min_pnl': pos.get('min_pnl', 0.0),
            'reason': reason,
            'time': time.strftime("%H:%M:%S")
        })
        
        self.report_status(engine_name, symbol, "EXEC", f"CLOSE {pos['side']} ({reason}) @ ${exit_price:,.2f} | PnL: ${pnl:,.2f}")
        return True

    def log(self, engine_name: str, message: str, level: str):
        if level.upper() == "ERROR":
            logger.error(f"[{engine_name}] {message}")
        elif level.upper() == "WARNING":
            logger.warning(f"[{engine_name}] {message}")
        else:
            logger.info(f"[{engine_name}] {message}")
            
    def report_status(self, engine_name: str, symbol: str, phase: str, message: str):
        self.state_manager.update_ui_status(engine_name, symbol, phase, message)
        
    def get_ui_state(self, engine_name: str) -> dict:
        """Called safely by the TUI loop to get a snapshot of the engine."""
        return self.state_manager.get_ui_state(engine_name, self.data_stream.latest_prices)
        
    def get_ui_orders(self, engine_name: str) -> list:
        """Called safely by the TUI loop to get active orders."""
        return self.state_manager.get_ui_orders(engine_name, self.data_stream.latest_prices)

    def get_ui_history(self, engine_name: str) -> list:
        """Called safely by the TUI loop to get new trade history entries."""
        return self.state_manager.get_ui_history(engine_name)

# Global singleton
kernel = Kernel()
