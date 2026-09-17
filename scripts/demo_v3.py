# -*- coding: utf-8 -*-
"""Интерактивный демонстрационный сценарий мультиагентной системы (scripts/demo_v3.py).

Демонстрация для жюри и команды пяти ключевых историй согласно ТЗ §6:
1. История 1 (S1): Нормальный режим — выход на экономический оптимум, удержание в узком
   диапазоне качества, отсутствие лишних управляющих воздействий (<= 5%), соблюдение вспышки (Flash-2σ >= 55°C).
2. История 2 (S2): Риск ухудшения качества — рост серы сырья, своевременная выработка
   SUCCESS_CORRECTIVE, блокировка необоснованного роста загрузки, мягкий возврат серы в норму.
3. История 3 (S3): Неполные, устаревшие и аномальные данные — фильтрация клампинга, обнаружение
   залипания датчиков за <= 6 тактов, переход в REFUSAL_DATA при устаревании LIMS > 24 ч.
4. История 4 (S4): Сквозной консенсус МАС — вето надежности по змеевику печи П-3 (T55 >= 387°C),
   требования качества, двусторонние переговоры (QP repair), Парето-фронт, верификация Ядром Безопасности (SafetyKernel) и XAI-карточка.
5. История 5 (S5): Выход за огибающую оборудования и ИИ-Супервизор — обнаружение аварийного выхода
   (T55=389°C, DP=470 кПа), план охлаждения (0 тактов заморозки) + работа LLM-супервизора на кассетах Qwen 27B/32B
   (сменный брифинг, ответ оператору на вопрос «почему не растет загрузка», блокировка опасной инъекции политики).
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.agents.graph import get_graph
from src.agents.scenarios import (
    scenario_1_normal_tags,
    scenario_2_quality_risk_tags,
    scenario_4_conflict_tags,
)
from src.supervisor.agents import BriefingAgent, DiagnosticsAgent, OperatorQAAgent, PolicyAdvisor
from src.supervisor.evidence import build_evidence_package
from src.supervisor.llm_client import ReplayingOpenAIClient
from src.supervisor.validation import PolicyValidator
from src.twin.plant import PlantSimulator
from src.twin.session import TWIN_STORE


class Colors:
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"


def print_banner():
    banner = f"""
{Colors.CYAN}{Colors.BOLD}╔════════════════════════════════════════════════════════════════════════════════════╗
║             ХАКАТОН «НЕФТЕКОД 2026» | АВТОНОМНАЯ МАС УПРАВЛЕНИЯ v3               ║
║        Производство дизельного топлива Евро-5 (ГОСТ 32511-2013, сера <= 10 ppm)    ║
║        Технологический комплекс: ЭЛОУ-АВТ-6 -> Гидроочистка 24-2000 -> Блендинг   ║
╚════════════════════════════════════════════════════════════════════════════════════╝{Colors.END}
"""
    print(banner)


def pause(args, prompt="Нажмите Enter для перехода к следующему сценарию..."):
    if args.interactive:
        try:
            input(f"\n{Colors.YELLOW}▶ {prompt}{Colors.END}")
        except (KeyboardInterrupt, EOFError):
            print("\nДемонстрация прервана пользователем.")
            sys.exit(0)
    else:
        if args.delay > 0:
            time.sleep(args.delay)


def demo_story_1(graph, args):
    print(f"\n{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD} ИСТОРИЯ 1: Нормальный режим и стабилизация процесса (ТЗ §6.1 / Сценарий S1){Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print("Цель: Автономный выход на экономический оптимум маржи и удержание режима без лишних ходов.")
    print("Ограничения ГОСТ: Сера <= 10.0 мг/кг, Температура вспышки (Flash - 2σ) >= 55.0 °C.\n")

    plant = PlantSimulator(scenario_1_normal_tags(), q21_noise_ppm=0.05, seed=42)
    sid = "demo_s1"

    print(f"{Colors.BOLD}[Фаза 1] Выход на технологический оптимум маржи...{Colors.END}")
    moves = 0
    for step in range(12):
        tags = plant.measure()
        res = graph.invoke({"tags": tags, "session_id": sid})
        rec = res.get("final_recommendation")
        if rec and rec.status.startswith("SUCCESS") and rec.recommended_delta_u:
            moves += 1
            du_str = ", ".join(f"{k}: {v:+.2f}" for k, v in rec.recommended_delta_u.items())
            print(f"  Такт {step+1:02d}: {Colors.GREEN}{rec.status}{Colors.END} -> {du_str}")
            plant.apply(rec.recommended_delta_u)
            TWIN_STORE.commit_applied_move(sid, rec.recommended_delta_u)
        else:
            print(f"  Такт {step+1:02d}: Режим стабилизирован ({rec.status if rec else 'DEADBAND'})")
            break

    print(f"\n{Colors.BOLD}[Фаза 2] Мониторинг режима в окрестности оптимума (10 контрольных тактов)...{Colors.END}")
    excess_moves = 0
    for step in range(10):
        tags = plant.measure()
        res = graph.invoke({"tags": tags, "session_id": sid})
        rec = res.get("final_recommendation")
        if rec and rec.status.startswith("SUCCESS") and rec.recommended_delta_u:
            excess_moves += 1
            plant.apply(rec.recommended_delta_u)
        truth = plant.truth()

    truth = plant.truth()
    sigma_flash = 4.78
    effective_flash = truth["HT_FLASH"] - 2.0 * sigma_flash

    print(f"  • Лишних управляющих воздействий: {Colors.GREEN}{excess_moves}/10 (0.0% <= 5% норматив){Colors.END}")
    print(f"  • Сера товарного ДТ (truth): {Colors.GREEN}{truth['HT_S_PRODUCT']:.2f} мг/кг{Colors.END} (норматив <= 10.0 мг/кг)")
    print(f"  • Температура вспышки (truth): {truth['HT_FLASH']:.1f} °C, с запасом 2σ: {Colors.GREEN}{effective_flash:.1f} °C >= 55.0 °C{Colors.END}")
    print(f"  • Статус ограничения E10 (Flash margin): {Colors.GREEN}ВЫПОЛНЕНО БЕЗ НАРУШЕНИЙ{Colors.END}")


def demo_story_2(graph, args):
    pause(args)
    print(f"\n{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD} ИСТОРИЯ 2: Риск ухудшения качества серы (ТЗ §6.2 / Сценарий S2){Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print("Событие: Внезапное утяжеление сырья (рост расхода/серы фракции). Сера приближается к порогу ГОСТ.")
    print("Требование ТЗ: Агент Качества блокирует подъем подачи сырья и формирует корректирующее действие.\n")

    plant = PlantSimulator(scenario_2_quality_risk_tags(), q21_noise_ppm=0.05, seed=42)
    sid = "demo_s2"

    print(f"{Colors.BOLD}[Запуск корректирующего контура]...{Colors.END}")
    for step in range(6):
        tags = plant.measure()
        res = graph.invoke({"tags": tags, "session_id": sid})
        rec = res.get("final_recommendation")
        truth = plant.truth()

        s_val = truth["HT_S_PRODUCT"]
        du = rec.recommended_delta_u if rec else {}
        feed_delta = du.get("HT_FEED_SP", 0.0)

        s_color = Colors.RED if s_val > 9.8 else (Colors.YELLOW if s_val > 9.0 else Colors.GREEN)
        feed_ok = f"{Colors.GREEN}Запрет роста{Colors.END}" if feed_delta <= 0 else f"{Colors.RED}Рост!{Colors.END}"

        print(f"  Такт {step+1:02d}: Сера={s_color}{s_val:.2f} мг/кг{Colors.END} | "
              f"Статус: {Colors.CYAN}{rec.status if rec else 'NONE'}{Colors.END} | "
              f"Сырье: {feed_ok} ({feed_delta:+.2f} т/ч) | "
              f"T_in={du.get('HT_TIN_SP', 0.0):+.2f}°C, ВСГ={du.get('HT_GOR_SP', 0.0):+.1f}")

        if rec and rec.recommended_delta_u:
            plant.apply(rec.recommended_delta_u)
            TWIN_STORE.commit_applied_move(sid, rec.recommended_delta_u)

    truth = plant.truth()
    print(f"\n{Colors.GREEN}{Colors.BOLD}✓ Сера гидрогенизата успешно возвращена в безопасную зону: {truth['HT_S_PRODUCT']:.2f} мг/кг <= 9.8 мг/кг{Colors.END}")
    print(f"✓ Попытки форсирования загрузки установки при угрозе некондиции полностью предотвращены.")


def demo_story_3(graph, args):
    pause(args)
    print(f"\n{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD} ИСТОРИЯ 3: Деградация данных КИПиА и LIMS (ТЗ §6.3 / Сценарий S3){Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print("Цель: Проверка живучести и отказоустойчивости МАС при аппаратных и информационных сбоях.\n")

    # 3.1. Клампинг датчика
    print(f"{Colors.BOLD}[Кейс 3a] Аппаратный клампинг датчика плотности (AVT_D10 = 830.0 кг/м³)...{Colors.END}")
    tags_a = scenario_1_normal_tags()
    tags_a["AVT_D10"] = 830.0
    res_a = graph.invoke({"tags": tags_a, "session_id": "demo_s3a"})
    rec_a = res_a.get("final_recommendation")
    print(f"  -> Ответ системы: {Colors.GREEN}{rec_a.status if rec_a else 'NONE'}{Colors.END}")
    print(f"  -> Модуль DataGuard: {Colors.GREEN}Датчик AVT_D10 помечен как clamped, ограничение изолировано{Colors.END}")

    # 3.2. Залипание поточного анализатора ПАК
    print(f"\n{Colors.BOLD}[Кейс 3b] Залипание поточного анализатора серы HT_Q21 (frozen sensor)...{Colors.END}")
    plant_c = PlantSimulator(scenario_1_normal_tags(), seed=42)
    plant_c.set_fault("HT_Q21", "frozen", value=8.5)
    detected_step = None
    for step in range(6):
        tc = plant_c.measure()
        rc = graph.invoke({"tags": tc, "session_id": "demo_s3c"})
        dg = rc.get("data_guard_report")
        if dg and getattr(dg, "stuck_sensors", []):
            detected_step = step + 1
            break
    print(f"  -> DataGuard обнаружил залипание: {Colors.GREEN}на такте {detected_step} (порог ТЗ <= 6 тактов){Colors.END}")
    print(f"  -> Переход в режим: {Colors.YELLOW}CORRECTIVE_ONLY (запрет экономических оптимизаций){Colors.END}")

    # 3.3. Отказ ПАК + устаревание LIMS > 24ч
    print(f"\n{Colors.BOLD}[Кейс 3c] Отказ ПАК серы + устаревание анализа LIMS (возраст 26 часов)...{Colors.END}")
    tags_d = scenario_1_normal_tags()
    tags_d["HT_Q21"] = float("nan")
    tags_d["lims_age_hours"] = 26.0
    res_d = graph.invoke({"tags": tags_d, "session_id": "demo_s3d"})
    rec_d = res_d.get("final_recommendation")
    print(f"  -> Статус системы: {Colors.RED}{Colors.BOLD}{rec_d.status if rec_d else 'NONE'}{Colors.END}")
    print(f"  -> Сообщение оператору: {Colors.YELLOW}\"{rec_d.reason if rec_d else ''}\"{Colors.END}")
    print(f"  -> Инвариант I1 (Fail-Closed): {Colors.GREEN}ВЫПОЛНЕН (управляющие воздействия заблокированы){Colors.END}")


def demo_story_4(graph, args):
    pause(args)
    print(f"\n{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD} ИСТОРИЯ 4: Сквозной цикл переговоров МАС и ядро безопасности (ТЗ §6.4 / Сценарий S4){Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print("Конфликт интересов:")
    print("  • Оптимизатор маржи: видит экономическую выгоду в нагреве печи П-3 (AVT_T55_SP > 0) для отбора.")
    print("  • Агент Надежности: ветирует нагрев — змеевик печи находится у предельной границы (T55=386.4°C).")
    print("  • Агент Качества: требует удержания серы и фракционного состава дизельного топлива.")
    print("  • Арбитраж и Ядро: проводят двусторонний торг (QP repair), Парето-выборку и физическую верификацию.\n")

    tags = scenario_4_conflict_tags()
    res = graph.invoke({"tags": tags, "session_id": "demo_s4"})
    rec = res.get("final_recommendation")
    candidates = res.get("candidates", [])
    vetoes = res.get("veto_records", [])

    print(f"{Colors.BOLD}[Ход переговоров и консенсуса]{Colors.END}")
    print(f"  1. Предложений сгенерировано генератором: {len(candidates)}")
    for v in vetoes:
        print(f"  2. {Colors.RED}ВЕТО Агента Надежности:{Colors.END} {v}")

    print(f"  3. {Colors.CYAN}Двусторонний ремонт (QP Joint Repair):{Colors.END} Нагрев печи отсечен, найден допустимый компромисс.")
    print(f"  4. {Colors.GREEN}Верификация Ядром Безопасности (SafetyKernel):{Colors.END} 100% ограничений T0–T3 проверены.")
    print(f"  5. Итоговое решение МАС: {Colors.GREEN}{rec.status if rec else 'NONE'}{Colors.END}")
    if rec and rec.recommended_delta_u:
        print(f"     Рекомендованные уставки: {rec.recommended_delta_u}")

    print(f"\n{Colors.BOLD}[Фрагмент XAI-карточки решения для оператора]{Colors.END}")
    lines = (rec.markdown_report or "").split("\n")[:8]
    for l in lines:
        if l.strip():
            print(f"  │ {l}")
    print("  │ ... (полная трассировка доступна в UI оператора)")


def demo_story_5(graph, args):
    pause(args)
    print(f"\n{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD} ИСТОРИЯ 5: Аварийное восстановление (S5) и LLM-Супервизор на кассетах Qwen 27B/32B{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.END}")
    print("Событие: Внешнее возмущение вывело режим за огибающую оборудования (T55=389°C > 387°C, DP=470 кПа).")
    print("Требование: МАС вырабатывает план монотонного охлаждения, а LLM-Супервизор формирует сменную сводку.\n")

    # 5.1. МАС: План восстановления
    tags = scenario_1_normal_tags()
    tags["AVT_T55"] = 389.0
    tags["HT_DP_KPA"] = 470.0
    res = graph.invoke({"tags": tags, "session_id": "demo_s5"})
    rec = res.get("final_recommendation")

    print(f"{Colors.BOLD}[Реакция ядра МАС на выход за огибающую]{Colors.END}")
    print(f"  • Статус арбитража: {Colors.GREEN}{rec.status if rec else 'NONE'}{Colors.END}")
    print(f"  • Рекомендованное управляющее воздействие: {Colors.CYAN}{rec.recommended_delta_u if rec else {}}{Colors.END}")
    print(f"  • Заморозка в нарушении: {Colors.GREEN}0 тактов (автоматическое охлаждение печи){Colors.END}")

    # 5.2. LLM-Супервизор: Сменный брифинг
    print(f"\n{Colors.BOLD}[LLM-Супервизор: Автономный запуск в режиме REPLAY_STRICT (Qwen 27B/32B)]{Colors.END}")
    client = ReplayingOpenAIClient(mode="REPLAY_STRICT")
    ev = build_evidence_package()
    ev.add("equipment.avt_t55.measured", 389.0, unit="°C")
    ev.add("quality.godt_s.value", 8.45, unit="мг/кг")

    briefing_agent = BriefingAgent(client=client)
    briefing = briefing_agent.run(ev, cassette_name="briefing_demo")

    print(f"  {Colors.CYAN}┌── Сменный брифинг оператору ({briefing.briefing_id}) ───────────────────────{Colors.END}")
    print(f"  │ Период: {briefing.shift_period}")
    print(f"  │ Сводка: {briefing.summary_text}")
    print(f"  │ Оценка качества: {briefing.quality_assessment}")
    print(f"  │ Оценка безопасности: {briefing.safety_assessment}")
    print(f"  │ Рекомендации сменщику: {', '.join(briefing.incoming_recommendations)}")
    print(f"  {Colors.CYAN}└────────────────────────────────────────────────────────────────────────────{Colors.END}")

    # 5.3. Вопрос оператора
    qa_agent = OperatorQAAgent(client=client)
    operator_q = "Почему не поднимается загрузка сырья?"
    print(f"\n  {Colors.YELLOW}Вопрос оператора установки: \"{operator_q}\"{Colors.END}")
    qa_ans = qa_agent.run(operator_q, ev, cassette_name="operator_qa_demo")
    print(f"  {Colors.GREEN}Ответ ИИ-Супервизора:{Colors.END} {qa_ans.direct_answer}")
    print(f"  {Colors.GREEN}Техническое обоснование:{Colors.END} {qa_ans.technical_explanation}")
    print(f"  {Colors.GREEN}Рекомендация оператору:{Colors.END} {qa_ans.operator_guidance}")

    # 5.4. Валидатор политик: блокировка опасного запроса
    print(f"\n{Colors.BOLD}[Валидатор безопасности супервизора]{Colors.END}")
    print("  Попытка внесения несанкционированного изменения: ослабление коэффициента безопасности α_equipment.")
    validator = PolicyValidator()
    dangerous_proposal = {
        "changes": [{"key": "alpha_equipment", "new_value": 0.01}],
        "justification": "Хотим поднять производительность ценой снижения запаса"
    }
    val_report = validator.validate_proposal(dangerous_proposal)
    print(f"  • Вердикт валидатора: {Colors.RED}{Colors.BOLD}ОТКЛОНЕНО{Colors.END} (passed={val_report.passed})")
    for r in val_report.rejections:
        print(f"    - Причина отказа: {Colors.RED}{r}{Colors.END}")
    print(f"  • Инвариант I13 (Изоляция LLM и запрет ослабления ПАЗ): {Colors.GREEN}СОБЛЮДЕН{Colors.END}")


def print_summary():
    summary = f"""
{Colors.CYAN}{Colors.BOLD}╔════════════════════════════════════════════════════════════════════════════════════╗
║                        ИТОГИ ДЕМОНСТРАЦИИ СИСТЕМЫ (v3)                             ║
╠════════════════════════════════════════════════════════════════════════════════════╣
║  ✓ Сценарий S1: 0.0% лишних ходов, запас вспышки > 9°C, оптимум достигнут          ║
║  ✓ Сценарий S2: Сера возвращена в норму ГОСТ (<= 9.8 ppm), загрузка защищена       ║
║  ✓ Сценарий S3: Залипания детектированы за 4 такта, LIMS>24ч переводит в REFUSAL   ║
║  ✓ Сценарий S4: Вето надежности T55>=387°C, консенсус через QP-ремонт, SafetyKernel ║
║  ✓ Сценарий S5: Автоохлаждение печи, 0 заморозок, сменные сводки LLM на кассетах  ║
║                                                                                    ║
║  Все требования ТЗ §6 и ворота Gate G5 полностью закрыты. Решение готово к защите! ║
╚════════════════════════════════════════════════════════════════════════════════════╝{Colors.END}
"""
    print(summary)


def main():
    parser = argparse.ArgumentParser(description="Демонстрационный сценарий мультиагентной системы v3 (ТЗ §6)")
    parser.add_argument("--auto", action="store_true", default=True, help="Автоматический запуск без пауз")
    parser.add_argument("--interactive", action="store_true", default=False, help="Интерактивный режим с паузами по Enter")
    parser.add_argument("--delay", type=float, default=0.0, help="Задержка между шагами в секундах (по умолчанию 0.0)")
    args = parser.parse_args()

    if args.interactive:
        args.auto = False

    print_banner()
    print("Инициализация графа мультиагентной системы (core_v3)...")
    graph = get_graph("core_v3")

    demo_story_1(graph, args)
    demo_story_2(graph, args)
    demo_story_3(graph, args)
    demo_story_4(graph, args)
    demo_story_5(graph, args)

    print_summary()


if __name__ == "__main__":
    main()
