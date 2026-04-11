import sys
import asyncio
from ui.dashboard import TradingDashboard
from exchange.kucoin import kucoin_client, kucoin_futures_client
from core.database import init_db

def main():
    """
    Entry point for the KuCoin Trade Bot with TUI (Text User Interface).
    """
    try:
        # 1. Initialize Database & Tables Automatically
        asyncio.run(init_db())
        
        # 2. Run TUI Application
        app = TradingDashboard()
        app.run()
    except KeyboardInterrupt:
        print("\nBot shutting down (KeyboardInterrupt)...")
    except Exception as e:
        print(f"\nFatal Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
