import pandas as pd
import numpy as np

class Indicators:
    @staticmethod
    def calculate_sma(df: pd.DataFrame, period: int = 20, column: str = 'close') -> pd.Series:
        return df[column].rolling(window=period).mean()

    @staticmethod
    def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: int = 2, column: str = 'close'):
        sma = Indicators.calculate_sma(df, period, column)
        std = df[column].rolling(window=period).std(ddof=0)
        upper_band = sma + (std * std_dev)
        lower_band = sma - (std * std_dev)
        return sma, upper_band, lower_band

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14, column: str = 'close') -> pd.Series:
        delta = df[column].diff()
        gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
        loss = -delta.clip(upper=0).ewm(alpha=1/period, min_periods=period).mean()
        
        # Avoid division by zero
        loss = loss.replace(0, 1e-10)
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        
        atr = true_range.ewm(alpha=1/period, min_periods=period).mean()
        return atr

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        up_move = df['high'] - df['high'].shift(1)
        down_move = df['low'].shift(1) - df['low']
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        plus_dm_series = pd.Series(plus_dm, index=df.index)
        minus_dm_series = pd.Series(minus_dm, index=df.index)
        
        atr = Indicators.calculate_atr(df, period)
        
        plus_di = 100 * (plus_dm_series.ewm(alpha=1/period, min_periods=period).mean() / atr)
        minus_di = 100 * (minus_dm_series.ewm(alpha=1/period, min_periods=period).mean() / atr)
        
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/period, min_periods=period).mean()
        
        return adx

    @staticmethod
    def attach_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Helper to compute and attach all necessary indicators to the DataFrame."""
        if df.empty or len(df) < 20: # Minimum required for BB and ADX
            return df
            
        df = df.copy()
        df['sma_20'], df['bb_upper'], df['bb_lower'] = Indicators.calculate_bollinger_bands(df, period=20)
        df['rsi_14'] = Indicators.calculate_rsi(df, period=14)
        df['atr_14'] = Indicators.calculate_atr(df, period=14)
        df['adx_14'] = Indicators.calculate_adx(df, period=14)
        return df
