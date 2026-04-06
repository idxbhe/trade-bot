import asyncio
from ui.dashboard import TradingDashboard
from core.logger import log_queue
import threading

async def run_test():
    app = TradingDashboard()
    
    async def simulate_start():
        await asyncio.sleep(2)
        app.action_toggle_bot() # Start bot
        await asyncio.sleep(5)
        app.exit()
    
    asyncio.create_task(simulate_start())
    await app.run_async()
    
    # print the logs
    while not log_queue.empty():
        print(log_queue.get_nowait())

asyncio.run(run_test())
