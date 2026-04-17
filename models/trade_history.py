from sqlalchemy import Column, Integer, String, Float, DateTime, func
from core.database import Base

class TradeHistory(Base):
    __tablename__ = "trade_history"

    id = Column(Integer, primary_key=True, index=True)
    engine_name = Column(String, index=True)
    market = Column(String, index=True)
    mode = Column(String, index=True)
    symbol = Column(String, index=True)
    side = Column(String)
    amount = Column(Float)
    entry_price = Column(Float)
    exit_price = Column(Float)
    pnl = Column(Float)
    max_pnl = Column(Float)
    min_pnl = Column(Float)
    reason = Column(String)
    time = Column(String) # For legacy display
    created_at = Column(DateTime, server_default=func.now())

class ActivePosition(Base):
    __tablename__ = "active_positions"

    id = Column(Integer, primary_key=True, index=True)
    engine_name = Column(String, index=True)
    mode = Column(String, index=True) # Added to separate modes
    symbol = Column(String, index=True)
    side = Column(String)
    amount = Column(Float)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    sl_order_id = Column(String, nullable=True)
    tp_order_id = Column(String, nullable=True)
    closing_order_id = Column(String, nullable=True)
    locked_margin = Column(Float, default=0.0)
    max_pnl = Column(Float, default=0.0)
    min_pnl = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())

class EquityBaseline(Base):
    __tablename__ = "equity_baselines"

    id = Column(Integer, primary_key=True, index=True)
    engine_name = Column(String, index=True)
    mode = Column(String, index=True) # Added to separate modes
    current_equity = Column(Float) # Persist the wallet balance
    daily = Column(Float)
    weekly = Column(Float)
    monthly = Column(Float)
    yearly = Column(Float)
    last_reset_daily = Column(DateTime)
    last_reset_weekly = Column(DateTime)
    last_reset_monthly = Column(DateTime)
    last_reset_yearly = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


