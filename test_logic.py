import asyncio
from data.fetcher import market_collector
from data.indicators import Indicators
from strategy.mean_reversion import MeanReversionStrategy

async def test():
    strategy = MeanReversionStrategy()
    df = await market_collector.get_historical_data("BTC/USDT", timeframe='15m', limit=50)
    df_indicators = Indicators.attach_all_indicators(df)
    current_price = df_indicators.iloc[-1]['close']
    signal_data = strategy.generate_signal(df_indicators, current_price)
    print("SUCCESS", signal_data)

asyncio.run(test())
