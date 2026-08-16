import sys, os
# 確保可以直接執行或模組執行
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import dash
from dash import dcc, html
import plotly.graph_objs as go
from src.database_manager import DatabaseManager

def create_dashboard(db_name="trading_app.db"):
    db = DatabaseManager(db_name)
    results = db.get_backtest_results()

    strategies = [r[0] for r in results]
    profits = [r[1] for r in results]

    app = dash.Dash(__name__)
    app.layout = html.Div(children=[
        html.H1(children="交易回測儀表板"),
        dcc.Graph(
            id="backtest-results",
            figure={
                "data": [
                    go.Bar(x=strategies, y=profits, name="獲利")
                ],
                "layout": go.Layout(
                    title="回測結果",
                    xaxis={"title":"策略名稱"},
                    yaxis={"title":"獲利金額"}
                )
            }
        )
    ])
    return app

if __name__ == "__main__":
    app = create_dashboard()
    app.run(debug=True)   # ✅ 新版 Dash API
