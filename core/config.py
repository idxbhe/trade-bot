import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # --- KuCoin API Credentials (Only this is strictly required in .env for now) ---
    KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY")
    KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET")
    KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")
    
    # --- Environment & Database (Fallback to defaults if not in .env) ---
    KUCOIN_ENV = os.getenv("KUCOIN_ENV", "sandbox")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///trade_bot.db")
    
    # --- Risk Management Params (Fallback to defaults) ---
    # Default Risk: 1% per trade
    MAX_RISK_PER_TRADE_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.01"))
    
    # Default Circuit Breaker: 5% total daily drawdown
    CIRCUIT_BREAKER_PCT = float(os.getenv("CIRCUIT_BREAKER_PCT", "0.05"))
    
    # Master Safety Net: 10% total daily drawdown across all engines
    MASTER_CIRCUIT_BREAKER_PCT = float(os.getenv("MASTER_CIRCUIT_BREAKER_PCT", "0.10"))
    
    # Test Mode initial virtual balance
    TEST_INITIAL_BALANCE = float(os.getenv("TEST_INITIAL_BALANCE", "10000.0"))
    
    # Proxy Setup (Optional)
    BOT_PROXY_URL = os.getenv("BOT_PROXY_URL")

config = Config()
