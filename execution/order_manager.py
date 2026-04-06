from typing import Dict, Any, Optional
from core.logger import logger
from exchange.kucoin import kucoin_client

class OrderManager:
    """
    Translates validated signals into actual exchange API calls.
    Handles 'Post-Only' limits to enforce Maker fees.
    """
    def __init__(self):
        self.client = kucoin_client

    async def execute_limit_order(self, symbol: str, side: str, amount: float, price: float, post_only: bool = True) -> Optional[Dict[str, Any]]:
        """
        Executes a Limit Order on KuCoin.
        post_only=True ensures the order goes to the order book (Maker fee). If it would match immediately, it's cancelled.
        """
        logger.info(f"Attempting to place {side.upper()} order for {amount:.4f} {symbol} @ ${price:,.2f} (Post-Only: {post_only})")
        
        try:
            params = {'postOnly': post_only}
            # CCXT wrapper for KuCoin limit orders
            order = await self.client.exchange.create_order(
                symbol=symbol,
                type='limit',
                side=side.lower(),
                amount=amount,
                price=price,
                params=params
            )
            logger.info(f"Order Placed Successfully: ID {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place {side.upper()} order for {symbol}: {e}")
            return None

    async def cancel_all_orders(self, symbol: Optional[str] = None):
        """Emergency function to cancel active orders, often used by Circuit Breaker."""
        try:
            if symbol:
                await self.client.exchange.cancel_all_orders(symbol)
                logger.info(f"Cancelled all active orders for {symbol}")
            else:
                # Some exchanges require a symbol, KuCoin allows global cancel if supported by CCXT
                await self.client.exchange.cancel_all_orders()
                logger.info("Cancelled all active orders globally")
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")

order_manager = OrderManager()
