from exchange.model import MarketOrder
from devtools import debug


class AmountError(Exception):
    def __init__(self, msg="", *args, **kwargs):
        super().__init__(f"[수량 오류]\n{msg}", *args, **kwargs)


class AmountPercentNoneError(AmountError):
    def __init__(self, *args, **kwargs):
        msg = "amount와 percent 중 적어도 하나는 입력해야 합니다!"
        super().__init__(msg, *args, **kwargs)


class AmountPercentBothError(AmountError):
    def __init__(self, *args, **kwargs):
        msg = "amount와 percent는 동시에 입력할 수 없습니다!"
        super().__init__(msg, *args, **kwargs)


class FreeAmountNoneError(AmountError):
    def __init__(self, *args, **kwargs):
        msg = "거래할 수량이 없습니다"
        super().__init__(msg, *args, **kwargs)


class MinAmountError(AmountError):
    def __init__(self, *args, **kwargs):
        msg = "최소 거래 수량을 만족하지 못했습니다!"
        super().__init__(msg, *args, **kwargs)


class PositionError(Exception):
    def __init__(self, msg="", *args, **kwargs):
        super().__init__(f"[포지션 오류]\n{msg}", *args, **kwargs)


class PositionNoneError(PositionError):
    def __init__(self, msg="", *args, **kwargs):
        super().__init__(f"{msg} 포지션이 없습니다", *args, **kwargs)


class LongPositionNoneError(PositionNoneError):
    def __init__(self, *args, **kwargs):
        msg = "롱"
        super().__init__(msg, *args, **kwargs)


class ShortPositionNoneError(PositionNoneError):
    def __init__(self, *args, **kwargs):
        msg = "숏"
        super().__init__(msg, *args, **kwargs)


class OrderError(Exception):
    def __init__(self, msg="", order_info: MarketOrder = None, *args, **kwargs):
        side = ""
        print('🐳🐳')
        if order_info is not None:
            if order_info.is_futures:
                #try:
                #    if order_info.is_tp_order:
                #        side = "TP"
                #    elif order_info.is_sl_order:
                #        side = "SL"
                #except Exception as e:
                #    print(e)
                if order_info.is_entry:
                    if order_info.is_buy:
                        side = "롱 진입"
                    elif order_info.is_sell:
                        side = "숏 진입"
                elif order_info.is_close:
                    if order_info.is_buy:
                        side = "숏 종료"
                    elif order_info.is_sell:
                        side = "롱 종료"
                else:
                    side = "TP/SL"

            elif order_info.is_buy:
                side = "매수"
            elif order_info.is_sell:
                side = "매도"

        super().__init__(f"[{side} 주문 오류]\n{msg}", *args, **kwargs)
