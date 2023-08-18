"""
IB Symbol Rules

SPY-USD-STK   SMART
EUR-USD-CASH  IDEALPRO
XAUUSD-USD-CMDTY  SMART
ES-202002-USD-FUT  GLOBEX
SI-202006-1000-USD-FUT  NYMEX
ES-2020006-C-2430-50-USD-FOP  GLOBEX
"""


from copy import copy
from datetime import datetime, timedelta
from threading import Thread, Condition
from typing import Optional, Dict, Any, List
import shelve
from tzlocal import get_localzone_name

from vnpy.event import EventEngine
from ibapi.client import EClient
from ibapi.common import OrderId, TickAttrib, TickerId
from ibapi.contract import Contract, ContractDetails
from ibapi.execution import Execution
from ibapi.order import Order
from ibapi.order_state import OrderState
from ibapi.ticktype import TickType, TickTypeEnum
from ibapi.wrapper import EWrapper
from ibapi.common import BarData as IbBarData

from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData,
    OrderData,
    TradeData,
    PositionData,
    AccountData,
    ContractData,
    BarData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    HistoryRequest
)
from vnpy.trader.constant import (
    Product,
    OrderType,
    Direction,
    Exchange,
    Currency,
    Status,
    OptionType,
    Interval
)
from vnpy.trader.utility import get_file_path, ZoneInfo
from vnpy.trader.event import EVENT_TIMER
from vnpy.event import Event

# Order state mapping
STATUS_IB2VT: Dict[str, Status] = {
    "ApiPending": Status.SUBMITTING,
    "PendingSubmit": Status.SUBMITTING,
    "PreSubmitted": Status.NOTTRADED,
    "Submitted": Status.NOTTRADED,
    "ApiCancelled": Status.CANCELLED,
    "Cancelled": Status.CANCELLED,
    "Filled": Status.ALLTRADED,
    "Inactive": Status.REJECTED,
}

# Long/short directional mapping
DIRECTION_VT2IB: Dict[Direction, str] = {Direction.LONG: "BUY", Direction.SHORT: "SELL"}
DIRECTION_IB2VT: Dict[str, Direction] = {v: k for k, v in DIRECTION_VT2IB.items()}
DIRECTION_IB2VT["BOT"] = Direction.LONG
DIRECTION_IB2VT["SLD"] = Direction.SHORT

# Order type mapping
ORDERTYPE_VT2IB: Dict[OrderType, str] = {
    OrderType.LIMIT: "LMT",
    OrderType.MARKET: "MKT",
    OrderType.STOP: "STP"
}
ORDERTYPE_IB2VT: Dict[str, OrderType] = {v: k for k, v in ORDERTYPE_VT2IB.items()}

# Exchange mapping
EXCHANGE_VT2IB: Dict[Exchange, str] = {
    Exchange.SMART: "SMART",
    Exchange.NYMEX: "NYMEX",
    Exchange.GLOBEX: "GLOBEX",
    Exchange.IDEALPRO: "IDEALPRO",
    Exchange.CME: "CME",
    Exchange.ICE: "ICE",
    Exchange.SEHK: "SEHK",
    Exchange.SSE: "SEHKNTL",
    Exchange.SZSE: "SEHKSZSE",
    Exchange.HKFE: "HKFE",
    Exchange.CFE: "CFE",
    Exchange.TSE: "TSE",
    Exchange.NYSE: "NYSE",
    Exchange.NASDAQ: "NASDAQ",
    Exchange.AMEX: "AMEX",
    Exchange.ARCA: "ARCA",
    Exchange.EDGEA: "EDGEA",
    Exchange.ISLAND: "ISLAND",
    Exchange.BATS: "BATS",
    Exchange.IEX: "IEX",
    Exchange.IBKRATS: "IBKRATS",
    Exchange.OTC: "PINK",
    Exchange.SGX: "SGX"
}
EXCHANGE_IB2VT: Dict[str, Exchange] = {v: k for k, v in EXCHANGE_VT2IB.items()}

# Product type mapping
PRODUCT_IB2VT: Dict[str, Product] = {
    "STK": Product.EQUITY,
    "CASH": Product.FOREX,
    "CMDTY": Product.SPOT,
    "FUT": Product.FUTURES,
    "OPT": Product.OPTION,
    "FOP": Product.OPTION,
    "CONTFUT": Product.FUTURES,
    "IND": Product.INDEX
}

# Option type mapping
OPTION_VT2IB: Dict[str, OptionType] = {OptionType.CALL: "CALL", OptionType.PUT: "PUT"}

# Currency type mapping
CURRENCY_VT2IB: Dict[Currency, str] = {
    Currency.USD: "USD",
    Currency.CAD: "CAD",
    Currency.CNY: "CNY",
    Currency.HKD: "HKD",
}

# Slice data field mapping
TICKFIELD_IB2VT: Dict[int, str] = {
    0: "bid_volume_1",
    1: "bid_price_1",
    2: "ask_price_1",
    3: "ask_volume_1",
    4: "last_price",
    5: "last_volume",
    6: "high_price",
    7: "low_price",
    8: "volume",
    9: "pre_close",
    14: "open_price",
}

# Account type mapping
ACCOUNTFIELD_IB2VT: Dict[str, str] = {
    "NetLiquidationByCurrency": "balance",
    "NetLiquidation": "balance",
    "UnrealizedPnL": "positionProfit",
    "AvailableFunds": "available",
    "MaintMarginReq": "margin",
}

# Data frequency mapping
INTERVAL_VT2IB: Dict[Interval, str] = {
    Interval.MINUTE: "1 min",
    Interval.HOUR: "1 hour",
    Interval.DAILY: "1 day",
}

# Other constants
LOCAL_TZ = ZoneInfo(get_localzone_name())
JOIN_SYMBOL: str = "-"


class IbGateway(BaseGateway):
    """
    VeighNa is used to interface with IB's trading interface.
    """

    default_name: str = "IB"

    default_setting: Dict[str, Any] = {
        "TWS address": "127.0.0.1",
        "TWS port": 7497,
        "Client ID": 1,
        "Trading account": ""
    }

    exchanges: List[str] = list(EXCHANGE_VT2IB.keys())

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """Constructor"""""
        super().__init__(event_engine, gateway_name)

        self.api: "IbApi" = IbApi(self)
        self.count: int = 0

    def connect(self, setting: dict) -> None:
        """Connect to broker API"""
        host: str = setting["TWS address"]
        port: int = setting["TWS port"]
        clientid: int = setting["Client ID"]
        account: str = setting["Trading account"]

        self.api.connect(host, port, clientid, account)

        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def close(self) -> None:
        """Shut down the API"""
        self.api.close()

    def subscribe(self, req: SubscribeRequest) -> None:
        """Subscribe to quotes"""
        self.api.subscribe(req)

    def send_order(self, req: OrderRequest) -> str:
        """Place an order"""
        return self.api.send_order(req)

    def cancel_order(self, req: CancelRequest) -> None:
        """Order withdrawal"""
        self.api.cancel_order(req)

    def query_account(self) -> None:
        """Search for funds"""
        pass

    def query_position(self) -> None:
        """Check positions"""
        pass

    def query_history(self, req: HistoryRequest) -> List[BarData]:
        """Query historical data"""
        return self.api.query_history(req)

    def process_timer_event(self, event: Event) -> None:
        """Timed event handling"""
        self.count += 1
        if self.count < 10:
            return
        self.count = 0

        self.api.check_connection()


class IbApi(EWrapper):
    """IB's API interface"""

    data_filename: str = "ib_contract_data.db"
    data_filepath: str = str(get_file_path(data_filename))

    def __init__(self, gateway: IbGateway) -> None:
        """Constructor"""
        super().__init__()

        self.gateway: IbGateway = gateway
        self.gateway_name: str = gateway.gateway_name

        self.status: bool = False

        self.reqid: int = 0
        self.orderid: int = 0
        self.clientid: int = 0
        self.history_reqid: int = 0
        self.account: str = ""
        self.ticks: Dict[int, TickData] = {}
        self.orders: Dict[str, OrderData] = {}
        self.accounts: Dict[str, AccountData] = {}
        self.contracts: Dict[str, ContractData] = {}

        self.tick_exchange: Dict[int, Exchange] = {}
        self.subscribed: Dict[str, SubscribeRequest] = {}
        self.data_ready: bool = False

        self.history_req: HistoryRequest = None
        self.history_condition: Condition = Condition()
        self.history_buf: List[BarData] = []

        self.client: EClient = EClient(self)

    def connectAck(self) -> None:
        """Connection success returns"""
        self.status = True
        self.gateway.write_log("IB TWS connection successful")

        self.load_contract_data()

        self.data_ready = False

    def connectionClosed(self) -> None:
        """Connection disconnection return"""
        self.status = False
        self.gateway.write_log("IB TWS disconnected.")

    def nextValidId(self, orderId: int) -> None:
        """Next valid order number return"""
        super().nextValidId(orderId)

        if not self.orderid:
            self.orderid = orderId

    def currentTime(self, time: int) -> None:
        """IB Current Server Time Returns"""
        super().currentTime(time)

        dt: datetime = datetime.fromtimestamp(time)
        time_string: str = dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        msg: str = f"Server time: {time_string}"
        self.gateway.write_log(msg)

    def error(self, reqId: TickerId, errorCode: int, errorString: str) -> None:
        """Specific error request return"""
        super().error(reqId, errorCode, errorString)
    
        # Information notification 2000-2999 is not an error message
        if reqId == self.history_reqid and errorCode not in range(2000, 3000):
            self.history_condition.acquire()
            self.history_condition.notify()
            self.history_condition.release()

        msg: str = f"Message notification, code: {errorCode}, content: {errorString}"
        self.gateway.write_log(msg)

        # Quote server connected
        if errorCode == 2104 and not self.data_ready:
            self.data_ready = True

            self.client.reqCurrentTime()

            reqs: list = list(self.subscribed.values())
            self.subscribed.clear()
            for req in reqs:
                self.subscribe(req)

    def tickPrice(
        self, reqId: TickerId, tickType: TickType, price: float, attrib: TickAttrib
    ) -> None:
        """Tick price update returns"""
        super().tickPrice(reqId, tickType, price, attrib)

        if tickType not in TICKFIELD_IB2VT:
            return

        tick: TickData = self.ticks[reqId]
        name: str = TICKFIELD_IB2VT[tickType]
        setattr(tick, name, price)

        # Update tick data name field
        contract: ContractData = self.contracts.get(tick.vt_symbol, None)
        if contract:
            tick.name = contract.name

        # Local calculation of tick time and latest prices for Forex of IDEALPRO and Spot Commodity
        exchange: Exchange = self.tick_exchange[reqId]
        if exchange is Exchange.IDEALPRO or "CMDTY" in tick.symbol:
            if not tick.bid_price_1 or not tick.ask_price_1:
                return
            tick.last_price = (tick.bid_price_1 + tick.ask_price_1) / 2
            tick.datetime = datetime.now(LOCAL_TZ)
        self.gateway.on_tick(copy(tick))

    def tickSize(
        self, reqId: TickerId, tickType: TickType, size: int
    ) -> None:
        """Tick size update return"""
        super().tickSize(reqId, tickType, size)

        if tickType not in TICKFIELD_IB2VT:
            return

        tick: TickData = self.ticks[reqId]
        name: str = TICKFIELD_IB2VT[tickType]
        setattr(tick, name, size)

        self.gateway.on_tick(copy(tick))

    def tickString(
        self, reqId: TickerId, tickType: TickType, value: str
    ) -> None:
        """Tick string update return"""
        super().tickString(reqId, tickType, value)

        if tickType != TickTypeEnum.LAST_TIMESTAMP:
            return

        tick: TickData = self.ticks[reqId]
        dt: datetime = datetime.fromtimestamp(int(value))
        tick.datetime = dt.replace(tzinfo=LOCAL_TZ)

        self.gateway.on_tick(copy(tick))

    def orderStatus(
        self,
        orderId: OrderId,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        """Order status update return"""
        super().orderStatus(
            orderId,
            status,
            filled,
            remaining,
            avgFillPrice,
            permId,
            parentId,
            lastFillPrice,
            clientId,
            whyHeld,
            mktCapPrice,
        )

        orderid: str = str(orderId)
        order: OrderData = self.orders.get(orderid, None)
        if not order:
            return

        order.traded = filled

        # Filtering of withdrawal abort status
        order_status: Status = STATUS_IB2VT.get(status, None)
        if order_status:
            order.status = order_status

        self.gateway.on_order(copy(order))

    def openOrder(
        self,
        orderId: OrderId,
        ib_contract: Contract,
        ib_order: Order,
        orderState: OrderState,
    ) -> None:
        """New order return"""
        super().openOrder(
            orderId, ib_contract, ib_order, orderState
        )

        orderid: str = str(orderId)
        order: OrderData = OrderData(
            symbol=generate_symbol(ib_contract),
            exchange=EXCHANGE_IB2VT.get(ib_contract.exchange, Exchange.SMART),
            type=ORDERTYPE_IB2VT[ib_order.orderType],
            orderid=orderid,
            direction=DIRECTION_IB2VT[ib_order.action],
            volume=ib_order.totalQuantity,
            gateway_name=self.gateway_name,
        )

        if order.type == OrderType.LIMIT:
            order.price = ib_order.lmtPrice
        elif order.type == OrderType.STOP:
            order.price = ib_order.auxPrice

        self.orders[orderid] = order
        self.gateway.on_order(copy(order))

    def updateAccountValue(
        self, key: str, val: str, currency: str, accountName: str
    ) -> None:
        """Account update return"""
        super().updateAccountValue(key, val, currency, accountName)

        if not currency or key not in ACCOUNTFIELD_IB2VT:
            return

        accountid: str = f"{accountName}.{currency}"
        account: AccountData = self.accounts.get(accountid, None)
        if not account:
            account = AccountData(
                accountid=accountid,
                gateway_name=self.gateway_name
            )
            self.accounts[accountid] = account

        name: str = ACCOUNTFIELD_IB2VT[key]
        setattr(account, name, float(val))

    def updatePortfolio(
        self,
        contract: Contract,
        position: float,
        marketPrice: float,
        marketValue: float,
        averageCost: float,
        unrealizedPNL: float,
        realizedPNL: float,
        accountName: str,
    ) -> None:
        """Position update return"""
        super().updatePortfolio(
            contract,
            position,
            marketPrice,
            marketValue,
            averageCost,
            unrealizedPNL,
            realizedPNL,
            accountName,
        )

        if contract.exchange:
            exchange: Exchange = EXCHANGE_IB2VT.get(contract.exchange, None)
        elif contract.primaryExchange:
            exchange: Exchange = EXCHANGE_IB2VT.get(contract.primaryExchange, None)
        else:
            exchange: Exchange = Exchange.SMART   # Use smart routing for default

        if not exchange:
            msg: str = f"Existence of unsupported exchange positions {generate_symbol(contract)} {contract.exchange} {contract.primaryExchange}"
            self.gateway.write_log(msg)
            return

        try:
            ib_size: int = int(contract.multiplier)
        except ValueError:
            ib_size = 1
        price = averageCost / ib_size

        pos: PositionData = PositionData(
            symbol=generate_symbol(contract),
            exchange=exchange,
            direction=Direction.NET,
            volume=position,
            price=price,
            pnl=unrealizedPNL,
            gateway_name=self.gateway_name,
        )
        self.gateway.on_position(pos)

    def updateAccountTime(self, timeStamp: str) -> None:
        """Account update time return"""
        super().updateAccountTime(timeStamp)
        for account in self.accounts.values():
            self.gateway.on_account(copy(account))

    def contractDetails(self, reqId: int, contractDetails: ContractDetails) -> None:
        """Contract data update return"""
        super().contractDetails(reqId, contractDetails)

        # Generate vnpy code from IB contract
        ib_contract: Contract = contractDetails.contract
        if not ib_contract.multiplier:
            ib_contract.multiplier = 1

        symbol: str = generate_symbol(ib_contract)

        # Generate a contract
        contract: ContractData = ContractData(
            symbol=symbol,
            exchange=EXCHANGE_IB2VT[ib_contract.exchange],
            name=contractDetails.longName,
            product=PRODUCT_IB2VT[ib_contract.secType],
            size=int(ib_contract.multiplier),
            pricetick=contractDetails.minTick,
            net_position=True,
            history_data=True,
            stop_supported=True,
            gateway_name=self.gateway_name,
        )

        if contract.vt_symbol not in self.contracts:
            self.gateway.on_contract(contract)

            self.contracts[contract.vt_symbol] = contract
            self.save_contract_data()

    def execDetails(
        self, reqId: int, contract: Contract, execution: Execution
    ) -> None:
        """Transaction data update return"""
        super().execDetails(reqId, contract, execution)

        dt: datetime = datetime.strptime(execution.time, "%Y%m%d  %H:%M:%S")
        dt: datetime = dt.replace(tzinfo=LOCAL_TZ)

        trade: TradeData = TradeData(
            symbol=generate_symbol(contract),
            exchange=EXCHANGE_IB2VT.get(contract.exchange, Exchange.SMART),
            orderid=str(execution.orderId),
            tradeid=str(execution.execId),
            direction=DIRECTION_IB2VT[execution.side],
            price=execution.price,
            volume=execution.shares,
            datetime=dt,
            gateway_name=self.gateway_name,
        )

        self.gateway.on_trade(trade)

    def managedAccounts(self, accountsList: str) -> None:
        """Returns on all sub-accounts"""
        super().managedAccounts(accountsList)

        if not self.account:
            for account_code in accountsList.split(","):
                if account_code:
                    self.account = account_code

        self.gateway.write_log(f"The currently used trading account is {self.account}")
        self.client.reqAccountUpdates(True, self.account)

    def historicalData(self, reqId: int, ib_bar: IbBarData) -> None:
        """Historical data update return"""
        # Daily-level data and weekly-level date data are in the form of %Y%m%d
        if len(ib_bar.date) > 8:
            dt: datetime = datetime.strptime(ib_bar.date, "%Y%m%d %H:%M:%S")
        else:
            dt: datetime = datetime.strptime(ib_bar.date, "%Y%m%d")
        dt: datetime = dt.replace(tzinfo=LOCAL_TZ)

        bar: BarData = BarData(
            symbol=self.history_req.symbol,
            exchange=self.history_req.exchange,
            datetime=dt,
            interval=self.history_req.interval,
            volume=ib_bar.volume,
            open_price=ib_bar.open,
            high_price=ib_bar.high,
            low_price=ib_bar.low,
            close_price=ib_bar.close,
            gateway_name=self.gateway_name
        )
        if bar.volume < 0:
            bar.volume = 0

        self.history_buf.append(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:
        """Historical data query completion return"""
        self.history_condition.acquire()
        self.history_condition.notify()
        self.history_condition.release()

    def connect(self, host: str, port: int, clientid: int, account: str) -> None:
        """Connecting to TWS"""
        if self.status:
            return

        self.host = host
        self.port = port
        self.clientid = clientid
        self.account = account

        self.client.connect(host, port, clientid)
        self.thread = Thread(target=self.client.run)
        self.thread.start()

    def check_connection(self) -> None:
        """Checking the connection"""
        if self.client.isConnected():
            return

        if self.status:
            self.close()

        self.client.connect(self.host, self.port, self.clientid)

        self.thread = Thread(target=self.client.run)
        self.thread.start()

    def close(self) -> None:
        """Disconnect TWS"""
        if not self.status:
            return

        self.status = False
        self.client.disconnect()

    def subscribe(self, req: SubscribeRequest) -> None:
        """Subscribe to tick data update"""
        if not self.status:
            return

        if req.exchange not in EXCHANGE_VT2IB:
            self.gateway.write_log(f"Unsupported exchange {req.exchange}")
            return

        # Filtering of duplicate subscriptions
        if req.vt_symbol in self.subscribed:
            return
        self.subscribed[req.vt_symbol] = req

        # Analyzing the details of IB contracts
        ib_contract: Contract = generate_ib_contract(req.symbol, req.exchange)
        if not ib_contract:
            self.gateway.write_log("Code parsing failed, please check if the format is correct")
            return

        # Query contract information via TWS
        self.reqid += 1
        self.client.reqContractDetails(self.reqid, ib_contract)

        #  Subscribe to tick data and create a buffer of tick objects
        self.reqid += 1
        self.client.reqMktData(self.reqid, ib_contract, "", False, False, [])

        tick: TickData = TickData(
            symbol=req.symbol,
            exchange=req.exchange,
            datetime=datetime.now(LOCAL_TZ),
            gateway_name=self.gateway_name,
        )
        self.ticks[self.reqid] = tick
        self.tick_exchange[self.reqid] = req.exchange

    def send_order(self, req: OrderRequest) -> str:
        """Place an order"""
        if not self.status:
            return ""

        if req.exchange not in EXCHANGE_VT2IB:
            self.gateway.write_log(f"Unsupported exchange: {req.exchange}")
            return ""

        if req.type not in ORDERTYPE_VT2IB:
            self.gateway.write_log(f"Unsupported price type: {req.type}")
            return ""

        self.orderid += 1

        ib_contract: Contract = generate_ib_contract(req.symbol, req.exchange)
        if not ib_contract:
            return ""

        ib_order: Order = Order()
        ib_order.orderId = self.orderid
        ib_order.clientId = self.clientid
        ib_order.action = DIRECTION_VT2IB[req.direction]
        ib_order.orderType = ORDERTYPE_VT2IB[req.type]
        ib_order.totalQuantity = req.volume
        ib_order.account = self.account

        # Fix the problem of delegate error due to API version upgrade
        ib_order.eTradeOnly = False
        ib_order.firmQuoteOnly = False

        if req.type == OrderType.LIMIT:
            ib_order.lmtPrice = req.price
        elif req.type == OrderType.STOP:
            ib_order.auxPrice = req.price

        self.client.placeOrder(self.orderid, ib_contract, ib_order)
        self.client.reqIds(1)

        order: OrderData = req.create_order_data(str(self.orderid), self.gateway_name)
        self.gateway.on_order(order)
        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """Order cancellation"""
        if not self.status:
            return

        self.client.cancelOrder(int(req.orderid))

    def query_history(self, req: HistoryRequest) -> List[BarData]:
        """Query historical data"""
        contract: ContractData = self.contracts[req.vt_symbol]
        if not contract:
            self.write_log(f"Contract not found: {req.vt_symbol}, please subscribe first")
            return []

        self.history_req = req

        self.reqid += 1

        ib_contract: Contract = generate_ib_contract(req.symbol, req.exchange)

        if req.end:
            end: datetime = req.end
            end_str: str = end.strftime("%Y%m%d %H:%M:%S")
        else:
            end: datetime = datetime.now(LOCAL_TZ)
            end_str: str = ""

        delta: timedelta = end - req.start
        days: int = min(delta.days, 180)     # IB 6 months data only
        duration: str = f"{days} D"
        bar_size: str = INTERVAL_VT2IB[req.interval]

        if contract.product in [Product.SPOT, Product.FOREX]:
            bar_type: str = "MIDPOINT"
        else:
            bar_type: str = "TRADES"

        self.history_reqid = self.reqid
        self.client.reqHistoricalData(
            self.reqid,
            ib_contract,
            end_str,
            duration,
            bar_size,
            bar_type,
            0,
            1,
            False,
            []
        )

        self.history_condition.acquire()    # Wait for asynchronous data to return
        self.history_condition.wait()
        self.history_condition.release()

        history: List[BarData] = self.history_buf
        self.history_buf: List[BarData] = []       # New buffer list
        self.history_req: HistoryRequest = None

        return history

    def load_contract_data(self) -> None:
        """Loading local contract data"""
        f = shelve.open(self.data_filepath)
        self.contracts = f.get("contracts", {})
        f.close()

        for contract in self.contracts.values():
            self.gateway.on_contract(contract)

        self.gateway.write_log("Local cached contract information loaded successfully")

    def save_contract_data(self) -> None:
        """Save contract data locally"""
        f = shelve.open(self.data_filepath)
        f["contracts"] = self.contracts
        f.close()


def generate_ib_contract(symbol: str, exchange: Exchange) -> Optional[Contract]:
    """Produce IB contract"""
    try:
        fields: list = symbol.split(JOIN_SYMBOL)

        ib_contract: Contract = Contract()
        ib_contract.exchange = EXCHANGE_VT2IB[exchange]
        ib_contract.secType = fields[-1]
        ib_contract.currency = fields[-2]
        ib_contract.symbol = fields[0]

        if ib_contract.secType in ["FUT", "OPT", "FOP"]:
            ib_contract.lastTradeDateOrContractMonth = fields[1]

        if ib_contract.secType == "FUT":
            if len(fields) == 5:
                ib_contract.multiplier = int(fields[2])

        if ib_contract.secType in ["OPT", "FOP"]:
            ib_contract.right = fields[2]
            ib_contract.strike = float(fields[3])
            ib_contract.multiplier = int(fields[4])
    except IndexError:
        ib_contract = None

    return ib_contract


def generate_symbol(ib_contract: Contract) -> str:
    """Generate vnpy code"""
    fields: list = [ib_contract.symbol]

    if ib_contract.secType in ["FUT", "OPT", "FOP"]:
        fields.append(ib_contract.lastTradeDateOrContractMonth)

    if ib_contract.secType in ["OPT", "FOP"]:
        fields.append(ib_contract.right)
        fields.append(str(ib_contract.strike))
        fields.append(str(ib_contract.multiplier))

    fields.append(ib_contract.currency)
    fields.append(ib_contract.secType)

    symbol: str = JOIN_SYMBOL.join(fields)

    return symbol
