import asyncio
from typing import Dict, Any, Optional
import time
from core.logger import logger
from datetime import datetime, timedelta

class StateManager:
    """
    Manages active positions, equity, baselines, and order history strictly in memory.
    Executes all DB operations asynchronously in a background queue (db_queue).
    """
    def __init__(self):
        self.state: Dict[str, Dict[str, Any]] = {}
        self.db_queue = asyncio.Queue()
        self._sync_task = None

    def start_sync_loop(self):
        if not self._sync_task:
            self._sync_task = asyncio.create_task(self._db_sync_worker())
            logger.info("StateManager DB Sync worker started.")

    async def stop_sync_loop(self):
        if self._sync_task:
            logger.info("StateManager: Stopping DB Sync worker gracefully...")
            try:
                # Wait for current queue to finish with timeout
                await asyncio.wait_for(self.db_queue.join(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("StateManager: Queue join timed out. Remaining tasks will be flushed.")
            
            # Signal worker to stop
            self.db_queue.put_nowait(('STOP',))
            
            try:
                # Wait for worker to finish its last task
                await asyncio.wait_for(self._sync_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._sync_task.cancel()
                try:
                    await self._sync_task
                except asyncio.CancelledError:
                    pass
            
            self._sync_task = None
            logger.info("StateManager DB Sync worker stopped.")

    def initialize_engine_state(self, engine_name: str, initial_equity: float, mode: str, market: str):
        if engine_name not in self.state:
            self.state[engine_name] = {
                'equity': initial_equity,
                'initial_equity': initial_equity,
                'mode': mode,
                'market': market,
                'positions': {},
                'pending_orders': {},
                'baselines': {
                    'daily': initial_equity,
                    'weekly': initial_equity,
                    'monthly': initial_equity,
                    'yearly': initial_equity,
                    'last_reset_daily': None,
                    'last_reset_weekly': None,
                    'last_reset_monthly': None,
                    'last_reset_yearly': None
                },
                'stats': {
                    'total_pnl': 0.0,
                    'daily_pnl': 0.0,
                    'weekly_pnl': 0.0,
                    'monthly_pnl': 0.0,
                    'yearly_pnl': 0.0,
                    'trade_count': 0,
                    'win_rate': 0.0,
                    'next_reset_in': '00:00:00'
                },
                'ui': {
                    'phase': 'IDLE',
                    'message': 'Engine initialized',
                    'history_queue': [],
                    'latest_activity': 'Engine ready'
                }
            }

    def _get_reset_boundary(self, timeframe: str) -> datetime:
        now = datetime.now()
        if timeframe == 'daily':
            anchor = now.replace(hour=7, minute=0, second=0, microsecond=0)
            if now < anchor: anchor -= timedelta(days=1)
            return anchor
        elif timeframe == 'weekly':
            days_since_monday = now.weekday()
            anchor = (now - timedelta(days=days_since_monday)).replace(hour=7, minute=0, second=0, microsecond=0)
            if now < anchor: anchor -= timedelta(days=7)
            return anchor
        elif timeframe == 'monthly':
            anchor = now.replace(day=1, hour=7, minute=0, second=0, microsecond=0)
            if now < anchor:
                if now.month == 1: anchor = anchor.replace(year=now.year-1, month=12)
                else: anchor = anchor.replace(month=now.month-1)
            return anchor
        elif timeframe == 'yearly':
            anchor = now.replace(month=1, day=1, hour=7, minute=0, second=0, microsecond=0)
            if now < anchor: anchor = anchor.replace(year=now.year-1)
            return anchor
        return now

    async def load_state_from_db(self, engine_name: str):
        try:
            from core.database import async_session
            from models.trade_history import ActivePosition, EquityBaseline, TradeHistory
            from sqlalchemy.future import select
            from sqlalchemy import func
            
            mode = self.state[engine_name]['mode']
            
            async with async_session() as session:
                # Positions: Filter by engine AND mode
                stmt = select(ActivePosition).where(
                    ActivePosition.engine_name == engine_name,
                    ActivePosition.mode == mode
                )
                result = await session.execute(stmt)
                records = result.scalars().all()
                for r in records:
                    self.state[engine_name]['positions'][r.symbol] = {
                        'entry_price': r.entry_price, 'amount': r.amount, 'side': r.side,
                        'stop_loss': r.stop_loss, 'take_profit': r.take_profit,
                        'sl_order_id': r.sl_order_id, 'tp_order_id': r.tp_order_id,
                        'max_pnl': r.max_pnl, 'min_pnl': r.min_pnl
                    }
                
                # Baselines: Filter by engine AND mode
                stmt = select(EquityBaseline).where(
                    EquityBaseline.engine_name == engine_name,
                    EquityBaseline.mode == mode
                )
                result = await session.execute(stmt)
                record = result.scalars().first()
                if record:
                    if record.current_equity is not None:
                        self.state[engine_name]['equity'] = record.current_equity
                    
                    self.state[engine_name]['baselines'].update({
                        'daily': record.daily, 'weekly': record.weekly,
                        'monthly': record.monthly, 'yearly': record.yearly,
                        'last_reset_daily': record.last_reset_daily,
                        'last_reset_weekly': record.last_reset_weekly,
                        'last_reset_monthly': record.last_reset_monthly,
                        'last_reset_yearly': record.last_reset_yearly
                    })
                    
                # Historical Stats: Filter by engine AND mode
                stmt = (select(func.sum(TradeHistory.pnl).label("total_pnl"), func.count(TradeHistory.id).label("trade_count"))
                        .where(
                            TradeHistory.engine_name == engine_name,
                            TradeHistory.mode == mode
                        ))
                result = await session.execute(stmt)
                row = result.first()
                total_pnl = float(row.total_pnl) if row and row.total_pnl is not None else 0.0
                trade_count = int(row.trade_count) if row and row.trade_count is not None else 0
                
                win_stmt = select(func.count(TradeHistory.id)).where(
                    TradeHistory.engine_name == engine_name, 
                    TradeHistory.mode == mode,
                    TradeHistory.pnl > 0
                )
                win_res = await session.execute(win_stmt)
                wins = win_res.scalar() or 0
                win_rate = (wins / trade_count * 100) if trade_count > 0 else 0.0
                
                self.state[engine_name]['stats'].update({
                    'total_pnl': total_pnl,
                    'trade_count': trade_count,
                    'win_rate': win_rate
                })

                # Fetch recent trade history for UI: Filter by engine AND mode
                hist_stmt = (select(TradeHistory)
                             .where(
                                 TradeHistory.engine_name == engine_name,
                                 TradeHistory.mode == mode
                             )
                             .order_by(TradeHistory.id.desc())
                             .limit(50))
                hist_res = await session.execute(hist_stmt)
                recent_trades = hist_res.scalars().all()
                
                # We want latest at the top, but history table might need them in specific order
                # The UI appends to table, so we pass them in reverse (oldest to newest) 
                # to populate correctly.
                for t in reversed(recent_trades):
                    self.state[engine_name]['ui']['history_queue'].append({
                        'symbol': t.symbol, 'side': t.side, 'amount': t.amount,
                        'entry': t.entry_price, 'exit': t.exit_price, 'pnl': t.pnl,
                        'max_pnl': t.max_pnl, 'min_pnl': t.min_pnl, 'reason': t.reason,
                        'time': t.time
                    })
        except Exception as e:
            logger.error(f"Failed to load DB state for {engine_name}: {e}")

    def get_equity(self, engine_name: str) -> float:
        return self.state.get(engine_name, {}).get('equity', 0.0)
        
    def get_baseline(self, engine_name: str, timeframe: str) -> float:
        return self.state.get(engine_name, {}).get('baselines', {}).get(timeframe, 0.0)

    def update_equity(self, engine_name: str, new_equity: float):
        if engine_name in self.state:
            self.state[engine_name]['equity'] = new_equity
            self._check_and_update_baselines(engine_name, new_equity)
            self.db_queue.put_nowait(('save_equity', engine_name, new_equity, self.state[engine_name]['baselines'].copy()))

    def _check_and_update_baselines(self, engine_name: str, current_equity: float):
        baselines = self.state[engine_name]['baselines']
        now = datetime.now()
        updated = False
        
        for tf in ['daily', 'weekly', 'monthly', 'yearly']:
            boundary = self._get_reset_boundary(tf)
            last_reset = baselines[f'last_reset_{tf}']
            
            if baselines[tf] is None or (last_reset and last_reset < boundary) or last_reset is None:
                baselines[tf] = current_equity
                baselines[f'last_reset_{tf}'] = now
                updated = True
                logger.info(f"[{engine_name}] {tf.capitalize()} baseline reset: ${baselines[tf]:,.2f}")

    def get_position(self, engine_name: str, symbol: str) -> Optional[dict]:
        return self.state.get(engine_name, {}).get('positions', {}).get(symbol)

    def get_all_positions(self, engine_name: str) -> Dict[str, dict]:
        return self.state.get(engine_name, {}).get('positions', {})

    def add_position(self, engine_name: str, symbol: str, pos_data: dict, side: str):
        if engine_name in self.state:
            pos_data['side'] = side
            self.state[engine_name]['positions'][symbol] = pos_data
            self.db_queue.put_nowait(('save_position', engine_name, symbol, pos_data, side))

    def add_pending_order(self, engine_name: str, order_id: str, order_data: dict):
        if engine_name in self.state:
            self.state[engine_name]['pending_orders'][order_id] = order_data

    def remove_pending_order(self, engine_name: str, order_id: str):
        if engine_name in self.state and order_id in self.state[engine_name]['pending_orders']:
            del self.state[engine_name]['pending_orders'][order_id]

    def remove_position(self, engine_name: str, symbol: str):
        if engine_name in self.state and symbol in self.state[engine_name]['positions']:
            del self.state[engine_name]['positions'][symbol]
            self.db_queue.put_nowait(('remove_position', engine_name, symbol))

    def clear_engine_state(self, engine_name: str):
        """Completely remove an engine's state from memory."""
        if engine_name in self.state:
            del self.state[engine_name]
            logger.info(f"StateManager: State for '{engine_name}' cleared from memory.")

    def reset_history(self, engine_name: str):
        """Reset trade history stats in memory and enqueue DB deletion."""
        if engine_name in self.state:
            s = self.state[engine_name]
            s['stats'].update({
                'total_pnl': 0.0,
                'trade_count': 0,
                'win_rate': 0.0
            })
            s['ui']['history_queue'].clear()
            mode = s['mode']
            self.db_queue.put_nowait(('reset_history', engine_name, mode))
            self.update_ui_status(engine_name, "SYSTEM", "SYNC", f"History reset for {mode} mode.")

    def reset_balance(self, engine_name: str, new_balance: float):
        """Reset balance in memory and enqueue DB update (TEST mode only)."""
        if engine_name in self.state:
            s = self.state[engine_name]
            if s['mode'] != 'TEST':
                return
            s['equity'] = new_balance
            s['initial_equity'] = new_balance
            now = datetime.now()
            for tf in ['daily', 'weekly', 'monthly', 'yearly']:
                s['baselines'][tf] = new_balance
                s['baselines'][f'last_reset_{tf}'] = now
            
            mode = s['mode']
            self.db_queue.put_nowait(('save_equity', engine_name, new_balance, s['baselines'].copy()))
            self.update_ui_status(engine_name, "SYSTEM", "SYNC", f"Balance reset to ${new_balance:,.2f}.")

    def record_history(self, engine_name: str, record: dict):
        if engine_name in self.state:
            s = self.state[engine_name]
            record['market'] = s['market']
            record['mode'] = s['mode']
            
            # Add to UI queue for real-time display
            s['ui']['history_queue'].append(record)
            
            self.db_queue.put_nowait(('save_history', engine_name, record))

    def get_ui_history(self, engine_name: str) -> list:
        """Fetch and clear the history queue for the UI."""
        if engine_name in self.state:
            queue = self.state[engine_name]['ui']['history_queue']
            self.state[engine_name]['ui']['history_queue'] = []
            return queue
        return []

    async def get_daily_pnl_history(self, engine_name: str, mode: str) -> list:
        """Retrieve aggregated daily PnL history for the chart."""
        try:
            from core.database import async_session
            from models.trade_history import TradeHistory
            from sqlalchemy.future import select
            from sqlalchemy import func
            
            async with async_session() as session:
                # Group by date part of created_at
                stmt = (
                    select(
                        func.date(TradeHistory.created_at).label("date"),
                        func.sum(TradeHistory.pnl).label("daily_pnl")
                    )
                    .where(
                        TradeHistory.engine_name == engine_name,
                        TradeHistory.mode == mode
                    )
                    .group_by("date")
                    .order_by("date")
                )
                
                result = await session.execute(stmt)
                rows = result.all()
                
                history = []
                for row in rows:
                    # Convert date string/object to a readable format: "Sat - 11 Apr"
                    dt = datetime.strptime(row.date, "%Y-%m-%d")
                    formatted_date = dt.strftime("%a - %d %b")
                    history.append({
                        "date": formatted_date,
                        "pnl": float(row.daily_pnl)
                    })
                return history
        except Exception as e:
            logger.error(f"Failed to fetch daily PnL history: {e}")
            return []

    def update_ui_status(self, engine_name: str, symbol: str, phase: str, message: str):
        if engine_name in self.state:
            ui = self.state[engine_name]['ui']
            ui['phase'] = phase
            ui['message'] = message
            
            phase_map = {
                "SCAN": ("🔍", "yellow"), "DATA": ("📥", "cyan"), "ANALYZE": ("📊", "magenta"),
                "RISK": ("🛡️", "orange3"), "EXEC": ("🚀", "bold green"), "SYNC": ("✅", "dim"), "IDLE": ("⏳", "dim")
            }
            icon, color = phase_map.get(phase, ("ℹ", "white"))
            if phase == "SCAN": ui['latest_activity'] = f"{symbol:10} {message}"
            else: ui['latest_activity'] = f"[{phase:8}] {symbol:10} {message}"

    def get_ui_state(self, engine_name: str, latest_prices: dict = None) -> dict:
        if engine_name not in self.state: return {}
        s = self.state[engine_name]
        
        # Ensure baselines are updated even if no trades occur
        self._check_and_update_baselines(engine_name, s['equity'])
        
        # Calculate PnL and Equity based on Market Type
        is_futures = (s['market'].lower() == 'futures')
        total_equity = s['equity']
        floating_pnl = 0.0
        if latest_prices is None: latest_prices = {}
        
        for sym, pos in s['positions'].items():
            current_price = latest_prices.get(sym, pos['entry_price'])
            
            # Calculate PnL
            if pos['side'] == 'LONG':
                pnl = (current_price - pos['entry_price']) * pos['amount']
            else: # SHORT
                pnl = (pos['entry_price'] - current_price) * pos['amount']
            
            floating_pnl += pnl
            
            # Equity calculation differs
            if not is_futures:
                # SPOT: Cash + Value of Holdings
                total_equity += (current_price * pos['amount'])
            # FUTURES: Wallet Balance + Floating PnL (Wallet Balance already updated on entry)
        
        if is_futures:
            total_equity += floating_pnl

        total_pnl = s['stats']['total_pnl'] + floating_pnl
        b = s['baselines']
        
        next_reset = self._get_reset_boundary('daily') + timedelta(days=1)
        time_until_reset = next_reset - datetime.now()
        hours, remainder = divmod(time_until_reset.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        return {
            'equity': total_equity, 
            'initial_equity': s['initial_equity'], 
            'total_pnl': total_pnl,
            'daily_pnl': total_equity - (b['daily'] or total_equity),
            'weekly_pnl': total_equity - (b['weekly'] or total_equity),
            'monthly_pnl': total_equity - (b['monthly'] or total_equity),
            'yearly_pnl': total_equity - (b['yearly'] or total_equity),
            'next_reset_in': f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            'trade_count': s['stats']['trade_count'], 
            'win_rate': s['stats']['win_rate'],
            'active_pos_count': len(s['positions']),
            'pending_order_count': len(s['pending_orders']),
            'mode': s['mode'],
            'current_phase': s['ui']['phase'], 
            'status_message': s['ui']['message'], 
            'latest_activity': s['ui']['latest_activity']
        }
        
    def get_ui_orders(self, engine_name: str, latest_prices: dict) -> list:
        if engine_name not in self.state: return []
        results = []
        for symbol, pos in self.state[engine_name]['positions'].items():
            current_price = latest_prices.get(symbol, pos['entry_price'])
            pnl_val = (current_price - pos['entry_price']) * pos['amount'] if pos['side'] == 'LONG' else (pos['entry_price'] - current_price) * pos['amount']
            results.append({
                'order_id': f"{symbol}_{pos['side']}", 'symbol': symbol, 'side': pos['side'],
                'size': pos['amount'], 'entry': pos['entry_price'], 'current': current_price,
                'sl': pos.get('stop_loss', 0.0), 'tp': pos.get('take_profit', 0.0), 'pnl': pnl_val
            })
        return results

    async def _db_sync_worker(self):
        from core.database import async_session
        from models.trade_history import TradeHistory, ActivePosition, EquityBaseline
        from sqlalchemy.future import select
        from sqlalchemy import delete
        while True:
            try:
                task = await self.db_queue.get()
                action = task[0]
                
                if action == 'STOP':
                    self.db_queue.task_done()
                    break
                    
                async with async_session() as session:
                    if action == 'save_equity':
                        _, eng_name, eq, bl = task
                        mode = self.state[eng_name]['mode'] if eng_name in self.state else 'TEST'
                        
                        stmt = select(EquityBaseline).where(
                            EquityBaseline.engine_name == eng_name,
                            EquityBaseline.mode == mode
                        )
                        res = await session.execute(stmt)
                        rec = res.scalars().first()
                        if rec:
                            rec.current_equity = eq
                            rec.daily = bl['daily']
                            rec.weekly = bl['weekly']
                            rec.monthly = bl['monthly']
                            rec.yearly = bl['yearly']
                            rec.last_reset_daily = bl['last_reset_daily']
                            rec.last_reset_weekly = bl['last_reset_weekly']
                            rec.last_reset_monthly = bl['last_reset_monthly']
                            rec.last_reset_yearly = bl['last_reset_yearly']
                        else:
                            rec = EquityBaseline(
                                engine_name=eng_name, mode=mode, current_equity=eq, 
                                daily=bl['daily'], weekly=bl['weekly'],
                                monthly=bl['monthly'], yearly=bl['yearly'], 
                                last_reset_daily=bl['last_reset_daily'],
                                last_reset_weekly=bl['last_reset_weekly'], 
                                last_reset_monthly=bl['last_reset_monthly'],
                                last_reset_yearly=bl['last_reset_yearly']
                            )
                            session.add(rec)
                        await session.commit()
                        
                    elif action == 'save_position':
                        _, eng_name, sym, pos, side = task
                        mode = self.state[eng_name]['mode'] if eng_name in self.state else 'TEST'
                        
                        stmt = select(ActivePosition).where(
                            ActivePosition.engine_name == eng_name, 
                            ActivePosition.symbol == sym,
                            ActivePosition.mode == mode
                        )
                        res = await session.execute(stmt)
                        rec = res.scalars().first()
                        if rec:
                            rec.amount = pos['amount']; rec.entry_price = pos['entry_price']
                            rec.stop_loss = pos.get('stop_loss', 0.0); rec.take_profit = pos.get('take_profit', 0.0)
                            rec.sl_order_id = pos.get('sl_order_id'); rec.tp_order_id = pos.get('tp_order_id')
                            rec.max_pnl = pos.get('max_pnl', 0.0); rec.min_pnl = pos.get('min_pnl', 0.0)
                        else:
                            rec = ActivePosition(
                                engine_name=eng_name, symbol=sym, side=side, mode=mode,
                                amount=pos['amount'], entry_price=pos['entry_price'], 
                                stop_loss=pos.get('stop_loss', 0.0), take_profit=pos.get('take_profit', 0.0),
                                sl_order_id=pos.get('sl_order_id'), tp_order_id=pos.get('tp_order_id')
                            )
                            session.add(rec)
                        await session.commit()
                        
                    elif action == 'remove_position':
                        _, eng_name, sym = task
                        mode = self.state[eng_name]['mode'] if eng_name in self.state else 'TEST'
                        
                        stmt = select(ActivePosition).where(
                            ActivePosition.engine_name == eng_name, 
                            ActivePosition.symbol == sym,
                            ActivePosition.mode == mode
                        )
                        res = await session.execute(stmt)
                        rec = res.scalars().first()
                        if rec:
                            await session.delete(rec)
                            await session.commit()
                            
                    elif action == 'save_history':
                        _, eng_name, rec_data = task
                        rec = TradeHistory(
                            engine_name=eng_name, market=rec_data.get('market', 'Spot'), mode=rec_data.get('mode', 'TEST'),
                            symbol=rec_data.get('symbol'), side=rec_data.get('side'), amount=rec_data.get('amount'),
                            entry_price=rec_data.get('entry'), exit_price=rec_data.get('exit'), pnl=rec_data.get('pnl'),
                            max_pnl=rec_data.get('max_pnl', 0.0), min_pnl=rec_data.get('min_pnl', 0.0),
                            reason=rec_data.get('reason'), time=rec_data.get('time')
                        )
                        session.add(rec)
                        await session.commit()

                    elif action == 'reset_history':
                        _, eng_name, mode = task
                        stmt = delete(TradeHistory).where(
                            TradeHistory.engine_name == eng_name,
                            TradeHistory.mode == mode
                        )
                        await session.execute(stmt)
                        await session.commit()
                self.db_queue.task_done()
            except asyncio.CancelledError: break
            except Exception as e:
                logger.error(f"DB Sync Worker Error: {e}")
                await asyncio.sleep(1)
