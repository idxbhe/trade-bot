from typing import Dict, Any, Optional
from core.logger import logger
from exchange.kucoin import kucoin_client, kucoin_futures_client

class OrderManager:
    """
    Translates validated signals into actual exchange API calls.
    Handles 'Post-Only' limits to enforce Maker fees for both Spot and Futures.
    """
    def __init__(self):
        pass

    async def execute_limit_order(self, symbol: str, side: str, amount: float, price: float, market: str, leverage: int = 1, post_only: bool = True, reduce_only: bool = False) -> Optional[Dict[str, Any]]:
        """
        Executes a Limit Order on KuCoin Spot or Futures.
        Validates minimum amounts and formats precision before sending.
        """
        logger.info(f"Attempting to place {market.upper()} {side.upper()} order for {amount:.4f} {symbol} @ ${price:,.2f} (Post-Only: {post_only})")
        
        is_futures = (market.lower() == 'futures')
        client = kucoin_futures_client if is_futures else kucoin_client
        
        try:
            await client.load_markets()
            
            # Use CCXT's built-in precision formatting
            formatted_amount = client.exchange.amount_to_precision(symbol, amount)
            formatted_price = client.exchange.price_to_precision(symbol, price)
            
            # Check minimums
            limits = await client.get_market_limits(symbol)
            if limits and 'limits' in limits:
                min_amount = limits['limits'].get('amount', {}).get('min', 0)
                if float(formatted_amount) < min_amount:
                    logger.warning(f"Order amount {formatted_amount} is below minimum {min_amount} for {symbol}. Order rejected.")
                    return None
                    
                if not is_futures:
                    min_cost = limits['limits'].get('cost', {}).get('min', 0)
                    if (float(formatted_amount) * float(formatted_price)) < min_cost:
                        logger.warning(f"Order cost is below minimum {min_cost} for {symbol}. Order rejected.")
                        return None

            params = {}
            if post_only:
                params['postOnly'] = True

            if is_futures:
                params['leverage'] = leverage
                if reduce_only:
                    params['reduceOnly'] = True

            # CCXT wrapper for KuCoin limit orders
            order = await client.exchange.create_order(
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

    async def execute_market_order(self, symbol: str, side: str, amount: float, market: str, leverage: int = 1, reduce_only: bool = False) -> Optional[Dict[str, Any]]:
        """
        Executes a Market Order on KuCoin Spot or Futures.
        Used for emergency exits where immediate execution is more critical than maker fees.
        """
        logger.info(f"Attempting to place {market.upper()} {side.upper()} MARKET order for {amount:.4f} {symbol}")
        
        is_futures = (market.lower() == 'futures')
        client = kucoin_futures_client if is_futures else kucoin_client
        
        try:
            await client.load_markets()
            
            # Use CCXT's built-in precision formatting
            formatted_amount = client.exchange.amount_to_precision(symbol, amount)
            
            # Check minimums
            limits = await client.get_market_limits(symbol)
            if limits and 'limits' in limits:
                min_amount = limits['limits'].get('amount', {}).get('min', 0)
                if float(formatted_amount) < min_amount:
                    logger.warning(f"Market order amount {formatted_amount} is below minimum {min_amount} for {symbol}. Order rejected.")
                    return None

            params = {}
            if is_futures:
                params['leverage'] = leverage
                if reduce_only:
                    params['reduceOnly'] = True

            # CCXT wrapper for KuCoin market orders
            order = await client.exchange.create_order(
                symbol=symbol,
                type='market',
                side=side.lower(),
                amount=float(formatted_amount),
                params=params
            )
            logger.info(f"Market Order Placed Successfully: ID {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place {side.upper()} MARKET order for {symbol}: {e}")
            return None

    async def cancel_all_orders(self, symbol: Optional[str] = None, market: str = 'spot'):
        """Emergency function to cancel active orders."""
        try:
            is_futures = (market.lower() == 'futures')
            client = kucoin_futures_client if is_futures else kucoin_client
            
            if symbol:
                await client.exchange.cancel_all_orders(symbol)
                logger.info(f"Cancelled all active orders for {symbol}")
            else:
                logger.warning("Global cancel requested but blocked to prevent cancelling non-bot orders. Please provide a specific symbol.")
                return
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")

order_manager = OrderManager()
