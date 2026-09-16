"""Модуль расчета физико-химических свойств гидроочищенного дизельного топлива (ГО ДТ).

Определяет плотность D15, фракционный состав (T95), низкотемпературные свойства (ПТФ / CFPP)
и цетановое число (ЦЧ / CN) стабильного гидрогенизата.
"""

from __future__ import annotations

from typing import Dict
from src.twin.feed_link import FeedState
from src.twin.params import ProductParams


def product_properties(feed: FeedState, p: ProductParams) -> Dict[str, float]:
    """
    Расчет физико-химических показателей продукта 24-2000 по состоянию сырьевого потока.

    :param feed: Состояние сырья после FeedLink (T95, S, D15).
    :param p: Калибровочные параметры продукта.
    :return: Словарь расчетных тегов продукта HT_*.
    """
    dt95 = feed.t95_feed_c - p.t95_feed_ref

    return {
        "HT_D15_PRODUCT": feed.d15_feed - p.delta_d15_hdt,
        "HT_T95_PRODUCT": feed.t95_feed_c - p.delta_t95_hdt,
        "HT_CFPP_PRODUCT": p.cfpp_ref + p.dcfpp_dt95 * dt95,
        "HT_CN_PRODUCT": p.cn_ref + p.dcn_dt95 * dt95,
    }
