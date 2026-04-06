from strategy.base_strategy import BaseStrategy
import pandas as pd
from typing import Dict, Any

class MeanReversionStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("Mean_Reversion")

    def generate_signal(self, df: pd.DataFrame, current_price: float) -> Dict[str, Any]:
        if df.empty or 'bb_lower' not in df.columns or 'rsi_14' not in df.columns:
            return {'signal': 'HOLD', 'reason': 'Missing indicators'}
            
        last_row = df.iloc[-1]
        lower_band = last_row['bb_lower']
        middle_band = last_row['sma_20']
        rsi = last_row['rsi_14']
        
        if pd.isna(lower_band) or pd.isna(rsi):
            return {'signal': 'HOLD', 'reason': 'Indicators calculating (NaN)'}
        
        # Entry logic: Price touches/crosses lower band AND RSI < 30 (Oversold)
        if current_price <= lower_band and rsi < 30:
            return {
                'signal': 'BUY',
                'reason': f'Price ({current_price}) <= Lower Band ({lower_band:.2f}) and RSI ({rsi:.2f}) < 30',
                'target_exit': middle_band
            }
            
        # Exit logic: Price returns to middle band (Mean)
        if current_price >= middle_band:
            return {
                'signal': 'EXIT_LONG',
                'reason': f'Price ({current_price}) reached Middle Band ({middle_band:.2f})'
            }
            
        return {'signal': 'HOLD', 'reason': 'No clear mean reversion setup.'}
