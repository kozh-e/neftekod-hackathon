"""Единый технологический реестр ограничений T0–T3 с провенансом.

Соответствует спецификации §4.3 implementation_plan_v3.md и ADR-12/ADR-19.
Каждая спецификация ConstraintSpec содержит точный провенанс, ярус важности,
структурную зависимость depends_on от управляющих воздействий и требования к датчикам.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Set
from src.agents.contracts import (
    ConstraintSpec,
    Provenance,
    ProvenanceKind,
    Tier,
)

# =============================================================================
# Ярус T0: Модельные границы и пределы скорости хода MV
# =============================================================================

T0_SPECS: tuple[ConstraintSpec, ...] = (
    # Расход сырья гидроочистки (HT_FEED_SP)
    ConstraintSpec(
        key="MV.HT_FEED_SP.MIN",
        label="Минимальный расход сырья 24-2000",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_FEED_SP",
        sense="min",
        limit=158.7,
        unit="т/ч",
        scale=10.0,
        depends_on=frozenset({"HT_FEED_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q05 рабочего периода сырья ГО ДТ",
        ),
    ),
    ConstraintSpec(
        key="MV.HT_FEED_SP.MAX",
        label="Максимальный расход сырья 24-2000",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_FEED_SP",
        sense="max",
        limit=252.8,
        unit="т/ч",
        scale=10.0,
        depends_on=frozenset({"HT_FEED_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q95 рабочего периода сырья ГО ДТ",
        ),
    ),
    # Температура входа Р-202 (HT_TIN_SP)
    ConstraintSpec(
        key="MV.HT_TIN_SP.MIN",
        label="Минимальная температура входа Р-202",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_TIN_SP",
        sense="min",
        limit=346.5,
        unit="°C",
        scale=5.0,
        depends_on=frozenset({"HT_TIN_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q05 температуры ГСС на входе в реактор Р-202",
        ),
    ),
    ConstraintSpec(
        key="MV.HT_TIN_SP.MAX",
        label="Максимальная температура входа Р-202",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_TIN_SP",
        sense="max",
        limit=380.5,
        unit="°C",
        scale=5.0,
        depends_on=frozenset({"HT_TIN_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q95 температуры ГСС на входе в реактор Р-202",
        ),
    ),
    # Давление в контуре гидроочистки (HT_P_SP)
    ConstraintSpec(
        key="MV.HT_P_SP.MIN",
        label="Минимальное давление сепарации Р-202",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_P_SP",
        sense="min",
        limit=3.755,
        unit="МПа",
        scale=0.1,
        depends_on=frozenset({"HT_P_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q05 рабочего давления реакторного блока",
        ),
    ),
    ConstraintSpec(
        key="MV.HT_P_SP.MAX",
        label="Максимальное давление сепарации Р-202",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_P_SP",
        sense="max",
        limit=4.009,
        unit="МПа",
        scale=0.1,
        depends_on=frozenset({"HT_P_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q95 рабочего давления реакторного блока",
        ),
    ),
    # Кратность ВСГ / сырье (HT_GOR_SP)
    ConstraintSpec(
        key="MV.HT_GOR_SP.MIN",
        label="Минимальная уставка кратности ВСГ/сырье",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_GOR_SP",
        sense="min",
        limit=313.0,
        unit="нм3/м3",
        scale=30.0,
        depends_on=frozenset({"HT_GOR_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q05 кратности циркуляции водородсодержащего газа",
        ),
    ),
    ConstraintSpec(
        key="MV.HT_GOR_SP.MAX",
        label="Максимальная уставка кратности ВСГ/сырье",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="HT_GOR_SP",
        sense="max",
        limit=490.0,
        unit="нм3/м3",
        scale=30.0,
        depends_on=frozenset({"HT_GOR_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="q95 кратности циркуляции водородсодержащего газа",
        ),
    ),
    # Уставка перевала печи П-3 (AVT_T55_SP)
    ConstraintSpec(
        key="MV.AVT_T55_SP.MIN",
        label="Минимальная уставка температуры перевала печи П-3",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="AVT_T55_SP",
        sense="min",
        limit=375.0,
        unit="°C",
        scale=5.0,
        depends_on=frozenset({"AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Нижняя граница регламентного диапазона печи П-3 (перемаркировано из NORM)",
        ),
    ),
    ConstraintSpec(
        key="MV.AVT_T55_SP.MAX",
        label="Максимальная уставка температуры перевала печи П-3",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="AVT_T55_SP",
        sense="max",
        limit=395.0,
        unit="°C",
        scale=5.0,
        depends_on=frozenset({"AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Предел ПАЗ печи П-3 (перемаркировано из NORM)",
        ),
    ),
    # Ограничения на максимальный шаг за цикл (Скорость хода)
    ConstraintSpec(
        key="RATE.HT_FEED_SP.MAX",
        label="Максимальная скорость изменения подачи сырья",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="RATE.HT_FEED_SP",
        sense="max",
        limit=10.0,
        unit="т/ч/такт",
        scale=5.0,
        depends_on=frozenset({"HT_FEED_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Максимальное приращение хода за один цикл управления",
        ),
    ),
    ConstraintSpec(
        key="RATE.HT_TIN_SP.MAX",
        label="Максимальная скорость изменения температуры входа",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="RATE.HT_TIN_SP",
        sense="max",
        limit=3.0,
        unit="°C/такт",
        scale=2.0,
        depends_on=frozenset({"HT_TIN_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Ограничение термического удара катализатора Р-202",
        ),
    ),
    ConstraintSpec(
        key="RATE.AVT_T55_SP.MAX",
        label="Максимальная скорость изменения температуры печи П-3",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="RATE.AVT_T55_SP",
        sense="max",
        limit=3.0,
        unit="°C/такт",
        scale=2.0,
        depends_on=frozenset({"AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Ограничение термонапряжения змеевика печи П-3",
        ),
    ),
    ConstraintSpec(
        key="RATE.HT_P_SP.MAX",
        label="Максимальная скорость изменения давления сепарации",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="RATE.HT_P_SP",
        sense="max",
        limit=0.05,
        unit="МПа/такт",
        scale=0.1,
        depends_on=frozenset({"HT_P_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note=(
                "По паспорту оборудования у HT_P_SP нет ограничения скорости хода за такт — "
                "это НЕ значение из паспорта, а осознанная инженерная оценка (по прямому запросу "
                "оператора продукта, 2026-09-21: 'укажи любое разумное число но чуть больше'), "
                "введённая только для того, чтобы разрешить автоматическое изменение уставки "
                "давления вместо жёсткого запрета «изменение только вручную в DCS» (см. "
                "corridor.py::check_step — без спека в реестре любое ненулевое изменение "
                "блокируется). До этого изменения в service.py::get_constants() уже был "
                "захардкожен ДЕКОРАТИВНЫЙ (ни на что не влияющий) показ 'RATE.HT_P_SP.MAX' = "
                "0.03 МПа/такт с описанием «Гидравлическая устойчивость контура ВСГ» — вкладка "
                "«Константы» показывала оператору лимит, который реестр никогда не применял. "
                "Взял 0.05 — заметно больше того декоративного числа (как и просили), но всё ещё "
                "заметно меньше scale=0.1 МПа для HT_P_SP. service.py теперь читает это поле из "
                "REGISTRY вместо отдельной захардкоженной копии. При появлении реального "
                "паспортного значения — заменить обе стороны (limit здесь и description)."
            ),
        ),
    ),
    ConstraintSpec(
        key="RATE.HT_GOR_SP.MAX",
        label="Максимальная скорость изменения кратности ВСГ/сырье",
        owner="kernel",
        tier=Tier.T0_BOUNDS,
        quantity="RATE.HT_GOR_SP",
        sense="max",
        limit=30.0,
        unit="нм3/м3/такт",
        scale=30.0,
        depends_on=frozenset({"HT_GOR_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note=(
                "По паспорту оборудования у HT_GOR_SP нет ограничения скорости хода за такт — "
                "это НЕ значение из паспорта, а осознанная инженерная оценка (по прямому запросу "
                "оператора продукта, 2026-09-22: 'убери все предупреждения по типу этого... либо "
                "задай большие интуитивные значения'), тот же паттерн, что и RATE.HT_P_SP.MAX выше "
                "(тоже введён по явному запросу 2026-09-21). Без спека в реестре любое ненулевое "
                "изменение HT_GOR_SP блокировалось (см. corridor.py::check_step — 'Шаг HT_GOR_SP "
                "не задан в паспорте, изменение только вручную в DCS'). Значение взято равным полю "
                "scale=30.0 нм3/м3 для HT_GOR_SP (см. MV.HT_GOR_SP.MIN/MAX выше), по аналогии с "
                "RATE.HT_FEED_SP.MAX, где rate-лимит равен scale — контур допускает пройти весь "
                "паспортный диапазон [313, 490] примерно за 6 тактов, того же порядка, что и "
                "остальные четыре MV. При появлении реального паспортного значения — заменить.\n"
                "Больше в T0_SPECS ни у одного MV НЕТ отсутствующего RATE-спека — все пять "
                "(HT_FEED_SP, HT_TIN_SP, HT_P_SP, HT_GOR_SP, AVT_T55_SP) теперь имеют явный лимит "
                "скорости хода, у трёх из пяти — паспортный (FEED/TIN/T55), у двух — ASSUMPTION "
                "(P_SP/GOR_SP). НЕ добавлять подобные спеки по аналогии для будущих MV без "
                "явного запроса — см. предупреждение в аудите 2026-09-20 про candidates.py::"
                "DEFAULT_MVS.max_move (параметр генерации кандидатов, а не физический лимит)."
            ),
        ),
    ),
)


# =============================================================================
# Ярус T1: Оборудование и ПАЗ/ESD (ReliabilityAgent)
# =============================================================================

T1_SPECS: tuple[ConstraintSpec, ...] = (
    ConstraintSpec(
        key="FURNACE.COT_MAX",
        label="Защитный предел перевала печи П-3 (COT)",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="AVT_T55",
        sense="max",
        limit=386.4,
        unit="°C",
        scale=12.0,
        domain="linear",
        chance=False,
        # not_worse_than_hold (было "strict" до аудита 2026-09-22): при уже действующем
        # нарушении (перевал выше 386.4, например сценарий S5) "strict" не давал ходу по
        # AVT_T55_SP вообще никакого кредита — печь физически не может остыть за один такт
        # (постоянная времени tau_t_in/tau_bed), поэтому пиковое значение траектории на
        # горизонте прогноза (chance_domain="extrema") в первой точке совпадает с hold, и
        # разгружающий ход выглядел для арбитража бесполезным: система никогда не выбирала
        # снизить AVT_T55_SP, потому что это не улучшало оценку ограничения ни на йоту (см.
        # reliability.py::_apply_transient_rule, который для этого правила сравнивает
        # установившийся режим, а не extrema). "not_worse_than_hold" зеркалит уже действующий
        # для RX.DP_MAX случай: ход, который печь ДЕЙСТВИТЕЛЬНО охлаждает, засчитывается
        # улучшающим, даже если предел еще не достигнут за один такт.
        transient="not_worse_than_hold",
        chance_domain="extrema",
        depends_on=frozenset({"AVT_T55_SP"}),
        requires_measurement="AVT_T55",
        trip_ref=395.0,
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="5% защитный буфер безопасности отсечения ПАЗ печи П-3 (в истории > 386.4 в 0.8%)",
        ),
    ),
    ConstraintSpec(
        key="FURNACE.COT_POLICY_WARM",
        label="Запрет нагрева печи П-3 в зоне предупреждения",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="AVT_T55",
        sense="max",
        limit=380.0,
        unit="°C",
        scale=5.0,
        domain="linear",
        chance=False,
        transient="ss",
        depends_on=frozenset({"AVT_T55_SP"}),
        requires_measurement="AVT_T55",
        provenance=Provenance(
            kind=ProvenanceKind.POLICY,
            ref="config/policy/policy_v1.json:heating_warning_zone_c",
            note="Запрет приближения к ПАЗ печи ради маржи (перемаркировано из NORM)",
        ),
    ),
    ConstraintSpec(
        key="RX.DP_MAX",
        label="Предельный гидравлический перепад давления реактора Р-202",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="HT_DP_KPA",
        sense="max",
        limit=454.5,
        unit="кПа",
        scale=50.0,
        domain="log",
        chance=True,
        # not_worse_than_hold: во время уже действующего нарушения (hold_eff > limit)
        # разгрузочный ход, который строго улучшает эффективное значение (не ухудшает
        # относительно hold), засчитывается допустимым — иначе некуда восстанавливаться
        # (trip_ref == limit, запаса до ESD нет). chance_domain="extrema" сохраняет пиковую
        # (не только конечную steady-state) проверку траектории — раньше оба смысла жили в
        # одном поле transient, что не позволяло независимо включить пиковую проверку и
        # лазейку одновременно (аудит 2026-09-20, см. test_audit_e9_... и
        # test_reliability_dp_max_multiplicative_clogging).
        transient="not_worse_than_hold",
        chance_domain="extrema",
        depends_on=frozenset({"HT_FEED_SP", "HT_TIN_SP", "HT_GOR_SP"}),
        requires_measurement="HT_P8",
        trip_ref=454.5,
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Технологический предел 4.635 кгс/см² по паспорту Р-202",
        ),
    ),
    ConstraintSpec(
        key="RX.T_OUT_MAX",
        label="Предельная температура на выходе реактора Р-202",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="HT_T11",
        sense="max",
        limit=390.0,
        unit="°C",
        scale=10.0,
        domain="linear",
        chance=False,
        transient="strict",
        chance_domain="extrema",
        depends_on=frozenset({"HT_FEED_SP", "HT_TIN_SP"}),
        requires_measurement="HT_T11",
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Предотвращение закоксовывания катализатора (история q95=379.1 °C)",
        ),
    ),
    ConstraintSpec(
        key="RX.GOR_MIN",
        label="Минимальная кратность циркуляции ВСГ/сырье",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="HT_GOR",
        sense="min",
        limit=300.0,
        unit="нм3/м3",
        scale=30.0,
        domain="linear",
        chance=False,
        transient="strict",
        chance_domain="extrema",
        depends_on=frozenset({"HT_FEED_SP", "HT_GOR_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Защита катализатора от деактивации недостатком водорода",
        ),
    ),
    ConstraintSpec(
        key="FURNACE.F31_MIN",
        label="Предусловие минимального расхода сырья в змеевики печи П-3",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="AVT_F31",
        sense="min",
        limit=362.5,
        unit="т/ч",
        scale=50.0,
        domain="linear",
        chance=False,
        transient="ss",
        depends_on=frozenset({"AVT_T55_SP"}),
        requires_measurement="AVT_F31",
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Защита змеевиков печи П-3 от прогара при снижении расхода сырья",
        ),
    ),
    ConstraintSpec(
        key="COL.P52_MAX",
        label="Предусловие допустимого перепада давления насадки колонны К-10",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="AVT_P52",
        sense="max",
        limit=0.077,
        unit="кгс/см2",
        scale=0.02,
        domain="linear",
        chance=False,
        transient="ss",
        depends_on=frozenset({"AVT_T55_SP"}),
        requires_measurement="AVT_P52",
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Предотвращение захлебывания вакуумной колонны К-10",
        ),
    ),
    ConstraintSpec(
        key="SUPPLY.FEED_TO_AVT_MIN",
        label="Минимальное отношение расхода сырья ГО к дизелю АВТ",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="HT_FEED_TO_AVT",
        sense="min",
        limit=0.81,
        unit="",
        scale=0.1,
        domain="linear",
        chance=False,
        transient="ss",
        depends_on=frozenset({"HT_FEED_SP", "AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Нижний технологический баланс емкостного парка АВТ-ГО",
        ),
    ),
    ConstraintSpec(
        key="SUPPLY.FEED_TO_AVT_MAX",
        label="Максимальное отношение расхода сырья ГО к дизелю АВТ",
        owner="reliability",
        tier=Tier.T1_EQUIPMENT,
        quantity="HT_FEED_TO_AVT",
        sense="max",
        limit=1.26,
        unit="",
        scale=0.1,
        domain="linear",
        chance=False,
        transient="ss",
        depends_on=frozenset({"HT_FEED_SP", "AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §1",
            note="Верхний технологический баланс емкостного парка АВТ-ГО",
        ),
    ),
)


# =============================================================================
# Ярус T2: Качество гидрогенизата и товарного ДТ (QualityAgent / Blending)
# =============================================================================

T2_SPECS: tuple[ConstraintSpec, ...] = (
    # Сера гидрогенизата ГО ДТ (GODT.S)
    ConstraintSpec(
        key="GODT.S_MAX",
        label="Массовая доля серы в гидрогенизате ГО ДТ",
        owner="quality",
        tier=Tier.T2_QUALITY,
        quantity="GODT.S",
        sense="max",
        limit=10.0,
        unit="мг/кг",
        scale=1.0,
        domain="log",
        chance=True,
        transient="not_worse_than_hold",
        depends_on=frozenset({"HT_FEED_SP", "HT_TIN_SP", "HT_P_SP", "HT_GOR_SP", "AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1 / initial_data/ТЗ_нефтекод.docx §4",
            note="Требование ГОСТ к дизельному топливу Евро-5: не более 10.0 мг/кг",
        ),
    ),
    # Температура вспышки гидрогенизата ГО ДТ (GODT.FLASH)
    ConstraintSpec(
        key="GODT.FLASH_MIN",
        label="Температура вспышки в закрытом тигле гидрогенизата ГО ДТ",
        owner="quality",
        tier=Tier.T2_QUALITY,
        quantity="GODT.FLASH",
        sense="min",
        limit=55.0,
        unit="°C",
        scale=5.0,
        domain="linear",
        chance=True,
        transient="ss",
        depends_on=frozenset({"HT_FEED_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1 / initial_data/ТЗ_нефтекод.docx §4",
            note="Требование ГОСТ: не ниже 55.0 °C для летних и демисезонных сортов",
        ),
    ),
    # Показатели товарного топлива (PRODUCT.*) — владелец BlendingAgent
    ConstraintSpec(
        key="PRODUCT.S_MAX",
        label="Массовая доля серы в товарном дизельном топливе",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.S",
        sense="max",
        limit=10.0,
        unit="мг/кг",
        scale=1.0,
        domain="linear",
        chance=False,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1",
            note="Норматив содержания серы в товарном топливе Евро-5",
        ),
    ),
    ConstraintSpec(
        key="PRODUCT.FLASH_MIN",
        label="Температура вспышки товарного дизельного топлива",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.FLASH",
        sense="min",
        limit=55.0,
        unit="°C",
        scale=5.0,
        domain="linear",
        chance=False,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1",
            note="Норматив температуры вспышки товарного топлива",
        ),
    ),
    ConstraintSpec(
        key="PRODUCT.D15_MIN",
        label="Минимальная плотность при 15 °C товарного топлива",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.D15",
        sense="min",
        limit=820.0,
        unit="кг/м3",
        scale=5.0,
        domain="linear",
        chance=False,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1",
            note="Нижний предел плотности товарного ДТ Евро-5",
        ),
    ),
    ConstraintSpec(
        key="PRODUCT.D15_MAX",
        label="Максимальная плотность при 15 °C товарного топлива",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.D15",
        sense="max",
        limit=845.0,
        unit="кг/м3",
        scale=5.0,
        domain="linear",
        chance=False,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1",
            note="Верхний предел плотности товарного ДТ Евро-5",
        ),
    ),
    ConstraintSpec(
        key="PRODUCT.T95_MAX",
        label="Температура перегонки 95% объема (T95) товарного топлива",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.T95",
        sense="max",
        limit=360.0,
        unit="°C",
        scale=5.0,
        domain="linear",
        chance=True,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1",
            note="Норматив фракционного состава T95 товарного ДТ",
        ),
    ),
    ConstraintSpec(
        key="PRODUCT.E360_MIN",
        label="Объемная доля перегонки до 360 °C (E360)",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.E360",
        sense="min",
        limit=95.0,
        unit="% об.",
        scale=2.0,
        domain="linear",
        chance=False,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1",
            note="Норматив доли отгона дизельного топлива",
        ),
    ),
    ConstraintSpec(
        key="PRODUCT.CN_MIN",
        label="Цетановое число товарного дизельного топлива",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.CN",
        sense="min",
        limit=51.0,
        unit="",
        scale=1.0,
        domain="linear",
        chance=False,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 1",
            note="Норматив воспламеняемости товарного топлива Евро-5",
        ),
    ),
    ConstraintSpec(
        key="PRODUCT.CFPP_MAX",
        label="Предельная температура фильтруемости (базовый сорт E)",
        owner="blending",
        tier=Tier.T2_QUALITY,
        quantity="PRODUCT.CFPP",
        sense="max",
        limit=-15.0,
        unit="°C",
        scale=3.0,
        domain="linear",
        chance=False,
        provenance=Provenance(
            kind=ProvenanceKind.NORM,
            ref="ГОСТ 32511-2013 Таблица 2 (сорт E)",
            note="Норматив низкотемпературных свойств демисезонного сорта E",
        ),
    ),
)


# =============================================================================
# Ярус T3: Эксплуатационные ограничения и баланс сырья (SupplyAgent)
# =============================================================================

T3_SPECS: tuple[ConstraintSpec, ...] = (
    ConstraintSpec(
        key="BUFFER.INVENTORY_MIN",
        label="Минимальный виртуальный буфер сырья АВТ-ГО",
        owner="supply",
        tier=Tier.T3_OPERATIONAL,
        quantity="BUFFER.INVENTORY_T",
        sense="min",
        limit=-300.0,
        unit="т",
        scale=50.0,
        domain="linear",
        chance=False,
        transient="ss",
        depends_on=frozenset({"HT_FEED_SP", "AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §2.2 Q17",
            note="Ограничение виртуального накопителя сырья между АВТ и ГО на горизонте планирования",
        ),
    ),
    ConstraintSpec(
        key="BUFFER.INVENTORY_MAX",
        label="Максимальный виртуальный буфер сырья АВТ-ГО",
        owner="supply",
        tier=Tier.T3_OPERATIONAL,
        quantity="BUFFER.INVENTORY_T",
        sense="max",
        limit=300.0,
        unit="т",
        scale=50.0,
        domain="linear",
        chance=False,
        transient="ss",
        depends_on=frozenset({"HT_FEED_SP", "AVT_T55_SP"}),
        provenance=Provenance(
            kind=ProvenanceKind.ASSUMPTION,
            ref="agents/ASSUMPTIONS.md §2.2 Q17",
            note="Предотвращение переполнения виртуального буфера сырья",
        ),
    ),
)

BUFFER_LONG_RUN_RATIO_SPEC = ConstraintSpec(
    key="BUFFER.LONG_RUN_RATIO",
    label="Предельная загрузка ГО с учетом баланса сырья АВТ и запаса",
    owner="supply",
    tier=Tier.T3_OPERATIONAL,
    quantity="BUFFER.LONG_RUN_RATIO",
    sense="max",
    limit=1.0,
    unit="",
    scale=0.1,
    domain="linear",
    chance=False,
    transient="ss",
    depends_on=frozenset({"HT_FEED_SP", "AVT_T55_SP"}),
    provenance=Provenance(
        kind=ProvenanceKind.ASSUMPTION,
        ref="agents/ASSUMPTIONS.md §2.2 Q17",
        note="Ограничение расхода сырья F9 установившимся выходом АВТ плюс допустимая сработка буфера",
    ),
)


# Полный реестр
ALL_SPECS: tuple[ConstraintSpec, ...] = T0_SPECS + T1_SPECS + T2_SPECS + T3_SPECS
REGISTRY_BY_KEY: dict[str, ConstraintSpec] = {spec.key: spec for spec in ALL_SPECS}
REGISTRY: dict[str, ConstraintSpec] = REGISTRY_BY_KEY


class RegistryAdapter:
    """Адаптер над ALL_SPECS, предоставляющий интерфейс mv_lo/mv_hi/mv_max_move/applicable
    независимому ядру безопасности (SafetyKernel.verify) — единственный источник для проверок
    T0.bounds/T0.rate и независимой проверки оборудования/качества (§5 kernel.py)."""

    def __init__(self, specs: Sequence[ConstraintSpec]) -> None:
        self.specs = specs
        self.mv_lo: Dict[str, float] = {}
        self.mv_hi: Dict[str, float] = {}
        self.mv_max_move: Dict[str, float] = {}
        for s in specs:
            if s.key.startswith("MV.") and s.key.endswith(".MIN"):
                self.mv_lo[s.quantity] = s.limit
            elif s.key.startswith("MV.") and s.key.endswith(".MAX"):
                self.mv_hi[s.quantity] = s.limit
            elif s.key.startswith("RATE.") and s.key.endswith(".MAX"):
                mv_name = s.quantity.replace("RATE.", "")
                self.mv_max_move[mv_name] = s.limit

    def applicable(self, delta_u: Dict[str, float]) -> List[ConstraintSpec]:
        changed = {k for k, du in delta_u.items() if abs(du) > 1e-4}
        return [s for s in self.specs if s.depends_on & changed]


REGISTRY_ADAPTER = RegistryAdapter(ALL_SPECS)


def get_constraint(key: str) -> ConstraintSpec:
    """Возвращает спецификацию ограничения по ключу."""
    if key not in REGISTRY_BY_KEY:
        raise KeyError(f"Ограничение с ключом '{key}' не найдено в реестре ConstraintSpec")
    return REGISTRY_BY_KEY[key]


def get_constraints_by_tier(tier: Tier) -> list[ConstraintSpec]:
    """Возвращает список всех ограничений заданного яруса."""
    return [spec for spec in ALL_SPECS if spec.tier == tier]


def get_constraints_by_owner(owner: str) -> list[ConstraintSpec]:
    """Возвращает список всех ограничений указанного владельца."""
    return [spec for spec in ALL_SPECS if spec.owner == owner]


def get_specs_for_mv(mv_name: str) -> list[ConstraintSpec]:
    """Возвращает список ограничений, структурно зависящих от данного MV."""
    return [spec for spec in ALL_SPECS if mv_name in spec.depends_on]


def validate_registry_provenance() -> list[str]:
    """
    Проверяет валидность провенанса всех ограничений в реестре:
    - у каждой записи есть непустой провенанс;
    - NORM только со ссылкой на initial_data/, new_data/ или ГОСТ;
    - ASSUMPTION со ссылкой на agents/ASSUMPTIONS.md;
    - POLICY со ссылкой на policy.
    """
    errors: list[str] = []
    for spec in ALL_SPECS:
        prov = spec.provenance
        if not prov.ref:
            errors.append(f"{spec.key}: пустой провенанс ref")
            continue

        if prov.kind == ProvenanceKind.NORM:
            has_valid_norm_ref = any(
                prefix in prov.ref for prefix in ("initial_data/", "new_data/", "ГОСТ")
            )
            if not has_valid_norm_ref:
                errors.append(
                    f"{spec.key}: NORM должен ссылаться на initial_data/, new_data/ или ГОСТ, получено '{prov.ref}'"
                )

        elif prov.kind == ProvenanceKind.ASSUMPTION:
            if "ASSUMPTION" not in prov.ref and "agents/ASSUMPTIONS.md" not in prov.ref:
                errors.append(
                    f"{spec.key}: ASSUMPTION должен ссылаться на agents/ASSUMPTIONS.md, получено '{prov.ref}'"
                )

        elif prov.kind == ProvenanceKind.POLICY:
            if "policy" not in prov.ref.lower():
                errors.append(
                    f"{spec.key}: POLICY должен ссылаться на политику, получено '{prov.ref}'"
                )

    return errors
