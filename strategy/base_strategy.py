from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any

class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, current_price: float) -> Dict[str, Any]:
        """
        Analyzes the dataframe and returns a signal dictionary.
        Format: {'signal': 'BUY'/'SELL'/'HOLD'/'ACTIVATE_GRID', 'reason': '...'}
        """
        pass
