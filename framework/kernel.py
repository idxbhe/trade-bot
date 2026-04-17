import asyncio
from typing import Dict, Any, Optional
import time
from core.logger import logger
from framework.state_manager import StateManager
from framework.data_stream import DataStream
from framework.context import TradingContext
from execution.order_manager import order_manager
from data.fetcher import market_collector
from exchange.kucoin import kucoin_client, kucoin_futures_client

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
        self.engine_symbol_locks = {} # engine_name -> {symbol: asyncio.Lock()}

    async def start(self):
        """Start background services (DB syncing and Data Streaming)."""
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
        """Phase 1: Load state from DB and init memory. Sync real balance if LIVE."""
        name = engine_instance.name
        self.engines[name] = engine_instance
        
        # 1. Init state memory (pre-load with virtual as placeholder)
        self.state_manager.initialize_engine_state(name, initial_equity, mode, market)
        self.state_manager.state[name]['ui']['phase'] = 'STANDBY'
        self.state_manager.state[name]['ui']['latest_activity'] = 'Syncing Balance...'
        
        # 2. Sync Real Balance if LIVE
        if mode == 'LIVE':
            is_futures = market.lower() == 'futures'
            client = kucoin_futures_client if is_futures else kucoin_client
            try:
                real_bal = await client.fetch_balance()
                # We update equity even if it is 0.0 to reflect real account state
                self.state_manager.update_equity(name, real_bal)
                logger.info(f"[{name}] Sync complete: Real {market.upper()} Balance is ${real_bal:,.2f}")
            except Exception as e:
                logger.error(f"[{name}] Failed to fetch real balance from API: {e}")
        
        # 3. Add Private WebSocket Monitoring if LIVE
        if mode == 'LIVE':
            self.data_stream.register_private_callback("KERNEL_SYSTEM", self._handle_private_data)
            self.data_stream._ensure_private_workers(market.lower() == 'futures')

        # 4. Load from DB (overwrites memory with saved values if exist, unless fresh)
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
        
        if engine_name not in self.engine_symbol_locks:
            self.engine_symbol_locks[engine_name] = {}

        # Bind data stream to engine's events with per-symbol locking
        async def event_router(symbol: str, data: dict, event_type: str):
            if symbol not in self.engine_symbol_locks[engine_name]:
                self.engine_symbol_locks[engine_name][symbol] = asyncio.Lock()
                
            async with self.engine_symbol_locks[engine_name][symbol]:
                # Mencegah eksekusi antrean (backlog) jika engine baru saja dihentikan
                current_phase = self.state_manager.state.get(engine_name, {}).get('ui', {}).get('phase')
                if current_phase == 'STANDBY':
                    return

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
            
        if engine_name in self.engine_symbol_locks:
            del self.engine_symbol_locks[engine_name]
            
        logger.info(f"Engine '{engine_name}' fully unloaded from memory and network.")

    async def update_engine_mode(self, engine_name: str, mode: str, market: str, initial_equity: float):
        """Change the execution mode (TEST/LIVE) for an engine by reloading its state."""
        if engine_name in self.engines:
            engine_instance = self.engines[engine_name]
            
            # 1. Fully unload old mode state and workers
            self.unload_engine(engine_name)
            
            # 2. Reload with new mode (fetches correct DB history/balance)
            await self.load_engine(engine_instance, mode, market, initial_equity)
            
            logger.info(f"Engine '{engine_name}' successfully switched to mode: {mode}")

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
        
        # Penyesuaian Harga Post-Only (Maker) menggunakan data real-time Order Book
        if post_only:
            best_bid = self.data_stream.latest_bids.get(symbol)
            best_ask = self.data_stream.latest_asks.get(symbol)
            
            if best_bid and best_ask:
                if side == 'BUY' and price >= best_ask:
                    self.log(engine_name, f"Post-Only BUY price (${price}) crosses ask. Auto-adjusting to Best Bid (${best_bid}).", "WARNING")
                    price = best_bid
                elif side == 'SELL' and price <= best_bid:
                    self.log(engine_name, f"Post-Only SELL price (${price}) crosses bid. Auto-adjusting to Best Ask (${best_ask}).", "WARNING")
                    price = best_ask

        if ctx.mode == 'LIVE':
            ccxt_side = 'buy' if side == 'BUY' else 'sell'
            leverage = getattr(self.engines[engine_name], 'leverage', 1)
            
            order = await order_manager.execute_limit_order(
                symbol=symbol, side=ccxt_side, amount=amount, price=price,
                market=ctx.market, leverage=leverage, post_only=post_only
            )
            if not order: return False
            
            # PENDING ORDER LOGIC: Simpan sebagai pending, jangan langsung buka posisi
            order_id = order.get('id')
            self.state_manager.add_pending_order(engine_name, order_id, {
                'symbol': symbol, 'side': side, 'amount': amount, 'price': price,
                'sl': sl, 'tp': tp, 'time': time.time()
            })
            
            # Log pending status
            self.report_status(engine_name, symbol, "EXEC", f"PENDING {side} {amount:.4f} @ ${price:,.2f} (ID: {order_id})")
            return True
                
        # Update Memory State Instantly (Hanya untuk TEST mode)
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
        
        # Kill exchange protections before manual close to avoid double execution
        await self._cancel_position_protection(engine_name, symbol)
        
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
        
        actual_exit_price = exit_price
        
        if ctx.mode == 'LIVE':
            ccxt_side = 'sell' if pos['side'] == 'LONG' else 'buy'
            leverage = getattr(self.engines[engine_name], 'leverage', 1)
            
            # Emergency logic for Market Orders
            emergency_reasons = ['STOP_LOSS', 'MANUAL_CLOSE', 'CIRCUIT_BREAKER', 'DYNAMIC_EXIT']
            
            if reason in emergency_reasons:
                logger.warning(f"[{engine_name}] Emergency exit triggered for {symbol} (Reason: {reason}). Executing Market Order.")
                order = await order_manager.execute_market_order(
                    symbol=symbol, side=ccxt_side, amount=pos['amount'],
                    market=ctx.market, leverage=leverage, reduce_only=True
                )
            else:
                # Standard exit: Limit Order but with post_only=False to ensure execution
                order = await order_manager.execute_limit_order(
                    symbol=symbol, side=ccxt_side, amount=pos['amount'], price=exit_price,
                    market=ctx.market, leverage=leverage, post_only=False, reduce_only=True
                )
            
            if not order:
                logger.error(f"[{engine_name}] Failed to close {symbol} via {ctx.market} API ({reason})")
                return False
                
            # Bersihkan proteksi di bursa jika penutupan dipicu oleh Sinyal/Manual (selain SL itu sendiri)
            if reason != 'STOP_LOSS':
                await self._cancel_position_protection(engine_name, symbol)
            
            if not actual_exit_price:
                actual_exit_price = self.data_stream.latest_prices.get(symbol, exit_price)

            logger.info(f"[{engine_name}] Position {symbol} close request sent. Reason: {reason}")
            return True
                
        return await self._finalize_position_close(engine_name, symbol, actual_exit_price, reason)

    async def _finalize_position_close(self, engine_name: str, symbol: str, exit_price: float, reason: str) -> bool:
        """Finalize state and history once a closure is confirmed (Live or Test)."""
        pos = self.state_manager.get_position(engine_name, symbol)
        if not pos: return False
        
        ctx = self.contexts.get(engine_name)
        if not ctx: return False
        
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

    async def get_daily_pnl_history(self, engine_name: str) -> list:
        """Retrieve aggregated daily PnL history for the chart."""
        ctx = self.contexts.get(engine_name)
        if not ctx: return []
        return await self.state_manager.get_daily_pnl_history(engine_name, ctx.mode)

    def reset_engine_history(self, engine_name: str):
        """Reset trade history stats and clear DB for the current mode."""
        self.state_manager.reset_history(engine_name)

    def reset_engine_balance(self, engine_name: str, initial_equity: float):
        """Reset the test balance back to the initial starting equity."""
        self.state_manager.reset_balance(engine_name, initial_equity)

    async def _handle_private_data(self, data: dict, event_type: str):
        """Unified entry point for private WebSocket events (Orders & Balance)."""
        if event_type == 'order':
            await self._handle_private_order_update(data)
        elif event_type == 'balance':
            await self._handle_private_balance_update(data)

    async def _handle_private_order_update(self, order: dict):
        """Processes real-time order status updates from CCXT Pro."""
        order_id = order.get('id')
        symbol = order.get('symbol')
        status = order.get('status')
        
        if not order_id or not symbol: return

        # Identify which engine owns this order
        target_engine = None
        order_type = None # 'PENDING', 'PROTECTION', 'CLOSING'
        
        for name, state in self.state_manager.state.items():
            # 1. Check Pending Orders
            if order_id in state.get('pending_orders', {}):
                target_engine = name
                order_type = 'PENDING'
                break
            
            # 2. Check Active Positions for SL/TP
            pos = state.get('positions', {}).get(symbol)
            if pos:
                if order_id in [pos.get('sl_order_id'), pos.get('tp_order_id')]:
                    target_engine = name
                    order_type = 'PROTECTION'
                    break
                    
                # 3. Check if it's a manual/signal closure (we don't track the ID explicitly yet, 
                # but we can infer if the side is opposite to position side)
                pos_side = pos.get('side') # LONG or SHORT
                order_side = 'BUY' if order.get('side') == 'buy' else 'SELL'
                if (pos_side == 'LONG' and order_side == 'SELL') or (pos_side == 'SHORT' and order_side == 'BUY'):
                    target_engine = name
                    order_type = 'CLOSING'
                    break

        if not target_engine:
            return

        if status == 'closed':
            logger.info(f"[{target_engine}] Order FILLED real-time: {symbol} (Type: {order_type}, ID: {order_id})")
            if order_type == 'PENDING':
                order_info = self.state_manager.state[target_engine]['pending_orders'][order_id]
                await self._handle_order_fill(target_engine, order_id, order_info, order)
            elif order_type == 'PROTECTION':
                await self._handle_protection_fill(target_engine, symbol, order)
            elif order_type == 'CLOSING':
                # Process closure finalization
                price = order.get('average') or order.get('price')
                # The exact reason could be signaled by previous close_position call, 
                # but for simplicity we use SIGNAL_CLOSE or analyze order comments if available.
                await self._finalize_position_close(target_engine, symbol, price, "SIGNAL_CLOSE")
                
        elif status in ['canceled', 'cancelled', 'expired', 'rejected']:
            if order_type == 'PENDING':
                order_info = self.state_manager.state[target_engine]['pending_orders'][order_id]
                await self._handle_order_cancel(target_engine, order_id, order_info, status.upper())

    async def _handle_private_balance_update(self, balance: dict):
        """Processes real-time balance updates to keep equity in sync."""
        # CCXT balance structure contains free/used/total
        # We look for USDT for simplicity as it's our quote currency
        if 'USDT' in balance:
            usdt_total = balance['USDT'].get('total')
            if usdt_total is not None:
                # Update all engines running in LIVE mode on the same market
                # In a multi-account setup, we'd need to filter by account, 
                # but here we assume one account per client type (Spot/Futures).
                for name, state in self.state_manager.state.items():
                    ctx = self.contexts.get(name)
                    if ctx and ctx.mode == 'LIVE':
                        # We update based on total to account for margin/used funds
                        self.state_manager.update_equity(name, float(usdt_total))
                        # logger.debug(f"[{name}] Equity synchronized via WebSocket: ${float(usdt_total):,.2f}")

    # --- Private Background Tasks ---

    async def _handle_order_fill(self, engine_name: str, order_id: str, order_info: dict, exchange_order: dict):
        """Finalize order execution once it is confirmed FILLED by the exchange."""
        symbol = order_info['symbol']
        side = order_info['side']
        amount = order_info['amount']
        sl = order_info['sl']
        tp = order_info['tp']
        
        # Resolve actual execution price
        price = exchange_order.get('average') or exchange_order.get('price') or order_info['price']
        
        ctx = self.contexts.get(engine_name)
        if not ctx: return

        # EQUITY LOGIC: Spot vs Futures
        if ctx.market.lower() != 'futures':
            current_eq = self.state_manager.get_equity(engine_name)
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
        
        # UI & Memory swap
        self.state_manager.remove_pending_order(engine_name, order_id)
        
        # ATTACH EXCHANGE PROTECTION (LIVE MODE ONLY)
        if ctx.mode == 'LIVE':
            try:
                if ctx.market.lower() == 'futures':
                    # Futures Protection (Stop Market)
                    side_to_close = 'SELL' if pos_side == 'LONG' else 'BUY'
                    stop_type = 'down' if pos_side == 'LONG' else 'up'
                    leverage = getattr(self.engines[engine_name], 'leverage', 1)
                    
                    if sl > 0:
                        order_sl = await order_manager.execute_conditional_order(symbol, side_to_close, amount, sl, ctx.market, stop_type, leverage)
                        if order_sl: pos_data['sl_order_id'] = order_sl.get('id')
                    
                    # Optional: Take Profit for Futures via another stop order
                    if tp > 0:
                        tp_type = 'up' if pos_side == 'LONG' else 'down'
                        order_tp = await order_manager.execute_conditional_order(symbol, side_to_close, amount, tp, ctx.market, tp_type, leverage)
                        if order_tp: pos_data['tp_order_id'] = order_tp.get('id')
                else:
                    # Spot Protection (OCO)
                    if sl > 0 and tp > 0:
                        oco_res = await order_manager.execute_oco_order(symbol, 'SELL', amount, tp, sl, ctx.market)
                        if oco_res:
                            # OCO ID serves for both SL and TP monitoring
                            pos_data['sl_order_id'] = oco_res.get('orderId')
                            pos_data['tp_order_id'] = oco_res.get('orderId')
            except Exception as e:
                logger.error(f"Critical: Failed to attach exchange protection for {symbol}: {e}")

        self.state_manager.add_position(engine_name, symbol, pos_data, pos_side)
        
        self.report_status(engine_name, symbol, "SYNC", f"FILLED {pos_side} {amount:.4f} @ ${price:,.2f}")
        logger.info(f"[{engine_name}] Order {order_id} filled. Position active for {symbol}.")

    async def _handle_order_cancel(self, engine_name: str, order_id: str, order_info: dict, status: str):
        """Cleanup pending order if it was canceled on the exchange."""
        symbol = order_info['symbol']
        self.state_manager.remove_pending_order(engine_name, order_id)
        self.report_status(engine_name, symbol, "SYNC", f"Order {status}")
        logger.warning(f"[{engine_name}] Pending order {order_id} for {symbol} was {status}.")

    async def _cancel_position_protection(self, engine_name: str, symbol: str):
        """Cancel any outstanding exchange-side SL/TP orders for a position."""
        pos = self.state_manager.get_position(engine_name, symbol)
        if not pos: return
        
        ctx = self.contexts.get(engine_name)
        if not ctx or ctx.mode != 'LIVE': return
        
        # Spot OCO uses the same ID for both parts usually, or we catch both
        sl_id = pos.get('sl_order_id')
        tp_id = pos.get('tp_order_id')
        
        if sl_id:
            await order_manager.cancel_order(sl_id, symbol, ctx.market)
        if tp_id and tp_id != sl_id:
            await order_manager.cancel_order(tp_id, symbol, ctx.market)

    async def _handle_protection_fill(self, engine_name: str, symbol: str, exchange_order: dict):
        """Finalize position closure when an exchange-side protection order triggers."""
        price = exchange_order.get('average') or exchange_order.get('price')
        logger.info(f"[{engine_name}] Protection trigger confirmed for {symbol} @ ${price:,.2f}")
        
        # We call the normal close_position but with 'STOP_LOSS' reason to avoid re-cancelling protection
        await self.close_position(engine_name, symbol, price, "STOP_LOSS")

# Global singleton
kernel = Kernel()
