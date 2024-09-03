import ccxt
import ccxt.async_support as ccxt_async
from devtools import debug

from exchange.model import MarketOrder, OrderBase, LimitOrder, ChangeSLOrder
import exchange.error as error
from decimal import Decimal
import time

class Okx:
    def __init__(self, key, secret, passphrase):
        self.client = ccxt.okx(
            {
                "apiKey": key,
                "secret": secret,
                "password": passphrase,
            }
        )
        self.client.load_markets()
        self.order_info: MarketOrder = None
        self.position_mode = "one-way"

    def init_info(self, order_info: MarketOrder):
        self.order_info = order_info

        unified_symbol = order_info.unified_symbol
        market = self.client.market(unified_symbol)

        is_contract = market.get("contract")
        if is_contract:
            order_info.is_contract = True
            order_info.contract_size = market.get("contractSize")

        if order_info.is_futures:
            self.client.options["defaultType"] = "swap"
        else:
            self.client.options["defaultType"] = "spot"

    def get_amount_precision(self, symbol):
        market = self.client.market(symbol)
        precision = market.get("precision")
        if (
            precision is not None
            and isinstance(precision, dict)
            and "amount" in precision
        ):
            return precision.get("amount")

    def get_contract_size(self, symbol):
        market = self.client.market(symbol)
        return market.get("contractSize")

    def parse_symbol(self, base: str, quote: str):
        if self.order_info.is_futures:
            return f"{base}/{quote}:{quote}"
        else:
            return f"{base}/{quote}"

    def get_ticker(self, symbol: str):
        return self.client.fetch_ticker(symbol)

    def get_price(self, symbol: str):
        return self.get_ticker(symbol)["last"]

    def get_balance(self, base: str):
        free_balance_by_base = None
        if self.order_info.is_entry or (
            self.order_info.is_spot
            and (self.order_info.is_buy or self.order_info.is_sell)
        ):
            free_balance = (
                self.client.fetch_free_balance()
                if not self.order_info.is_total
                else self.client.fetch_total_balance()
            )
            free_balance_by_base = free_balance.get(base)

        if free_balance_by_base is None or free_balance_by_base == 0:
            raise error.FreeAmountNoneError()
        return free_balance_by_base

    def get_futures_position(self, symbol=None, all=False):
        if symbol is None and all:
            positions = self.client.fetch_balance()["info"]["positions"]
            positions = [
                position
                for position in positions
                if float(position["positionAmt"]) != 0
            ]
            return positions

        positions = self.client.fetch_positions([symbol])
        long_contracts = None
        short_contracts = None
        if positions:
            for position in positions:
                if position["side"] == "long":
                    long_contracts = position["contracts"]
                elif position["side"] == "short":
                    short_contracts = position["contracts"]

            if self.order_info.is_close and self.order_info.is_buy:
                if not short_contracts:
                    raise error.ShortPositionNoneError()
                else:
                    return short_contracts
            elif self.order_info.is_close and self.order_info.is_sell:
                if not long_contracts:
                    raise error.LongPositionNoneError()
                else:
                    return long_contracts
        else:
            raise error.PositionNoneError()

    def get_amount(self, order_info: MarketOrder) -> float:
        if order_info.amount is not None and order_info.percent is not None:
            raise error.AmountPercentBothError()
        elif order_info.amount is not None:
            if order_info.is_contract:
                result = self.client.amount_to_precision(
                    order_info.unified_symbol,
                    float(
                        Decimal(str(order_info.amount))
                        // Decimal(str(order_info.contract_size))
                    ),
                )

            else:
                result = order_info.amount
        elif order_info.percent is not None:
            if self.order_info.is_entry or (order_info.is_spot and order_info.is_buy):
                if order_info.is_coinm:
                    free_base = self.get_balance(order_info.base)
                    if order_info.is_contract:
                        result = (
                            free_base * (order_info.percent - 0.5) / 100
                        ) // order_info.contract_size
                    else:
                        result = free_base * order_info.percent / 100
                else:
                    free_quote = self.get_balance(order_info.quote)
                    cash = free_quote * (order_info.percent - 0.5) / 100
                    current_price = self.get_price(order_info.unified_symbol)
                    if order_info.is_contract:
                        result = (cash / current_price) // order_info.contract_size
                    else:
                        result = cash / current_price
            elif self.order_info.is_close:
                if order_info.is_contract:
                    free_amount = self.get_futures_position(order_info.unified_symbol)
                    result = free_amount * order_info.percent / 100
                else:
                    free_amount = self.get_futures_position(order_info.unified_symbol)
                    result = free_amount * float(order_info.percent) / 100

            elif order_info.is_spot and order_info.is_sell:
                free_amount = self.get_balance(order_info.base)
                result = free_amount * float(order_info.percent) / 100

            result = float(
                self.client.amount_to_precision(order_info.unified_symbol, result)
            )
            order_info.amount_by_percent = result
        else:
            raise error.AmountPercentNoneError()

        return float(result)

    def market_order(self, order_info: MarketOrder):
        from exchange.pexchange import retry

        symbol = (
            order_info.unified_symbol
        )  # self.parse_symbol(order_info.base, order_info.quote)
        params = {"tgtCcy": "base_ccy"}

        try:
            return retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                order_info.amount,
                order_info.price,
                params,
                order_info=order_info,
                max_attempts=5,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            raise error.OrderError(e, self.order_info)

    def get_position(self, symbol):
        positions = self.client.fetch_positions([symbol])
        for position in positions:
            if position['symbol'] == symbol:
                return position
        return None

    def get_stop_orders(self, symbol):
        open_orders = self.client.fetch_open_orders(symbol)
        return [order for order in open_orders if order['type'].lower() == 'stop']

    def cancel_order(self, order_id, symbol):
        return self.client.cancel_order(order_id, symbol)

    def create_stop_order(self, symbol, side, amount, price):
        return self.client.create_order(
            symbol=symbol,
            type='STOP_MARKET',
            side=side,
            amount=abs(amount),
            price=None, #이걸 , None이라고 하는 게 기존 코드였어서 그대로 사용. 혹시 에러가 발생하면, price로 교체. 
            params={'stopPrice': price, 'reduceOnly': True}
        )

    def change_sl_order(self, order_info: ChangeSLOrder):
        try:
            symbol = self.order_info.unified_symbol
            position = self.get_position(symbol)
            if not position or position['amount'] == 0:
                print(f"No open position for {symbol}")
                return None
            
            stop_orders = self.get_stop_orders(symbol)
            
            for order in stop_orders:
                self.cancel_order(order['id'], symbol)
                print(f"Cancelled existing stop order: {order['id']}")
                
            side = 'sell' if position['amount'] > 0 else 'buy'
            new_stop_price = position['entryPrice']
            
            new_stop_order = self.create_stop_order(
                symbol,
                side,
                position['amount'],
                new_stop_price
            )
            
            print(f"Created new stop order at entry price: {new_stop_price}")
            return new_stop_order

        except Exception as e:
            print(f"Error in change_sl_order: {str(e)}")
            raise
    
    
    def create_sl_order_with_retry(self, symbol, sl_side, entry_amount, sl_price, params):
        max_retries = 5
        retry_delay = 0.2

        for attempt in range(max_retries):
            try:
                sl_order = self.client.create_order(
                    symbol=symbol,
                    type='stop_market',
                    side=sl_side,
                    amount=abs(entry_amount),
                    price=None,
                    params={
                        'stopPrice': sl_price,
                        'reduceOnly': True,
                    }
                )
                return sl_order  # 성공 시 주문 정보 반환
            except Exception as e:
                if attempt < max_retries - 1:  # 마지막 시도가 아닌 경우
                    print(f"SL 주문 생성 실패 (시도 {attempt + 1}/{max_retries}): {str(e)}")
                    time.sleep(retry_delay)  # 다음 시도 전 0.2초 대기
                else:
                    print(f"최대 재시도 횟수 도달. SL 주문 생성 실패: {str(e)}")
                    raise  # 모든 시도 실패 시 예외 발생

        return None 

    def limit_entry(self, order_info: LimitOrder):
        from exchange.pexchange import retry
        symbol = order_info.unified_symbol
        entry_amount = self.get_amount(order_info)
        if entry_amount == 0:
            raise error.MinAmountError()
        
        params = self._get_position_params(order_info)
        
        if order_info.leverage is not None:
            self.set_leverage(order_info.leverage, symbol)
        
        try:
            result = retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                abs(entry_amount),
                order_info.price,
                params,
                order_info=order_info,
                max_attempts=10,
                delay=0.1,
                instance=self,
            )
            return result
        except Exception as e:
            raise error.OrderError(e, self.order_info)

    def market_buy(
        self,
        order_info: MarketOrder,
    ):
        # 수량기반
        buy_amount = self.get_amount(order_info)
        fee = self.client.fetch_trading_fee(self.order_info.unified_symbol)
        order_info.amount = buy_amount
        result = self.market_order(order_info)
        order_info.amount = buy_amount * (1 - fee["taker"])
        return result

    def market_sell(
        self,
        order_info: MarketOrder,
    ):
        # 수량기반
        symbol = (
            order_info.unified_symbol
        )  # self.parse_symbol(order_info.base, order_info.quote)
        fee = self.client.fetch_trading_fee(symbol)
        sell_amount = self.get_amount(order_info)

        if order_info.percent is not None:
            order_info.amount = sell_amount
        else:
            order_info.amount = sell_amount * (1 - fee["taker"])

        return self.market_order(order_info)

    def _get_position_params(self, order_info: OrderBase):
        params = {}
        
        # OKX uses 'cross' or 'isolated' for tdMode
        if self.position_mode == "one-way":
            params["tdMode"] = "cross"
        elif self.position_mode == "hedge":
            params["tdMode"] = "isolated"
        
        # Set posSide for hedge mode
        if self.position_mode == "hedge":
            if order_info.side == "buy":
                params["posSide"] = "long"
            elif order_info.side == "sell":
                params["posSide"] = "short"
        
        # Add additional parameters specific to OKX if needed
        # For example, you might need to set 'reduceOnly' for close orders
        if hasattr(order_info, 'is_close') and order_info.is_close:
            params["reduceOnly"] = True

        return params

    def set_leverage(self, leverage, symbol):
        if self.order_info.is_futures:
            if self.order_info.is_futures and self.order_info.is_entry:
                if self.order_info.is_buy:
                    pos_side = "long"
                elif self.order_info.is_sell:
                    pos_side = "short"
            try:
                if (
                    self.order_info.margin_mode is None
                    or self.order_info.margin_mode == "isolated"
                ):
                    if self.position_mode == "hedge":
                        self.client.set_leverage(
                            leverage,
                            symbol,
                            params={"mgnMode": "isolated", "posSide": pos_side},
                        )
                    elif self.position_mode == "one-way":
                        self.client.set_leverage(
                            leverage,
                            symbol,
                            params={"mgnMode": "isolated", "posSide": "net"},
                        )
                else:
                    self.client.set_leverage(
                        leverage,
                        symbol,
                        params={"mgnMode": self.order_info.margin_mode},
                    )
            except Exception as e:
                pass

    def market_entry(
        self,
        order_info: MarketOrder,
    ):
        from exchange.pexchange import retry

        symbol = (
            order_info.unified_symbol
        )  # self.parse_symbol(order_info.base, order_info.quote)
        use_tp1 = order_info.use_tp1
        use_tp2 = order_info.use_tp2
        use_tp3 = order_info.use_tp3
        use_tp4 = order_info.use_tp4
        use_sl = order_info.use_sl
        if use_tp1:
            tp1_price = order_info.tp1_price
        else:
            tp1_price = None
        if use_tp2:
            tp2_price = order_info.tp2_price
        else:
            tp2_price = None
        if use_tp3:
            tp3_price = order_info.tp3_price
        else:
            tp3_price = None
        if use_tp4:
            tp4_price = order_info.tp4_price
        else:
            tp4_price = None
        if use_sl:
            sl_price = order_info.sl_price
        else:
            sl_price = None
        tp_data = [
            (order_info.use_tp1, order_info.tp1_price, order_info.tp1_qty_percent),
            (order_info.use_tp2, order_info.tp2_price, order_info.tp2_qty_percent),
            (order_info.use_tp3, order_info.tp3_price, order_info.tp3_qty_percent),
            (order_info.use_tp4, order_info.tp4_price, order_info.tp4_qty_percent),
        ]
        entry_amount = self.get_amount(order_info)
        if entry_amount == 0:
            raise error.MinAmountError()

        params = {}
        if order_info.leverage is None:
            self.set_leverage(1, symbol)
        else:
            self.set_leverage(order_info.leverage, symbol)
        if order_info.margin_mode is None:
            params |= {"tdMode": "isolated"}
        else:
            params |= {"tdMode": order_info.margin_mode}

        if self.position_mode == "one-way":
            params |= {}
        elif self.position_mode == "hedge":
            if order_info.is_futures and order_info.side == "buy":
                if order_info.is_entry:
                    pos_side = "long"
                elif order_info.is_close:
                    pos_side = "short"
            elif order_info.is_futures and order_info.side == "sell":
                if order_info.is_entry:
                    pos_side = "short"
                elif order_info.is_close:
                    pos_side = "long"
            params |= {"posSide": pos_side}

        try:
            # 메인 주문 생성
            result = retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                abs(entry_amount),
                None,
                params,
                order_info=order_info,
                max_attempts=10,
                delay=0.1,
                instance=self,
            )

            # TP 주문 생성 (reduce-only)
            for use_tp, tp_price, tp_qty_percent in tp_data:
                if use_tp and tp_price and tp_qty_percent:
                    tp_side = "sell" if order_info.side == "buy" else "buy"
                    tp_amount = abs(entry_amount) * (tp_qty_percent / 100)
                    tp_params = {
                        "reduceOnly": True,
                        #"tdMode": params["tdMode"]
                    }
                    retry(
                        self.client.create_order,
                        symbol,
                        "limit",
                        tp_side,
                        tp_amount,
                        tp_price,
                        tp_params,
                        order_info=order_info,
                        max_attempts=10,
                        delay=0.1,
                        instance=self,
                    )

            # SL 주문 생성 (reduce-only)
            if sl_price:
                sl_side = "sell" if order_info.side == "buy" else "buy"
                sl_params = {
                    "reduceOnly": True,
                    "tdMode": params["tdMode"],
                    "stopLossPrice": sl_price
                }
                retry(
                    self.client.create_order,
                    symbol,
                    "market",
                    sl_side,
                    abs(entry_amount),
                    None,
                    sl_params,
                    order_info=order_info,
                    max_attempts=10,
                    delay=0.1,
                    instance=self,
                )

            return result
        except Exception as e:
            raise error.OrderError(e, self.order_info)

    def market_close(
        self,
        order_info: MarketOrder,
    ):
        from exchange.pexchange import retry

        symbol = self.order_info.unified_symbol
        close_amount = self.get_amount(order_info)

        if self.position_mode == "one-way":
            if (
                self.order_info.margin_mode is None
                or self.order_info.margin_mode == "isolated"
            ):
                params = {"reduceOnly": True, "tdMode": "isolated"}
            elif self.order_info.margin_mode == "cross":
                params = {"reduceOnly": True, "tdMode": "cross"}

        elif self.position_mode == "hedge":
            if order_info.is_futures and order_info.side == "buy":
                if order_info.is_entry:
                    pos_side = "long"
                elif order_info.is_close:
                    pos_side = "short"
            elif order_info.is_futures and order_info.side == "sell":
                if order_info.is_entry:
                    pos_side = "short"
                elif order_info.is_close:
                    pos_side = "long"
            if (
                self.order_info.margin_mode is None
                or self.order_info.margin_mode == "isolated"
            ):
                params = {"posSide": pos_side, "tdMode": "isolated"}
            elif self.order_info.margin_mode == "cross":
                params = {"posSide": pos_side, "tdMode": "cross"}

        try:
            return retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                abs(close_amount),
                None,
                params,
                order_info=order_info,
                max_attempts=5,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            raise error.OrderError(e, self.order_info)
