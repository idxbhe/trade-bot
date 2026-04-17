from typing import Dict, Any, Optional
import time
from decimal import Decimal
from core.logger import logger
from exchange.kucoin import kucoin_client, kucoin_futures_client

class OrderManager:
    """
    Translates validated signals into actual exchange API calls.
    Handles 'Post-Only' limits to enforce Maker fees for both Spot and Futures.
    """
    def __init__(self):
        pass

    async def execute_limit_order(self, engine_name: str, symbol: str, side: str, amount: float, price: float, market: str, leverage: int = 1, post_only: bool = True, reduce_only: bool = False) -> Optional[Dict[str, Any]]:
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
                if Decimal(str(formatted_amount)) < Decimal(str(min_amount)):
                    logger.warning(f"Order amount {formatted_amount} is below minimum {min_amount} for {symbol}. Order rejected.")
                    return None
                    
                if not is_futures:
                    min_cost = limits['limits'].get('cost', {}).get('min', 0)
                    if (Decimal(str(formatted_amount)) * Decimal(str(formatted_price))) < Decimal(str(min_cost)):
                        logger.warning(f"Order cost is below minimum {min_cost} for {symbol}. Order rejected.")
                        return None

            params = {
                'clientOid': f"{engine_name.replace('_', '')}{int(time.time()*1000)}"
            }
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
                if Decimal(str(formatted_amount)) < Decimal(str(min_amount)):
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

    async def execute_oco_order(self, symbol: str, side: str, amount: float, tp_price: float, sl_trigger_price: float, market: str) -> Optional[Dict[str, Any]]:
        """
        Executes an OCO (One-Cancels-the-Other) order on KuCoin Spot.
        Combines a Limit (Take Profit) and a Stop-Limit (Stop Loss).
        """
        logger.info(f"Placing OCO Protection for {symbol}: TP @ ${tp_price:,.2f}, SL Trigger @ ${sl_trigger_price:,.2f}")
        
        client = kucoin_client # OCO is Spot only
        try:
            await client.load_markets()
            
            # Formatted values
            fmt_amount = client.exchange.amount_to_precision(symbol, amount)
            fmt_tp = client.exchange.price_to_precision(symbol, tp_price)
            fmt_sl_trigger = client.exchange.price_to_precision(symbol, sl_trigger_price)
            
            # Limit price for SL part: 0.2% buffer to ensure execution
            if side.lower() == 'sell': # Closing a LONG
                sl_limit_price = sl_trigger_price * 0.998
            else: # Closing a SHORT (if Spot supports short, but Spot is usually focus on buys)
                sl_limit_price = sl_trigger_price * 1.002
            
            fmt_sl_limit = client.exchange.price_to_precision(symbol, sl_limit_price)
            
            # Using implicit API for KuCoin OCO
            # ref: https://www.kucoin.com/docs/rest/spot-trading/oco-orders/place-oco-order
            payload = {
                'symbol': client.exchange.market(symbol)['id'], # KuCoin expects 'BTC-USDT'
                'side': side.lower(),
                'price': fmt_tp,
                'stopPrice': fmt_sl_trigger,
                'limitPrice': fmt_sl_limit,
                'size': fmt_amount,
                'tradeType': 'TRADE'
            }
            
            # Implicit private POST call
            oco = await client.exchange.private_post_oco_order(payload)
            logger.info(f"OCO Order Placed Successfully: ID {oco.get('data', {}).get('orderId')}")
            return oco.get('data') # Returns object contains orderId
        except Exception as e:
            logger.error(f"Failed to place OCO order for {symbol}: {e}")
            return None

    async def execute_conditional_order(self, symbol: str, side: str, amount: float, trigger_price: float, market: str, stop_type: str = 'down', leverage: int = 1) -> Optional[Dict[str, Any]]:
        """
        Executes a Conditional Order (Stop Market) on KuCoin Futures.
        Used as a safety net (SL/TP) directly on the exchange.
        """
        logger.info(f"Placing Futures Conditional ({stop_type}) for {symbol} @ ${trigger_price:,.2f}")
        
        client = kucoin_futures_client
        try:
            await client.load_markets()
            fmt_amount = client.exchange.amount_to_precision(symbol, amount)
            fmt_trigger = client.exchange.price_to_precision(symbol, trigger_price)
            
            params = {
                'stop': stop_type,
                'stopPrice': fmt_trigger,
                'reduceOnly': True,
                'leverage': leverage
            }
            
            # Type 'market' with stop params creates a Stop Market order on KuCoin Futures via CCXT
            order = await client.exchange.create_order(
                symbol=symbol,
                type='market',
                side=side.lower(),
                amount=float(fmt_amount),
                params=params
            )
            logger.info(f"Conditional Order Placed: ID {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place {stop_type} conditional order for {symbol}: {e}")
            return None

    async def cancel_order(self, order_id: str, symbol: str, market: str = 'spot'):
        """Cancel a specific order by ID."""
        try:
            is_futures = (market.lower() == 'futures')
            client = kucoin_futures_client if is_futures else kucoin_client
            await client.exchange.cancel_order(order_id, symbol)
            logger.info(f"Cancelled order {order_id} for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

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
