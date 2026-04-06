import pandas as pd
from typing import Dict, List, Any
from core.logger import logger
from strategy.base_strategy import BaseStrategy
from risk.position_sizer import position_sizer

class BacktestEngine:
    """
    Offline Backtesting Engine to simulate strategy performance on historical data.
    Supports slippage and basic fee simulation.
    """
    def __init__(self, initial_capital: float = 10000.0, maker_fee: float = 0.001, slippage: float = 0.001):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.maker_fee = maker_fee
        self.slippage = slippage
        
        self.positions: List[Dict[str, Any]] = []
        self.trade_history: List[Dict[str, Any]] = []
        
    def _apply_slippage_and_fee(self, price: float, is_buy: bool, amount: float) -> float:
        """Calculates actual execution price and deducts fee."""
        # Slippage makes buys more expensive and sells cheaper
        executed_price = price * (1 + self.slippage) if is_buy else price * (1 - self.slippage)
        
        # Calculate fee in quote currency (USDT)
        fee_amount = (executed_price * amount) * self.maker_fee
        self.current_capital -= fee_amount
        
        return executed_price

    def run_backtest(self, df: pd.DataFrame, strategy: BaseStrategy):
        """
        Iterates through historical data row-by-row to simulate trading.
        Note: In a real tick-level backtester, we would use smaller timeframes.
        This is a simplified bar-by-bar backtester.
        """
        logger.info(f"Starting Backtest for Strategy: {strategy.name}")
        logger.info(f"Initial Capital: ${self.initial_capital:,.2f}")
        
        for index, row in df.iterrows():
            current_price = row['close']
            
            # 1. Check existing positions for Stop Loss or Take Profit
            for pos in self.positions[:]: # Iterate copy to allow removal
                if pos['type'] == 'LONG':
                    if current_price <= pos['stop_loss']:
                        self._close_position(pos, current_price, index, 'STOP_LOSS')
                    elif 'target_exit' in pos and current_price >= pos['target_exit']:
                        self._close_position(pos, current_price, index, 'TAKE_PROFIT')
            
            # 2. Generate Signal from Strategy
            # In backtesting, we feed the data up to the current row to prevent look-ahead bias
            historical_slice = df.loc[:index] 
            signal_data = strategy.generate_signal(historical_slice, current_price)
            
            # 3. Execute Signal
            if signal_data['signal'] == 'BUY' and not self.positions:
                # Calculate size using Risk Management
                atr = row.get('atr_14', 0)
                sl_price = position_sizer.calculate_stop_loss(current_price, atr, is_long=True)
                
                size, risk_usd = position_sizer.calculate_position_size(self.current_capital, current_price, sl_price)
                
                if size > 0:
                    executed_price = self._apply_slippage_and_fee(current_price, is_buy=True, amount=size)
                    total_cost = executed_price * size
                    
                    if total_cost <= self.current_capital:
                        self.current_capital -= total_cost
                        
                        new_pos = {
                            'entry_time': index,
                            'entry_price': executed_price,
                            'amount': size,
                            'type': 'LONG',
                            'stop_loss': sl_price,
                            'target_exit': signal_data.get('target_exit')
                        }
                        self.positions.append(new_pos)
                        logger.debug(f"[{index}] BUY Executed @ ${executed_price:.2f} | Size: {size:.4f} | SL: ${sl_price:.2f}")

            elif signal_data['signal'] == 'EXIT_LONG' and self.positions:
                 for pos in self.positions[:]:
                     self._close_position(pos, current_price, index, 'SIGNAL_EXIT')

    def _close_position(self, pos: Dict[str, Any], current_price: float, exit_time, reason: str):
        """Closes an open position and updates capital and history."""
        executed_price = self._apply_slippage_and_fee(current_price, is_buy=False, amount=pos['amount'])
        revenue = executed_price * pos['amount']
        
        self.current_capital += revenue
        self.positions.remove(pos)
        
        pnl = revenue - (pos['entry_price'] * pos['amount'])
        pnl_pct = pnl / (pos['entry_price'] * pos['amount'])
        
        trade_record = {
            'entry_time': pos['entry_time'],
            'exit_time': exit_time,
            'entry_price': pos['entry_price'],
            'exit_price': executed_price,
            'reason': reason,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'capital_after': self.current_capital
        }
        self.trade_history.append(trade_record)
        logger.debug(f"[{exit_time}] {reason} @ ${executed_price:.2f} | PNL: ${pnl:.2f} ({pnl_pct*100:.2f}%)")

    def get_results(self) -> pd.DataFrame:
        """Returns trade history as a DataFrame for analysis."""
        return pd.DataFrame(self.trade_history)

    def print_summary(self):
        """Prints a summary of the backtest results."""
        total_return = self.current_capital - self.initial_capital
        return_pct = (total_return / self.initial_capital) * 100
        
        logger.info("\n=== BACKTEST SUMMARY ===")
        logger.info(f"Initial Capital: ${self.initial_capital:,.2f}")
        logger.info(f"Final Capital:   ${self.current_capital:,.2f}")
        logger.info(f"Net Profit:      ${total_return:,.2f} ({return_pct:.2f}%)")
        
        if self.trade_history:
            df_results = self.get_results()
            win_rate = (df_results['pnl'] > 0).mean() * 100
            total_trades = len(df_results)
            
            logger.info(f"Total Trades:    {total_trades}")
            logger.info(f"Win Rate:        {win_rate:.2f}%")
            logger.info(f"Best Trade:      ${df_results['pnl'].max():,.2f}")
            logger.info(f"Worst Trade:     ${df_results['pnl'].min():,.2f}")
        else:
            logger.info("Total Trades:    0")
        logger.info("========================")
