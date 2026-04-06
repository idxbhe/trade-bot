import asyncio
import pandas as pd
import numpy as np
from core.logger import logger
from data.indicators import Indicators
from strategy.mean_reversion import MeanReversionStrategy
from backtest.engine import BacktestEngine

def create_volatile_synthetic_data() -> pd.DataFrame:
    """Create 500 rows of synthetic OHLCV data with distinct mean reversion opportunities"""
    np.random.seed(42)
    dates = pd.date_range(start='2026-01-01', periods=500, freq='15min')
    
    # Base price
    close_prices = np.full(500, 50000.0)
    
    # Inject sudden drops (oversold) followed by rapid recoveries (mean reversion)
    for i in range(1, 500):
        if i % 100 == 0:  # Every 100 candles, simulate a massive drop
            close_prices[i] = close_prices[i-1] * 0.95
        elif i % 100 < 20: # Recovery phase
            close_prices[i] = close_prices[i-1] * 1.005
        else: # Normal noise
            close_prices[i] = close_prices[i-1] + (np.random.randn() * 50)
            
    data = {
        'timestamp': dates,
        'open': close_prices + np.random.randn(500) * 10,
        'high': close_prices + np.abs(np.random.randn(500) * 50),
        'low': close_prices - np.abs(np.random.randn(500) * 50),
        'close': close_prices,
        'volume': np.random.rand(500) * 100
    }
    df = pd.DataFrame(data)
    df.set_index('timestamp', inplace=True)
    return df

def run_simulation():
    logger.info("--- Starting Backtest Simulation (Phase 5) ---")
    
    # 1. Prepare Data
    df = create_volatile_synthetic_data()
    df_with_indicators = Indicators.attach_all_indicators(df)
    
    # 2. Initialize Strategy and Engine
    # Using Maker Fee 0.1% (KuCoin standard) and Slippage 0.1%
    strategy = MeanReversionStrategy()
    engine = BacktestEngine(initial_capital=10000.0, maker_fee=0.001, slippage=0.001)
    
    # 3. Run Engine
    engine.run_backtest(df_with_indicators, strategy)
    
    # 4. Print Results
    engine.print_summary()
    
    results_df = engine.get_results()
    if not results_df.empty:
        logger.info("\nTrade Log (First 5 Trades):")
        # Format for display
        display_df = results_df[['exit_time', 'reason', 'entry_price', 'exit_price', 'pnl', 'capital_after']].head(5)
        logger.info(f"\n{display_df}")

if __name__ == '__main__':
    # Since backtesting is CPU bound and doesn't use network IO, we don't need asyncio here
    run_simulation()
