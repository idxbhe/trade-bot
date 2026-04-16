import asyncio
import pandas as pd
from core.logger import logger
from exchange.kucoin import kucoin_client

class MarketDataCollector:
    def __init__(self):
        self.client = kucoin_client
        self.min_volume_24h = 10_000_000  # $10 million
        self.max_spread_pct = 0.0015      # 0.15%
        self.min_depth_usd = 100_000      # $100k depth within 2%

    async def filter_market(self, symbol: str) -> bool:
        """
        Check if a symbol meets the liquidity, spread, and depth requirements.
        Returns True if it passes all filters.
        """
        logger.info(f"Filtering market for {symbol}...")
        
        # 1. Fetch Ticker for Volume and Spread
        ticker = await self.client.fetch_ticker(symbol)
        if not ticker:
            logger.warning(f"[{symbol}] Failed to fetch ticker.")
            return False
            
        # Get 24h quote volume (in USD assuming USDT pair)
        quote_volume = ticker.get('quoteVolume', 0)
        
        # Calculate Spread: (ask - bid) / ask
        bid = ticker.get('bid', 0)
        ask = ticker.get('ask', 0)
        
        if not bid or not ask or quote_volume == 0:
            logger.warning(f"[{symbol}] Incomplete ticker data.")
            return False
            
        spread_pct = (ask - bid) / ask
        
        logger.info(f"[{symbol}] Volume: ${quote_volume:,.2f}, Spread: {spread_pct*100:.4f}%")
        
        if quote_volume < self.min_volume_24h:
            logger.warning(f"[{symbol}] Failed: Volume ${quote_volume:,.2f} < ${self.min_volume_24h:,.2f}")
            return False
            
        if spread_pct > self.max_spread_pct:
            logger.warning(f"[{symbol}] Failed: Spread {spread_pct*100:.4f}% > {self.max_spread_pct*100:.4f}%")
            return False
            
        # 2. Fetch Order Book for Depth within ±2%
        ob = await self.client.fetch_order_book(symbol)
        if not ob:
            logger.warning(f"[{symbol}] Failed to fetch order book.")
            return False
            
        current_price = (bid + ask) / 2
        upper_bound = current_price * 1.02
        lower_bound = current_price * 0.98
        
        # Calculate Bids depth (buying power below current price down to -2%)
        bids_depth_usd = 0
        for price, amount in ob.get('bids', []):
            if price >= lower_bound:
                bids_depth_usd += price * amount
            else:
                break # Order book is sorted descending for bids
                
        # Calculate Asks depth (selling pressure above current price up to +2%)
        asks_depth_usd = 0
        for price, amount in ob.get('asks', []):
            if price <= upper_bound:
                asks_depth_usd += price * amount
            else:
                break # Order book is sorted ascending for asks
                
        logger.info(f"[{symbol}] Bids Depth (2%): ${bids_depth_usd:,.2f}, Asks Depth (2%): ${asks_depth_usd:,.2f}")
        
        if bids_depth_usd < self.min_depth_usd or asks_depth_usd < self.min_depth_usd:
            logger.warning(f"[{symbol}] Failed: Insufficient order book depth.")
            return False
            
        logger.info(f"[{symbol}] Passed all market filters!")
        return True

    async def fetch_ticker(self, symbol: str) -> dict:
        """Fetch the latest ticker data for a symbol."""
        return await self.client.fetch_ticker(symbol)

    async def get_historical_data(self, symbol: str, timeframe: str = '15m', limit: int = 200) -> pd.DataFrame:
        """
        Fetch OHLCV data and convert to Pandas DataFrame.
        """
        ohlcv = await self.client.fetch_ohlcv(symbol, timeframe, limit)
        if not ohlcv:
            return pd.DataFrame()
            
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df

market_collector = MarketDataCollector()
