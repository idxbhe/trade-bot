import asyncio
import pandas as pd
from core.logger import logger
from risk.position_sizer import position_sizer
from risk.circuit_breaker import CircuitBreaker
from execution.order_manager import order_manager

async def run_test_risk_and_execution():
    logger.info("--- Testing Risk Management (Phase 4) ---")
    
    # 1. Test Circuit Breaker
    logger.info("\n[1] Testing Circuit Breaker:")
    circuit_breaker = CircuitBreaker(max_daily_drawdown_pct=0.05)
    baseline = 10000.0
    
    # Simulate a small loss
    is_tripped = circuit_breaker.is_tripped(9800.0, baseline) # 2% loss
    logger.info(f"Equity drops to $9800 (2% loss) -> Is Safe to trade? {not is_tripped}")
    
    # Simulate a catastrophic loss (> 5%)
    is_tripped = circuit_breaker.is_tripped(9400.0, baseline) # 6% loss
    logger.info(f"Equity drops to $9400 (6% loss) -> Is Safe to trade? {not is_tripped}")
    
    # Reset for next tests
    circuit_breaker.reset()
    
    # 2. Test Position Sizing & ATR Stop Loss
    logger.info("\n[2] Testing Position Sizer (Mean Reversion Scenario):")
    entry_price = 50000.0
    current_atr = 1500.0
    account_equity = 10000.0
    
    # Calculate Stop Loss
    sl_price = position_sizer.calculate_stop_loss(entry_price, current_atr, is_long=True)
    logger.info(f"Entry: ${entry_price:,.2f} | ATR: ${current_atr:,.2f} | Dynamic Stop Loss: ${sl_price:,.2f}")
    
    # Calculate Size (Risking exactly 1% of $10,000 = $100)
    size, actual_risk = position_sizer.calculate_position_size(account_equity, entry_price, sl_price)
    logger.info(f"Calculated Size: {size:.6f} BTC | Actual Risk: ${actual_risk:,.2f} (Max allowed: $100.00)")
    
    # 3. Test Order Execution (Mocking/Sandbox API expected to fail safely without valid keys)
    logger.info("\n[3] Testing Order Execution Engine (Dry Run):")
    if True:  # Changed from undefined is_safe
         logger.info("Circuit breaker is reset, attempting to format a mock order...")
         symbol = "BTC/USDT"
         # This will just print the intent and catch the CCXT exception since we use public/dummy keys
         await order_manager.execute_limit_order("Test_Engine", symbol, "buy", size, entry_price, "spot", post_only=True)
    
    # Cleanup CCXT session
    from exchange.kucoin import kucoin_client
    await kucoin_client.close()

if __name__ == '__main__':
    asyncio.run(run_test_risk_and_execution())
