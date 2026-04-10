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
        Validates minimum amounts and formats precision before sending.
        """
        logger.info(f"Attempting to place {side.upper()} order for {amount:.4f} {symbol} @ ${price:,.2f} (Post-Only: {post_only})")
        
        try:
            await self.client.load_markets()
            
            # Use CCXT's built-in precision formatting
            formatted_amount = self.client.exchange.amount_to_precision(symbol, amount)
            formatted_price = self.client.exchange.price_to_precision(symbol, price)
            
            # Check minimums
            limits = await self.client.get_market_limits(symbol)
            if limits and 'limits' in limits:
                min_amount = limits['limits'].get('amount', {}).get('min', 0)
                if float(formatted_amount) < min_amount:
                    logger.warning(f"Order amount {formatted_amount} is below minimum {min_amount} for {symbol}. Order rejected.")
                    return None
                    
                min_cost = limits['limits'].get('cost', {}).get('min', 0)
                if (float(formatted_amount) * float(formatted_price)) < min_cost:
                    logger.warning(f"Order cost is below minimum {min_cost} for {symbol}. Order rejected.")
                    return None

            params = {'postOnly': post_only}
            # CCXT wrapper for KuCoin limit orders
            order = await self.client.exchange.create_order(
                symbol=symbol,
                type='limit',
                side=side.lower(),
                amount=float(formatted_amount),
                price=float(formatted_price),
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
                # Global cancel is too risky in a multi-bot environment
                logger.warning("Global cancel requested but blocked to prevent cancelling non-bot orders. Please provide a specific symbol.")
                return
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")

order_manager = OrderManager()
