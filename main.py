import sys
import asyncio
from ui.dashboard import TradingDashboard
from exchange.kucoin import kucoin_client, kucoin_futures_client

def main():
    """
    Entry point for the KuCoin Trade Bot with TUI (Text User Interface).
    """
    try:
        app = TradingDashboard()
        app.run()
    except KeyboardInterrupt:
        print("\nBot shutting down (KeyboardInterrupt)...")
    except Exception as e:
        print(f"\nFatal Error: {e}")
        sys.exit(1)
    finally:
        # Menutup semua session CCXT secara bersih untuk menghindari Unclosed Connector error dari Python
        asyncio.run(kucoin_client.close())
        asyncio.run(kucoin_futures_client.close())

if __name__ == "__main__":
    main()
