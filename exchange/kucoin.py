import asyncio
import ccxt.async_support as ccxt
from core.config import config
from core.logger import logger

class KuCoinClient:
    def __init__(self):
        exchange_config = {
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True,
            }
        }
        
        # Inject Optional Proxy (Bypass ISP Block)
        if config.BOT_PROXY_URL and config.BOT_PROXY_URL.strip():
            exchange_config['proxies'] = {
                'http': config.BOT_PROXY_URL.strip(),
                'https': config.BOT_PROXY_URL.strip()
            }
            logger.info(f"Proxy integration enabled. Routing traffic via: {config.BOT_PROXY_URL.strip()}")
            
        # Only add auth credentials if they have been changed from the default template
        if config.KUCOIN_API_KEY and config.KUCOIN_API_KEY != 'your_api_key_here':
            exchange_config.update({
                'apiKey': config.KUCOIN_API_KEY,
                'secret': config.KUCOIN_API_SECRET,
                'password': config.KUCOIN_API_PASSPHRASE,
            })
            
        self.exchange = ccxt.kucoin(exchange_config)
        
        if config.KUCOIN_ENV.lower() == 'sandbox':
            try:
                self.exchange.set_sandbox_mode(True)
                logger.info("KuCoin API initialized in SANDBOX mode.")
            except Exception as e:
                logger.warning(f"KuCoin Sandbox mode not supported: {e}. Falling back to live mode (read-only for now).")
        else:
            logger.info("KuCoin API initialized in LIVE mode. (Proceed with caution)")

    async def _safe_call(self, func, *args, **kwargs):
        """Wrapper to handle Rate Limiting and temporary API errors with retries."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except ccxt.RateLimitExceeded as e:
                wait_time = (attempt + 1) * 2
                logger.warning(f"Rate limit exceeded. Waiting {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(wait_time)
            except ccxt.NetworkError as e:
                wait_time = (attempt + 1) * 1
                logger.warning(f"Network error: {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.error(f"API Error: {e}")
                return None
        return None

    async def fetch_ticker(self, symbol: str):
        return await self._safe_call(self.exchange.fetch_ticker, symbol)

    async def fetch_order_book(self, symbol: str, limit: int = 100):
        return await self._safe_call(self.exchange.fetch_order_book, symbol, limit)

    async def fetch_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 100):
        return await self._safe_call(self.exchange.fetch_ohlcv, symbol, timeframe, limit=limit)

    async def close(self):
        if self.exchange:
            await self.exchange.close()
            logger.info("KuCoin connection closed.")

class KuCoinFuturesClient(KuCoinClient):
    def __init__(self):
        exchange_config = {
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True,
            }
        }
        
        if config.BOT_PROXY_URL and config.BOT_PROXY_URL.strip():
            exchange_config['proxies'] = {
                'http': config.BOT_PROXY_URL.strip(),
                'https': config.BOT_PROXY_URL.strip()
            }
            logger.info(f"Futures Proxy integration enabled. Routing traffic via: {config.BOT_PROXY_URL.strip()}")
            
        if config.KUCOIN_API_KEY and config.KUCOIN_API_KEY != 'your_api_key_here':
            exchange_config.update({
                'apiKey': config.KUCOIN_API_KEY,
                'secret': config.KUCOIN_API_SECRET,
                'password': config.KUCOIN_API_PASSPHRASE,
            })
            
        self.exchange = ccxt.kucoinfutures(exchange_config)
        
        if config.KUCOIN_ENV.lower() == 'sandbox':
            try:
                self.exchange.set_sandbox_mode(True)
                logger.info("KuCoin Futures API initialized in SANDBOX mode.")
            except Exception as e:
                logger.warning(f"KuCoin Futures Sandbox mode not supported: {e}. Falling back to live mode.")
        else:
            logger.info("KuCoin Futures API initialized in LIVE mode. (Proceed with caution)")

kucoin_client = KuCoinClient()
kucoin_futures_client = KuCoinFuturesClient()
