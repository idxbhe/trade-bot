import asyncio
import ccxt.pro as ccxt
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

    async def fetch_open_orders(self, symbol: str = None):
        return await self._safe_call(self.exchange.fetch_open_orders, symbol)

    async def fetch_order(self, order_id: str, symbol: str):
        return await self._safe_call(self.exchange.fetch_order, order_id, symbol)

    async def fetch_balance(self) -> float:
        """Fetch available USDT balance from Trade account."""
        try:
            # KuCoin Spot: Funds must be in 'trade' account for the bot to use them
            balance = await self._safe_call(self.exchange.fetch_balance, params={'type': 'trade'})
            if balance and 'USDT' in balance:
                return float(balance['USDT'].get('free', 0.0))
            
            # If balance call succeeded but USDT isn't there, it means you have 0 USDT
            if balance is not None:
                return 0.0
        except Exception as e:
            logger.error(f"Error fetching Spot balance: {e}")
        return 0.0

    async def fetch_active_positions(self) -> list:
        """
        Spot Reconcile: Scan trade account for non-USDT assets.
        Returns standardized list of positions.
        """
        try:
            balance = await self._safe_call(self.exchange.fetch_balance, params={'type': 'trade'})
            if not balance: return []
            
            positions = []
            for asset, data in balance.items():
                if asset in ['USDT', 'info', 'timestamp', 'datetime', 'free', 'used', 'total']:
                    continue
                
                total = float(data.get('total', 0.0))
                if total > 0.00000001: # Threshold to ignore dust
                    symbol = f"{asset}/USDT"
                    
                    # Try to find entry price from last trade
                    entry_price = 0.0
                    trades = await self._safe_call(self.exchange.fetch_my_trades, symbol, limit=5)
                    if trades:
                        # Find latest BUY trade
                        buy_trades = [t for t in trades if t['side'] == 'buy']
                        if buy_trades:
                            entry_price = float(buy_trades[-1]['price'])
                    
                    # Fallback to ticker if no trade found
                    if entry_price == 0:
                        ticker = await self.fetch_ticker(symbol)
                        if ticker: entry_price = float(ticker['last'])

                    positions.append({
                        'symbol': symbol,
                        'amount': total,
                        'entry_price': entry_price,
                        'side': 'LONG'
                    })
            return positions
        except Exception as e:
            logger.error(f"Error during Spot state reconciliation: {e}")
            return []

    async def load_markets(self):
        if not self.exchange.markets:
            await self._safe_call(self.exchange.load_markets)

    async def get_market_limits(self, symbol: str):
        await self.load_markets()
        if self.exchange.markets and symbol in self.exchange.markets:
            market = self.exchange.markets[symbol]
            return {
                'precision': market.get('precision', {}),
                'limits': market.get('limits', {}),
                'contractSize': market.get('contractSize', 1.0)
            }
        return None

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

    async def fetch_balance(self) -> float:
        """Fetch base wallet balance from Futures account (excluding Unrealized PnL)."""
        balance = await self._safe_call(self.exchange.fetch_balance)
        if balance and 'USDT' in balance:
            # Akses raw API info dari bursa untuk akurasi tingkat tinggi.
            # Di KuCoin, 'marginBalance' adalah saldo dompet murni (Wallet Balance)
            # yang belum ditambah dengan Unrealized PnL.
            info = balance.get('info', {})
            if isinstance(info, dict) and 'marginBalance' in info:
                return float(info['marginBalance'])
            
            # Fallback jika dictionary info gagal diurai oleh CCXT
            return float(balance['USDT'].get('free', 0.0)) + float(balance['USDT'].get('used', 0.0))
        return 0.0

    async def fetch_active_positions(self) -> list:
        """Futures Reconcile: Calls fetch_positions and returns standardized list."""
        try:
            await self.load_markets()
            raw_positions = await self._safe_call(self.exchange.fetch_positions)
            if not raw_positions: return []
            
            positions = []
            for p in raw_positions:
                contracts = abs(float(p.get('contracts', 0.0)))
                if contracts > 0:
                    symbol = p.get('symbol')
                    contract_size = self.exchange.markets.get(symbol, {}).get('contractSize', 1.0)
                    positions.append({
                        'symbol': symbol,
                        'amount': contracts * contract_size,  # Konversi ke nominal base currency
                        'entry_price': float(p.get('entryPrice', 0.0)),
                        'side': p.get('side', '').upper() # 'LONG' or 'SHORT'
                    })
            return positions
        except Exception as e:
            logger.error(f"Error during Futures state reconciliation: {e}")
            return []

kucoin_client = KuCoinClient()
kucoin_futures_client = KuCoinFuturesClient()
