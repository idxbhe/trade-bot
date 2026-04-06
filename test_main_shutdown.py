import asyncio
import sys
from ui.dashboard import TradingDashboard
from exchange.kucoin import kucoin_client

async def test_run():
    app = TradingDashboard()
    
    async def stop_after_delay():
        await asyncio.sleep(2)
        app.action_toggle_bot() # Start
        await asyncio.sleep(4)
        app.exit()
        
    asyncio.create_task(stop_after_delay())
    await app.run_async()
    await kucoin_client.close()

if __name__ == "__main__":
    asyncio.run(test_run())
