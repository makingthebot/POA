from exchange.pexchange import ccxt, ccxt_async, httpx
from devtools import debug
from exchange.model import MarketOrder, OrderBase, LimitOrder, ChangeSLOrder
import exchange.error as error
import time


class Binance:
    def __init__(self, key, secret):
        self.client = ccxt.binance(
            {
                "apiKey": key,
                "secret": secret,
                "options": {"adjustForTimeDifference": True},
            }
        )
        self.client.load_markets()
        self.position_mode = "one-way"
        self.order_info: OrderBase = None
    def create_order_with_retry(self, symbol, tp_side, tp_amount, tp_price):
        max_retries = 5
        retry_delay = 0.2

        for attempt in range(max_retries):
            try:
                tp_order = self.client.create_order(
                    symbol=symbol,
                    type='limit',
                    side=tp_side,
                    amount=abs(tp_amount),
                    price=tp_price,
                    params={'reduceOnly': True}
                )
                return tp_order  # 성공 시 주문 정보 반환
            except Exception as e:
                if attempt < max_retries - 1:  # 마지막 시도가 아닌 경우
                    print(f"주문 생성 실패 (시도 {attempt + 1}/{max_retries}): {str(e)}")
                    time.sleep(retry_delay)  # 다음 시도 전 0.2초 대기
                else:
                    print(f"최대 재시도 횟수 도달. 주문 생성 실패: {str(e)}")
                    raise  # 모든 시도 실패 시 예외 발생
        return None
    
    def get_position(self, symbol):
        positions = self.client.fetch_positions([symbol])
        for position in positions:
            if position['symbol'] == symbol:
                return position
        return None
    def is_stop_order(self, order):
        order_type = order['type'].upper()
        stop_types = ['STOP', 'STOP_MARKET', 'STOP_LOSS', 'STOP_LOSS_LIMIT', 'TAKE_PROFIT', 'TAKE_PROFIT_MARKET']
        return order_type in stop_types or 'STOP' in order_type    

    def get_stop_orders(self, symbol):
        try:
            open_orders = self.client.fetch_open_orders(symbol)
        except Exception as e:
            print(f"Error fetching open orders: {str(e)}")
            return []
        return [order for order in open_orders if self.is_stop_order(order)]

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
            print("Changing SL order")
            symbol = order_info.unified_symbol
            sl_price = order_info.sl_price
            position = self.get_position(symbol)
            position_amt = float(position['info'].get('positionAmt', 0))
            print(f"Position: {position}")
            if not position or position_amt == 0:
                print(f"No open position for {symbol}")
                return None

            try:
                stop_orders = self.get_stop_orders(symbol)
                if not stop_orders:
                    pass
                    print(f"No existing stop orders for {symbol}")
                else:    
                    print(f"Stop orders: {stop_orders}")
                    for order in stop_orders:
                        self.cancel_order(order['id'], symbol)
                        print(f"Cancelled existing stop order: {order['id']}")
            except Exception as e:
                print(f"Error cancelling existing stop orders: {str(e)}")
                
            side = 'sell' if position_amt > 0 else 'buy'
            if sl_price is None:
                new_stop_price = position['entryPrice']
            else:
                new_stop_price = sl_price
            try:
                new_stop_order = self.create_stop_order(
                    symbol,
                    side,
                    abs(position_amt),
                    new_stop_price
                )
                
                print(f"Created new stop order at entry price: {new_stop_price}")
                return new_stop_order

            except Exception as stop_order_error:
                print(f"Failed to create new stop order: {str(stop_order_error)}")
                print("Attempting to close position with market order")
                
                try:
                    market_close_order = self.client.create_market_order(
                        symbol=symbol,
                        side=side,
                        amount=abs(position_amt),
                        params={'reduceOnly': True}
                    )
                    print(f"Closed position with market order: {market_close_order}")
                    return market_close_order
                except Exception as market_order_error:
                    print(f"Failed to close position with market order: {str(market_order_error)}")
                    raise
        
        except Exception as e:
            print(f"Error in change_sl_order: {str(e)}")
            raise
    
    
    def create_sl_order_with_retry(self, symbol, sl_side, entry_amount, sl_price, params):
        max_retries = 5
        retry_delay = 0.2
        print('SL 주문 생성 retry 로직. sl_side : ', sl_side)
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

    def init_info(self, order_info: MarketOrder):
        self.order_info = order_info

        unified_symbol = order_info.unified_symbol
        market = self.client.market(unified_symbol)

        if order_info.amount is not None:
            order_info.amount = float(
                self.client.amount_to_precision(
                    order_info.unified_symbol, order_info.amount
                )
            )

        if order_info.is_futures:
            if order_info.is_coinm:
                is_contract = market.get("contract")
                if is_contract:
                    order_info.is_contract = True
                    order_info.contract_size = market.get("contractSize")
                self.client.options["defaultType"] = "delivery"
            else:
                self.client.options["defaultType"] = "swap"
        else:
            self.client.options["defaultType"] = "spot"

    def get_ticker(self, symbol: str):
        return self.client.fetch_ticker(symbol)

    def get_price(self, symbol: str):
        return self.get_ticker(symbol)["last"]

    def get_futures_position(self, symbol=None, all=False):
        if symbol is None and all:
            positions = self.client.fetch_balance()["info"]["positions"]
            positions = [
                position
                for position in positions
                if float(position["positionAmt"]) != 0
            ]
            return positions

        positions = None
        if self.order_info.is_coinm:
            positions = self.client.fetch_balance()["info"]["positions"]
            positions = [
                position
                for position in positions
                if float(position["positionAmt"]) != 0
                and position["symbol"] == self.client.market(symbol).get("id")
            ]
        else:
            positions = self.client.fetch_positions(symbols=[symbol])

        long_contracts = None
        short_contracts = None
        if positions:
            if self.order_info.is_coinm:
                for position in positions:
                    amt = float(position["positionAmt"])
                    if position["positionSide"] == "LONG":
                        long_contracts = amt
                    elif position["positionSide"] == "SHORT":
                        short_contracts: float = amt
                    elif position["positionSide"] == "BOTH":
                        if amt > 0:
                            long_contracts = amt
                        elif amt < 0:
                            short_contracts = abs(amt)
            else:
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

    def get_amount(self, order_info: MarketOrder) -> float:
        if order_info.amount is not None and order_info.percent is not None:
            raise error.AmountPercentBothError()
        elif order_info.amount is not None:
            if order_info.is_contract:
                current_price = self.get_price(order_info.unified_symbol)
                result = (order_info.amount * current_price) // order_info.contract_size
            else:
                result = order_info.amount
        elif order_info.percent is not None:
            if order_info.is_entry or (order_info.is_spot and order_info.is_buy):
                if order_info.is_coinm:
                    free_base = self.get_balance(order_info.base)
                    if order_info.is_contract:
                        current_price = self.get_price(order_info.unified_symbol)
                        result = (
                            free_base * order_info.percent / 100 * current_price
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

        return result

    def set_leverage(self, leverage, symbol):
        if self.order_info.is_futures:
            self.client.set_leverage(leverage, symbol)

    def market_order(self, order_info: MarketOrder):
        from exchange.pexchange import retry

        symbol = order_info.unified_symbol  # self.parse_symbol(base, quote)
        params = {}
        try:
            return retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                order_info.amount,
                None,
                params,
                order_info=order_info,
                max_attempts=5,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            print(f"Error creating market order: {str(e)}")
            #raise error.OrderError(e, self.order_info)

    def limit_order(self, order_info: LimitOrder):
        from exchange.pexchange import retry
        symbol = order_info.unified_symbol
        params = {}
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
            print(f"Error creating limit order: {str(e)}")
            raise 

    # async def market_order_async(
    #     self,
    #     base: str,
    #     quote: str,
    #     type: str,
    #     side: str,
    #     amount: float,
    #     price: float = None,
    # ):
    #     symbol = self.parse_symbol(base, quote)
    #     return await self.spot_async.create_order(
    #         symbol, type.lower(), side.lower(), amount
    #     )

    def market_buy(self, order_info: MarketOrder):
        # 수량기반
        buy_amount = self.get_amount(order_info)
        order_info.amount = buy_amount

        return self.market_order(order_info)

    def market_sell(self, order_info: MarketOrder):
        sell_amount = self.get_amount(order_info)
        order_info.amount = sell_amount
        return self.market_order(order_info)

    def market_entry(
        self,
        order_info: MarketOrder,
    ):
        from exchange.pexchange import retry

        # self.client.options["defaultType"] = "swap"
        symbol = self.order_info.unified_symbol  # self.parse_symbol(base, quote)
        print('order 호출 1')
        entry_amount = self.get_amount(order_info)
        use_tp1 = order_info.use_tp1
        use_tp2 = order_info.use_tp2
        use_tp3 = order_info.use_tp3
        use_tp4 = order_info.use_tp4
        use_sl = order_info.use_sl
        try:
            if order_info.entry_price is not None:
                entry_price = order_info.entry_price
            else:
                entry_price = None
        except:
            entry_price = None
        
        round_info = 2
        if "SOL" in symbol:
            round_info = 0
        elif "BTC" in symbol:
            round_info = 3
        if use_tp1:
            tp1_price = order_info.tp1_price
            tp1_qty_percent = order_info.tp1_qty_percent
        else:
            tp1_price = None
            tp1_qty_percent = None
        if use_tp2:
            tp2_price = order_info.tp2_price
            tp2_qty_percent = order_info.tp2_qty_percent
        else:
            tp2_price = None
            tp2_qty_percent = None
        if use_tp3:
            tp3_price = order_info.tp3_price
            tp3_qty_percent = order_info.tp3_qty_percent
        else:
            tp3_price = None
            tp3_qty_percent = None
        if use_tp4:
            tp4_price = order_info.tp4_price
            tp4_qty_percent = order_info.tp4_qty_percent
        else:
            tp4_price = None
            tp4_qty_percent = None
        tp_prices = [
        order_info.tp1_price if use_tp1 else None,
        order_info.tp2_price if use_tp2 else None,
        order_info.tp3_price if use_tp3 else None,
        order_info.tp4_price if use_tp4 else None,
    ]
        tp_data = [
        (order_info.use_tp1, order_info.tp1_price, order_info.tp1_qty_percent),
        (order_info.use_tp2, order_info.tp2_price, order_info.tp2_qty_percent),
        (order_info.use_tp3, order_info.tp3_price, order_info.tp3_qty_percent),
        (order_info.use_tp4, order_info.tp4_price, order_info.tp4_qty_percent),
    ]

        sl_price = order_info.sl_price
        if entry_amount == 0:
            raise error.MinAmountError()
        if self.position_mode == "one-way":
            params = {}
        elif self.position_mode == "hedge":
            if order_info.side == "buy":
                if order_info.is_entry:
                    positionSide = "LONG"
                elif order_info.is_close:
                    positionSide = "SHORT"
            elif order_info.side == "sell":
                if order_info.is_entry:
                    positionSide = "SHORT"
                elif order_info.is_close:
                    positionSide = "LONG"
            params = {"positionSide": positionSide}
        if order_info.leverage is not None:
            self.set_leverage(order_info.leverage, symbol)

        try:
            print('order 호출 2')
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
            print(result)
            time.sleep(1)
            print('order 호출 3')
            # 진입 주문이 성공적으로 실행된 후 기존 SL 주문 취소
            cancelled_sl_orders = self.cancel_sl_order(symbol)
            print(f"Cancelled SL orders: {cancelled_sl_orders}")
            tp1_qty = 0.0
            tp2_qty = 0.0
            tp3_qty = 0.0
            tp4_qty = 0.0
            tp_quantities = [0.0, 0.0, 0.0, 0.0]
            used_amount = 0.0
        
            
            tp_percentages = [order_info.tp1_qty_percent, order_info.tp2_qty_percent, order_info.tp3_qty_percent, order_info.tp4_qty_percent]
            use_tp_flags = [use_tp1, use_tp2, use_tp3, use_tp4]
            print(f"TP percentages: {tp_percentages}")
            print(f"Use TP flags: {use_tp_flags}")
            for i, (qty_percent, use_tp) in enumerate(zip(tp_percentages, use_tp_flags)):
                if qty_percent is not None and use_tp:
                    tp_qty = round(abs(entry_amount) * (qty_percent / 100), round_info)
                    used_amount += tp_qty
                    tp_quantities[i] = tp_qty
                    print(f"TP{i+1} quantity: {tp_qty}")
            
            remaining_qty = abs(entry_amount) - used_amount
            if remaining_qty > 0:
                for i in range(3, -1, -1):
                    if use_tp_flags[i]:
                        tp_quantities[i] += remaining_qty
                        print(f"Added remaining {remaining_qty} to TP{i+1}")
                        break
        
            try:
                print(f"Final TP quantities: {tp_quantities}")
                # TP 주문 생성 (reduce-only)
                tp_count = 0

                for use_tp, tp_price, tp_qty in zip(use_tp_flags, tp_data, tp_quantities):
                    print(f"Processing TP: use_tp={use_tp}, tp_price={tp_price}, tp_qty={tp_qty}")
                    tp_price = tp_price[1] if isinstance(tp_price, tuple) else tp_price
                    if use_tp and tp_price and tp_qty > 0:
                        tp_side = "buy" if order_info.side == "sell" else "sell"
                        positionSide = "SHORT" if order_info.side == "sell" else "LONG"
                        tp_params = {
                            "reduceOnly": True,
                            "positionSide": positionSide
                        }
                        print(f"Creating TP order: side={tp_side}, qty={tp_qty}, price={tp_price}, params={tp_params}")
                        tp_order = self.create_order_with_retry(symbol, tp_side, tp_qty, tp_price)
                        tp_count += 1
                        print(f"TP {tp_count} order created: {tp_order}")
                    else:
                        print(f"Skipping TP order: use_tp={use_tp}, tp_price={tp_price}, tp_qty={tp_qty}")
            except Exception as e:
                print(f"Error creating TP orders: {e}")
                #raise error.OrderError(e, self.order_info)
            try:
                # SL 주문 생성 (reduce-only)
                if sl_price:
                    print('SL 주문 생성')
                    sl_side = "sell" if order_info.side == "buy" else "buy"
                    sl_params = {
                        "reduceOnly": True,
                        "positionSide": params.get("positionSide", None)
                    }
                    sl_order = self.create_sl_order_with_retry(symbol, sl_side, abs(entry_amount), sl_price, {'stopPrice': sl_price, 'reduceOnly': True})
            except Exception as e:
                print(f"Error creating SL order: {e}")
                #raise error.OrderError(e, self.order_info)


            return result
        except Exception as e:
            print(f"Error creating market entry order: {str(e)}")
            #raise error.OrderError(e, self.order_info)

    def limit_entry(self, order_info: LimitOrder):
        from exchange.pexchange import retry
        symbol = self.order_info.unified_symbol
        entry_amount = self.get_amount(order_info)
        if entry_amount == 0:
            raise error.MinAmountError()
        
        if self.position_mode == "one-way":
            params = {}
        elif self.position_mode == "hedge":
            if order_info.side == "buy":
                positionSide = "LONG" if order_info.is_entry else "SHORT"
            elif order_info.side == "sell":
                positionSide = "SHORT" if order_info.is_entry else "LONG"
            params = {"positionSide": positionSide}
        
        if order_info.leverage is not None:
            self.set_leverage(order_info.leverage, symbol)
        
        try:
            result = retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                abs(entry_amount),
                order_info.price,  # 리밋 주문에서는 가격을 지정
                params,
                order_info=order_info,
                max_attempts=10,
                delay=0.1,
                instance=self,
            )
            return result
        except Exception as e:
            print(f"Error creating limit entry order: {str(e)}")
            #raise error.OrderError(e, self.order_info)

    def is_hedge_mode(self):
        response = self.client.fapiPrivate_get_positionside_dual()
        if response["dualSidePosition"]:
            return True
        else:
            return False

    def cancel_sl_order(self, symbol):
        try:
            print(f"Cancelling SL orders for {symbol}")
            
            # 스톱 주문 조회
            stop_orders = self.get_stop_orders(symbol)
            print(f"Stop orders: {stop_orders}")
            
            cancelled_orders = []
            if not stop_orders:
                return cancelled_orders
            for order in stop_orders:
                try:
                    self.cancel_order(order['id'], symbol)
                    cancelled_orders.append(order['id'])
                    print(f"Cancelled stop order: {order['id']}")
                except Exception as cancel_error:
                    print(f"Error cancelling stop order {order['id']}: {str(cancel_error)}")
            
            if cancelled_orders:
                print(f"Successfully cancelled {len(cancelled_orders)} stop order(s) for {symbol}")
            else:
                print(f"No stop orders found to cancel for {symbol}")
            
            return cancelled_orders
        
        except Exception as e:
            print(f"Error in cancel_sl_order: {str(e)}")
            #raise e


    def market_sltp_order(
        self,
        base: str,
        quote: str,
        type: str,
        side: str,
        amount: float,
        stop_price: float,
        profit_price: float,
    ):
        symbol = self.order_info.unified_symbol  # self.parse_symbol(base, quote)
        inverted_side = (
            "sell" if side.lower() == "buy" else "buy"
        )  # buy면 sell, sell이면 buy * 진입 포지션과 반대로 주문 넣어줘 야함
        self.client.create_order(
            symbol,
            "STOP_MARKET",
            inverted_side,
            amount,
            None,
            {"stopPrice": stop_price, "newClientOrderId": "STOP_MARKET"},
        )  # STOP LOSS 오더
        self.client.create_order(
            symbol,
            "TAKE_PROFIT_MARKET",
            inverted_side,
            amount,
            None,
            {"stopPrice": profit_price, "newClientOrderId": "TAKE_PROFIT_MARKET"},
        )  # TAKE profit 오더

        # response = self.future.private_post_order_oco({
        #     'symbol': self.future.market(symbol)['id'],
        #     'side': 'BUY',  # SELL, BUY
        #     'quantity': self.future.amount_to_precision(symbol, amount),
        #     'price': self.future.price_to_precision(symbol, profit_price),
        #     'stopPrice': self.future.price_to_precision(symbol, stop_price),
        #     # 'stopLimitPrice': self.future.price_to_precision(symbol, stop_limit_price),  # If provided, stopLimitTimeInForce is required
        #     # 'stopLimitTimeInForce': 'GTC',  # GTC, FOK, IOC
        #     # 'listClientOrderId': exchange.uuid(),  # A unique Id for the entire orderList
        #     # 'limitClientOrderId': exchange.uuid(),  # A unique Id for the limit order
        #     # 'limitIcebergQty': exchangea.amount_to_precision(symbol, limit_iceberg_quantity),
        #     # 'stopClientOrderId': exchange.uuid()  # A unique Id for the stop loss/stop loss limit leg
        #     # 'stopIcebergQty': exchange.amount_to_precision(symbol, stop_iceberg_quantity),
        #     # 'newOrderRespType': 'ACK',  # ACK, RESULT, FULL
        # })
        
    def market_all_close(self, order_info: MarketOrder):
        from exchange.pexchange import retry
        position_amt = None
        symbol = self.order_info.unified_symbol
        if self.position_mode == "one-way":
            params = {"reduceOnly": True}
        elif self.position_mode == "hedge":
            if order_info.side == "buy":
                if order_info.is_entry:
                    positionSide = "LONG"
                elif order_info.is_close:
                    positionSide = "SHORT"
            elif order_info.side == "sell":
                if order_info.is_entry:
                    positionSide = "SHORT"
                elif order_info.is_close:
                    positionSide = "LONG"
            params = {"positionSide": positionSide}
        try:
            position = self.get_position(symbol)
            position_amt = float(position['info'].get('positionAmt', 0))
        except Exception as e:
            print(str(e))
            return None
        try:
            return retry(
                self.client.create_order,
                symbol,
                order_info.type.lower(),
                order_info.side,
                abs(position_amt),
                None,
                params,
                order_info=order_info,
                max_attempts=10,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            print(f"Error creating all market closing order: {str(e)}")
            #raise error.OrderError(e, self.order_info)

    def market_close(
        self,
        order_info: MarketOrder,
    ):
        from exchange.pexchange import retry

        symbol = self.order_info.unified_symbol  # self.parse_symbol(base, quote)
        close_amount = self.get_amount(order_info)
        if self.position_mode == "one-way":
            params = {"reduceOnly": True}
        elif self.position_mode == "hedge":
            if order_info.side == "buy":
                if order_info.is_entry:
                    positionSide = "LONG"
                elif order_info.is_close:
                    positionSide = "SHORT"
            elif order_info.side == "sell":
                if order_info.is_entry:
                    positionSide = "SHORT"
                elif order_info.is_close:
                    positionSide = "LONG"
            params = {"positionSide": positionSide}

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
                max_attempts=10,
                delay=0.1,
                instance=self,
            )
        except Exception as e:
            print(f"Error creating market close order: {str(e)}")
            #raise error.OrderError(e, self.order_info)

    def get_listen_key(self):
        url = "https://fapi.binance.com/fapi/v1/listenKey"

        listenkey = httpx.post(
            url, headers={"X-MBX-APIKEY": self.client.apiKey}
        ).json()["listenKey"]
        return listenkey

    def get_trades(self):
        is_futures = self.order_info.is_futures
        if is_futures:
            trades = self.client.fetch_my_trades()
            print(trades)
