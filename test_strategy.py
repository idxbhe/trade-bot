import asyncio
import pandas as pd
import numpy as np
from data.indicators import Indicators
from strategy.neutral_grid import NeutralGridStrategy
from strategy.mean_reversion import MeanReversionStrategy
from core.logger import logger

def create_synthetic_data() -> pd.DataFrame:
    """Create 100 rows of synthetic OHLCV data for testing"""
    np.random.seed(42)
    dates = pd.date_range(start='2026-04-05', periods=100, freq='15min')
    
    # Generate a random walk
    close_prices = 50000 + np.cumsum(np.random.randn(100) * 100)
    
    data = {
        'timestamp': dates,
        'open': close_prices + np.random.randn(100) * 10,
        'high': close_prices + np.abs(np.random.randn(100) * 50),
        'low': close_prices - np.abs(np.random.randn(100) * 50),
        'close': close_prices,
        'volume': np.random.rand(100) * 100
    }
    df = pd.DataFrame(data)
    df.set_index('timestamp', inplace=True)
    return df

def test_strategies():
    logger.info("Generating synthetic market data...")
    df = create_synthetic_data()
    
    logger.info("Calculating technical indicators...")
    df_with_indicators = Indicators.attach_all_indicators(df)
    
    logger.info(f"Indicators calculated. Last row sample:\n{df_with_indicators.iloc[-1][['close', 'sma_20', 'rsi_14', 'adx_14', 'atr_14']]}")
    
    current_price = df_with_indicators.iloc[-1]['close']
    
    # Test Neutral Grid
    logger.info("\n--- Testing Neutral Grid Strategy ---")
    grid_strat = NeutralGridStrategy(lower_bound=48000, upper_bound=52000, grids=20)
    grid_signal = grid_strat.generate_signal(df_with_indicators, current_price)
    logger.info(f"Signal: {grid_signal}")
    
    # Test Mean Reversion
    logger.info("\n--- Testing Mean Reversion Strategy ---")
    mr_strat = MeanReversionStrategy()
    
    # Force an oversold condition for testing
    df_mr = df_with_indicators.copy()
    df_mr.iloc[-1, df_mr.columns.get_loc('close')] = df_mr.iloc[-1]['bb_lower'] - 100 # Drop below lower band
    df_mr.iloc[-1, df_mr.columns.get_loc('rsi_14')] = 25 # Force RSI < 30
    
    mr_signal = mr_strat.generate_signal(df_mr, df_mr.iloc[-1]['close'])
    logger.info(f"Signal (Forced Oversold): {mr_signal}")

if __name__ == '__main__':
    test_strategies()
