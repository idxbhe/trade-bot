import asyncio
import pandas as pd
import numpy as np
from data.indicators import Indicators
from strategy.neutral_grid import NeutralGridStrategy
from strategy.mean_reversion import MeanReversionStrategy
from strategy.volatility_breakout import VolatilityBreakoutStrategy
from strategy.orderbook_imbalance import OrderBookImbalanceStrategy
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

    # Test Volatility Breakout
    logger.info("\n--- Testing Volatility Breakout Strategy ---")
    vb_strat = VolatilityBreakoutStrategy()
    df_vb = df_with_indicators.copy()
    # Ensure vol_sma_20 is present for testing
    df_vb['vol_sma_20'] = Indicators.calculate_sma(df_vb, period=20, column='volume')
    
    # Force a breakout condition
    df_vb.iloc[-1, df_vb.columns.get_loc('close')] = df_vb.iloc[-1]['bb_upper'] + 100
    df_vb.iloc[-1, df_vb.columns.get_loc('volume')] = df_vb.iloc[-1]['vol_sma_20'] + 100
    
    vb_signal = vb_strat.generate_signal(df_vb, df_vb.iloc[-1]['close'])
    logger.info(f"Signal (Forced Breakout): {vb_signal}")

    # Test OrderBook Imbalance
    logger.info("\n--- Testing Order Book Imbalance Strategy ---")
    obi_strat = OrderBookImbalanceStrategy(imbalance_threshold=0.7)
    
    # Simulate a massive buy wall
    bullish_orderbook = {
        'bids': [[50000, 10], [49990, 50], [49980, 40]], # Total 100
        'asks': [[50010, 5], [50020, 10], [50030, 5]]    # Total 20
    }
    
    # Simulate a massive sell wall
    bearish_orderbook = {
        'bids': [[50000, 5], [49990, 10], [49980, 5]],   # Total 20
        'asks': [[50010, 10], [50020, 50], [50030, 40]]  # Total 100
    }
    
    bull_signal = obi_strat.evaluate_orderbook(bullish_orderbook)
    logger.info(f"Signal (Bullish OB): {bull_signal}")
    
    bear_signal = obi_strat.evaluate_orderbook(bearish_orderbook)
    logger.info(f"Signal (Bearish OB): {bear_signal}")

if __name__ == '__main__':
    test_strategies()
