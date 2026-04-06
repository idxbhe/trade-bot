from strategy.base_strategy import BaseStrategy
import pandas as pd
from typing import Dict, Any, List

class NeutralGridStrategy(BaseStrategy):
    def __init__(self, lower_bound: float, upper_bound: float, grids: int = 20):
        super().__init__("Neutral_Grid")
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound
        self.grids = grids
        self.grid_levels = self._calculate_geometric_grids()
        
    def _calculate_geometric_grids(self) -> List[float]:
        """
        Calculate grid levels using Geometric progression.
        Formula: P_i = L * (1 + r)^i
        """
        # P_n = U = L * (1 + r)^n => (1 + r) = (U / L)^(1/n)
        ratio = (self.upper_bound / self.lower_bound) ** (1 / self.grids)
        levels = [self.lower_bound * (ratio ** i) for i in range(self.grids + 1)]
        return levels

    def generate_signal(self, df: pd.DataFrame, current_price: float) -> Dict[str, Any]:
        if df.empty or 'adx_14' not in df.columns:
            return {'signal': 'HOLD', 'reason': 'Missing indicators'}
            
        last_row = df.iloc[-1]
        adx = last_row['adx_14']
        
        if pd.isna(adx):
             return {'signal': 'HOLD', 'reason': 'ADX is calculating (NaN)'}

        # Sideways condition based on ADX < 20
        if adx < 20:
            return {
                'signal': 'ACTIVATE_GRID',
                'reason': f'ADX is {adx:.2f} (< 20), sideways market detected.',
                'levels': self.grid_levels
            }
            
        return {'signal': 'HOLD', 'reason': f'ADX is {adx:.2f} (>= 20), trending market.'}
