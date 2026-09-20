import os
import re

def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

system_design = read_file('artifacts/System_Design.md')
domain = read_file('agents/DOMAIN_KNOWLEDGE.md')
assumptions = read_file('agents/ASSUMPTIONS.md')
impl_v3 = read_file('agents/implementation_plan_v3.md')
whats_done = read_file('artifacts/whats_done.md')

# Build REFERENCE.md
ref_lines = []
ref_lines.append("# REFERENCE — Инженерный справочник МАС «Нефтекод»")
ref_lines.append("> Последнее обновление: сентябрь 2026. Заменяет: DOMAIN_KNOWLEDGE.md, ASSUMPTIONS.md, System_Design.md (доменная часть), implementation_plan_v3.md (архитектурная часть).")
ref_lines.append("")

ref_lines.append("## 1. Технологический контекст")
# Extract section 1.1 from system_design
m = re.search(r'(## 1\.1\..*?)(?=\n## 1\.2|\n# Раздел 2)', system_design, re.DOTALL)
if m:
    ref_lines.append(m.group(1))

ref_lines.append("")
ref_lines.append("## 2. Нормативы и технологические границы")
ref_lines.append(domain)
m_limits = re.search(r'(### 4\.1\. Стадия 1.*?(?=\n### 4\.2))', system_design, re.DOTALL)
if m_limits:
    ref_lines.append("\n### Дополнительные ограничения из System Design\n")
    ref_lines.append(m_limits.group(1))

ref_lines.append("")
ref_lines.append("## 3. Реестр инженерных допущений")
# Extract assumptions, removing duplicates if possible, or just append
ref_lines.append(assumptions)

ref_lines.append("")
ref_lines.append("## 4. Архитектура МАС v3")
m_arch = re.search(r'(## 1\. Решения по развилкам.*?\n## 6\. LLM-супервизор)', impl_v3, re.DOTALL)
if m_arch:
    arch_content = m_arch.group(1)
    # Remove P0/P1/P2/P3/P4/P5 step-by-step
    ref_lines.append(arch_content)

ref_lines.append("")
ref_lines.append("## 5. Экономическая модель")
m_econ = re.search(r'(### 2\.7\. Экономика.*?(?=\n### 2\.8))', assumptions, re.DOTALL)
if m_econ:
    ref_lines.append(m_econ.group(1))
m_blend = re.search(r'(### 3\.5\. Программный модуль блендинга.*?(?=\n## Шаг 4))', system_design, re.DOTALL)
if m_blend:
    ref_lines.append(m_blend.group(1))

write_file('agents/REFERENCE.md', '\n'.join(ref_lines))

# Build STATUS.md
status_lines = []
status_lines.append("# STATUS — Текущее состояние проекта МАС «Нефтекод»")
status_lines.append("> Последнее обновление: сентябрь 2026.")
status_lines.append("")

status_lines.append("## Что реализовано и работает")
status_lines.append(whats_done)
status_lines.append("")

status_lines.append("## Открытые задачи")
status_lines.append("### P3.14 Legacy cleanup")
status_lines.append("- [x] optimization_stub.py")
status_lines.append("- [x] implementation_plan_v2.md")
status_lines.append("- [x] mockups/")
status_lines.append("- [x] baseline_kpis.py")
status_lines.append("- [x] test_step1_mvp.py")
status_lines.append("- [x] supervisor/tools.py")
status_lines.append("- [x] safe_hold.py")
status_lines.append("- [ ] optimization.py")
status_lines.append("- [ ] auditors.py (still needed by MVP unit tests)")
status_lines.append("")
status_lines.append("### P4 LLM-супервизор")
# extract open tasks for P4 and P5
m_p4 = re.search(r'(## 12\. Этап P4.*?)(?=\n## 13)', impl_v3, re.DOTALL)
if m_p4:
    tasks = re.findall(r'- \[ \].*', m_p4.group(1))
    for t in tasks: status_lines.append(t)

status_lines.append("")
status_lines.append("### P5 Финализация")
m_p5 = re.search(r'(## 13\. Этап P5.*?)(?=\n# Часть III)', impl_v3, re.DOTALL)
if m_p5:
    tasks = re.findall(r'- \[ \].*', m_p5.group(1))
    for t in tasks: status_lines.append(t)

status_lines.append("")
status_lines.append("## Известные ограничения и технический долг")
m_debt = re.search(r'(### Технический долг.*?(?=\n##|$))', impl_v3, re.DOTALL)
if m_debt:
    status_lines.append(m_debt.group(1))

write_file('agents/STATUS.md', '\n'.join(status_lines))

