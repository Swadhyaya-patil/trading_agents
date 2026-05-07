import pandas as pd

from data.hist_data import hist_data
from feature_engineering.enrich_features import enrich_features


class HistoricalAgent:

    def __init__(self):

        self.client = hist_data()
        self.client.log_in()

    def get_symbols(self):

        df = pd.read_csv("data/FNO_LST_190.csv")

        return df["Script"].tolist()

    def get_market_data(
        self,
        symbol,
        interval,
        from_date,
        to_date
    ):

        df = self.client.get_eq_data(
            symbol,
            symbol,
            from_date,
            to_date,
            interval
        )

        if df is None or len(df) == 0:
            return None

        # FEATURE ENRICHMENT
        df = enrich_features(df)

        return df