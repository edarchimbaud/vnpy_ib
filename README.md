# InteractiveBrokers Trading Interface for the VeighNa Framework

<p align="center">
  <img src ="https://vnpy.oss-cn-shanghai.aliyuncs.com/vnpy-logo.png"/>
</p>

<p align="center">
    <img src ="https://img.shields.io/badge/version-9.81.1.6-blueviolet.svg"/>
    <img src ="https://img.shields.io/badge/platform-windows|linux|macos-yellow.svg"/>
    <img src ="https://img.shields.io/badge/python-3.7|3.8|3.9|3.10-blue.svg" />
    <img src ="https://img.shields.io/github/license/vnpy/vnpy.svg?color=orange"/>
</p>

## Description

InteractiveBrokers trading interface developed based on ibapi version 9.81.1.post1.

Contract code naming rules and examples in IbGateway:

| ContractType | CodeRule | Code (symbol) | Exchange |
|--------------|----------|---------------|----------|
| Stocks | Name-Currency-Class | SPY-USD-STK | SMART |
| Forex | Name-Currency-Class | SPY-USD-STK | SMART |
| SPY-USD-STK | SMART | Forex | Name-Currency-Category | EUR-USD-CASH | IDEALPRO |
| Precious Metals | Name-Currency-Category | SPY-USD-STK | SMART |
| Precious Metals | Name-Currency-Category | XAUUSD-USD-CMDTY | SMART |
| Futures | Name-Maturity-Year-Month-Currency-Category | ES-202002-USD-FUT | GLOBEX |
| Futures (Specified Multiplier) | Name-Month-Expiry-Contract-Multiplier-Category | SI-202006-1000-USD-FUT | NYMEX |
| Options on Futures | Name - Expiration Month and Year - Option Type - Strike Price - Contract Multiplier - Currency - Category | ES-2020006-C-2430-50-USD-FOP | GLOBEX |

## Installation

The installation environment is recommended to be based on [[**VeighNa Studio**](https://www.vnpy.com)] above version 3.4.0.

Use the pip command directly:

``
pip install vnpy_ib
```

Or download the source code, unzip it and run it in cmd:

```bash
pip install .
```

## Use

Start as a script (script/run.py):

```python
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

from vnpy_ib import IbGateway


def main():
    """Main entry function"""
    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(IbGateway)

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
```
