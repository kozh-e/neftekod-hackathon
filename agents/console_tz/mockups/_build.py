"""Сборка автономных HTML-копий макета пульта из исходников холста (.dc.html).

Исходники холста требуют среды редактора (support.js). Скрипт вырезает из них разметку и логику
и вставляет крошечный рендерер шаблонов ({{путь}}, <sc-if>, <sc-for>), чтобы страница открывалась
в любом браузере без сети (кроме шрифтов Google Fonts, есть фолбэк).

Запуск: python agents/console_tz/mockups/_build.py <папка с *.dc.html>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent

SCREENS = [
    ("Main.dc.html", "01_advisory_edit.html", "Пульт Р-202 — Совет, правка оператора (S2)", {}),
    ("Auto.dc.html", "02_auto.html", "Пульт Р-202 — Автомат (S1)", {}),
    ("NoData.dc.html", "03_refusal_no_data.html", "Пульт Р-202 — Автомат отключён, нет данных (S3d)", {}),
]

RUNTIME = r"""
<script>
// Мини-рендерер шаблонов макета: {{path}}, <sc-if value>, <sc-for list as>. Только для просмотра макета.
class DCLogic { constructor(){ this.props = {}; this.state = {}; } setState(){} }
function lookup(scope, expr){
  expr = expr.trim();
  if (expr === 'true') return true; if (expr === 'false') return false;
  if (/^-?\d+(\.\d+)?$/.test(expr)) return Number(expr);
  return expr.split('.').reduce((o, k) => (o == null ? undefined : o[k]), scope);
}
const HOLE = /\{\{([^}]+)\}\}/g;
function interp(str, scope){
  const whole = str.match(/^\{\{([^}]+)\}\}$/);
  if (whole) return lookup(scope, whole[1]);
  return str.replace(HOLE, (_, e) => { const v = lookup(scope, e); return v == null ? '' : String(v); });
}
function renderNode(node, scope, out){
  if (node.nodeType === 3){ out.push(document.createTextNode(interp(node.nodeValue, scope) ?? '')); return; }
  if (node.nodeType !== 1) return;
  const tag = node.localName;
  if (tag === 'sc-if'){
    if (lookup(scope, node.getAttribute('value').replace(/[{}]/g, ''))) node.childNodes.forEach(c => renderNode(c, scope, out));
    return;
  }
  if (tag === 'sc-for'){
    const list = lookup(scope, node.getAttribute('list').replace(/[{}]/g, '')) || [];
    const as = node.getAttribute('as');
    list.forEach((item, i) => { const s = Object.assign(Object.create(scope), { [as]: item, $index: i }); node.childNodes.forEach(c => renderNode(c, s, out)); });
    return;
  }
  const el = node.cloneNode(false);
  for (const a of Array.from(el.attributes)){
    if (a.name.startsWith('hint-')) { el.removeAttribute(a.name); continue; }
    const v = interp(a.value, scope);
    if (typeof v === 'function') { el.removeAttribute(a.name); continue; }
    if (a.name === 'defaultvalue') { el.removeAttribute(a.name); el.setAttribute('value', v); continue; }
    if (a.name === 'defaultchecked') { el.removeAttribute(a.name); if (v) el.setAttribute('checked', ''); continue; }
    el.setAttribute(a.name, v == null ? '' : String(v));
  }
  node.childNodes.forEach(c => { const kids = []; renderNode(c, scope, kids); kids.forEach(k => el.appendChild(k)); });
  out.push(el);
}
function renderMockup(){
  const vals = new Component().renderVals();
  const tpl = document.getElementById('tpl').content;
  const out = []; tpl.childNodes.forEach(c => renderNode(c, vals, out));
  const root = document.getElementById('root'); out.forEach(n => root.appendChild(n));
}
</script>
"""


def convert(src: Path, title: str, replaces: dict[str, str]) -> str:
    s = src.read_text(encoding="utf-8")
    for a, b in replaces.items():
        s = s.replace(a, b)
    helmet = re.search(r"<helmet>(.*?)</helmet>", s, re.S).group(1)
    body = re.search(r"</helmet>(.*?)</x-dc>", s, re.S).group(1)
    logic = re.search(r"<script type=\"text/x-dc\"[^>]*>(.*?)</script>", s, re.S).group(1)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<!-- Автономная копия экрана из Design-холста «Пульт оператора Р-202». Эталон вёрстки для агентов.
     Числа иллюстративные — брать из API, не из макета. Сгенерировано agents/console_tz/mockups/_build.py -->
{helmet}
</head>
<body>
<div id="root"></div>
<template id="tpl">{body}</template>
{RUNTIME}
<script>
{logic}
renderMockup();
</script>
</body>
</html>
"""


def main() -> None:
    src_dir = Path(sys.argv[1])
    for src, dst, title, rep in SCREENS:
        (OUT / dst).write_text(convert(src_dir / src, title, rep), encoding="utf-8")
        print("ok", dst)


if __name__ == "__main__":
    main()
