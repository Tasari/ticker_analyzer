# Stock Analyzer

Local Streamlit app for rule-based stock analysis using `yfinance`.

## Setup

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## Features

- Ticker input for any symbol supported by `yfinance`.
- Growth, Fundamentals, and Value tabs.
- Partial scoring when data is missing.
- Editable scoring configuration saved in `metrics_config.json`.
- Basic charts for price, financial trends, assets, and debt.
