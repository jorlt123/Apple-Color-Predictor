#!/usr/bin/env python3
"""
Predictor v2: de los ~37.000 colores válidos, elige los 20 que Apple tiene más
probabilidades de lanzar en sus próximos iPhone (años 2027+).

Se apoya en apple_color_generator.py (conversiones de color e histórico) y añade
más factores. Cada candidato se puntúa por separado para dos líneas de producto:

  PRO       -> acabados metálicos (titanio/aluminio anodizado): oscuros o medios, croma contenido
  ESTÁNDAR  -> aluminio + vidrio teñido en masa: más claros, pasteles y algún tono vivo

FACTORES (peso)
  1. Novedad vs. histórico, ponderada por recencia              (0.30)
  2. Encaje con la "zona Apple" de luminosidad/croma por línea  (0.24)
  3. Hueco de tono: huecos libres en los últimos 3 años         (0.12)
  4. Venta: tonos que históricamente se venden mejor (supuesto) (0.12)
  5. Tonos del sistema iOS (Apple casa hardware y software)     (0.09)
  6. Nostalgia: familias de hace 4+ años que Apple suele rescatar (0.06)
  -  Penalización "barro": marrones/oliva apagados que se ven sucios (-0.25)
  -  Penalización de viabilidad de material (anodizado muy saturado, etc.) (-0.25)

Los pesos y la tabla de ventas son supuestos míos, no datos de Apple: ajústalos.

Uso:  python3 apple_color_predictor.py [--html] [--per-tier 10]
"""
import argparse
import math
import random

from apple_color_generator import (
    HISTORY, HUE_NAMES, gauss, hex_to_rgb, rgb_to_oklab, rgb_to_hex,
    oklab_to_rgb_unclipped, lch_to_lab, in_gamut, lab_dist, _to_srgb,
)

NOW = 2026  # año del último lanzamiento conocido

# Histórico: el de la v1 + colores de 2025 que faltaban + lanzamiento Pro de 2026
EXTRA = [
    (2025, "Deep Blue",    "#2E3F5C"),
    (2025, "Silver",       "#D9DADC"),
    (2025, "Black",        "#1F1F21"),
    (2025, "White",        "#F2F1EC"),
    (2026, "Deep Black",   "#232325"),
    (2026, "Silver",       "#D6D7D9"),
    (2026, "Glacier Blue", "#A8C4D8"),
    (2026, "Burgundy",     "#6E2230"),
]
HIST = [(int(n[:4]), n[5:], h) for n, h in HISTORY] + EXTRA
HIST = [(y, n, h, rgb_to_oklab(hex_to_rgb(h))) for y, n, h in HIST]


def hue_of(lab):
    return math.degrees(math.atan2(lab[2], lab[1])) % 360


def chroma_of(lab):
    return math.hypot(lab[1], lab[2])


def circ(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def recency(year):
    return math.exp(-(NOW - year) / 2.0)


# ------------------------------- factores ---------------------------------
def f_novelty(lab):
    best = 9.0
    for y, _, _, hl in HIST:
        eff = lab_dist(lab, hl) / (0.45 + 1.0 * recency(y))
        best = min(best, eff)
    return min(best / 0.17, 1.0)


def f_nostalgia(lab):
    near_recent = min(lab_dist(lab, hl) for y, _, _, hl in HIST if NOW - y <= 2)
    if near_recent < 0.10:
        return 0.0
    return max(gauss(lab_dist(lab, hl), 0, 0.08) for y, _, _, hl in HIST if NOW - y >= 4)


def f_hue_gap(h, C):
    if C < 0.035:
        return 0.4
    dens = 0.0
    for y, _, _, hl in HIST:
        if NOW - y <= 3 and chroma_of(hl) > 0.035:
            dens += recency(y) * gauss(circ(h, hue_of(hl)), 0, 28)
    return 1.0 - min(1.0, dens / 1.2)


APPLE_HUES = [27, 55, 95, 145, 175, 200, 225, 255, 275, 305, 350]


def f_system_hue(h, C):
    if C < 0.035:
        return 0.6
    return max(gauss(circ(h, x), 0, 16) for x in APPLE_HUES)


# SUPUESTO: popularidad relativa de venta por familia de tono (azul/neutros mandan)
SALES = [(0, .65), (27, .65), (55, .60), (95, .40), (145, .70), (195, .70),
         (255, 1.0), (290, .75), (340, .70)]


def f_sales(h, C):
    num = sum(v * gauss(circ(h, c), 0, 25) for c, v in SALES)
    den = sum(gauss(circ(h, c), 0, 25) for c, _ in SALES)
    hue_w = num / den
    t = min(1.0, C / 0.06)
    return 0.9 * (1 - t) + hue_w * t


def p_mud(L, C, h):
    return gauss(h, 80, 28) * gauss(L, 0.50, 0.10) * min(1.0, C / 0.05)


def taste(tier, L, C):
    if tier == "PRO":
        return gauss(L, 0.55, 0.16) * gauss(C, 0.07, 0.04)
    pastel = gauss(L, 0.78, 0.10) * gauss(C, 0.085, 0.045)
    vivid = 0.7 * gauss(L, 0.68, 0.09) * gauss(C, 0.14, 0.04)
    return max(pastel, vivid)


def p_feasible(tier, L, C):
    if tier == "PRO":
        pen = max(0, C - 0.115) / 0.05 + max(0, L - 0.80) / 0.10
    else:
        pen = max(0, C - 0.17) / 0.05 + max(0, 0.46 - L) / 0.05
    return min(1.0, pen)


W = dict(nov=.30, taste=.24, gap=.12, sales=.12, sys=.09, nost=.06)
POS = sum(W.values())


def score(tier, L, C, h, lab):
    f = dict(
        nov=f_novelty(lab), taste=taste(tier, L, C), gap=f_hue_gap(h, C),
        sales=f_sales(h, C), sys=f_system_hue(h, C), nost=f_nostalgia(lab),
    )
    mud, feas = p_mud(L, C, h), p_feasible(tier, L, C)
    raw = sum(W[k] * f[k] for k in W) - 0.25 * mud - 0.25 * feas
    f["mud"], f["feas"] = mud, feas
    return max(0.0, raw) / POS * 100, f


# ------------------------------ candidatos --------------------------------
def candidates():
    out = []
    for h in range(0, 360, 3):
        for C100 in range(2, 21):
            C = C100 / 100
            for L100 in range(42, 95, 2):
                L = L100 / 100
                lab = lch_to_lab(L, C, h)
                lin = oklab_to_rgb_unclipped(*lab)
                if in_gamut(lin):
                    out.append((L, C, h, lab, lin))
    return out


def select(tier, cands, n, min_sep=0.08, bucket_cap=2):
    ranked = []
    for L, C, h, lab, lin in cands:
        s, f = score(tier, L, C, h, lab)
        ranked.append((s, L, C, h, lab, lin, f))
    ranked.sort(key=lambda t: t[0], reverse=True)
    chosen, buckets = [], {}
    for item in ranked:
        s, L, C, h, lab, lin, f = item
        key = "neutral" if C < 0.035 else int(h // 45)
        if buckets.get(key, 0) >= bucket_cap:
            continue
        if all(lab_dist(lab, c[4]) >= min_sep for c in chosen):
            chosen.append(item)
            buckets[key] = buckets.get(key, 0) + 1
        if len(chosen) == n:
            break
    return chosen


# -------------------------------- nombres ---------------------------------
NEUTRAL_WORDS = ["Graphite", "Smoke", "Stone", "Mist", "Slate", "Dune"]
# Tabla de nombres por tono OKLCH (rojo~29, naranja~55, amarillo~100, verde~145, cian~195, azul~260, púrpura~305, rosa~345)
HUE_WORDS = [
    (20,  ["Rose", "Blush", "Berry"]),
    (45,  ["Rust", "Terracotta", "Copper", "Ember"]),
    (75,  ["Amber", "Apricot", "Saffron"]),
    (110, ["Brass", "Olive", "Pistachio", "Citrine"]),
    (160, ["Fern", "Moss", "Verdigris", "Pine"]),
    (200, ["Lagoon", "Teal", "Jade", "Glacier"]),
    (235, ["Azure", "Sky", "Lagoon"]),
    (275, ["Indigo", "Cobalt", "Denim", "Horizon"]),
    (320, ["Iris", "Plum", "Orchid", "Amethyst"]),
    (361, ["Mauve", "Fuchsia", "Rose", "Orchid"]),
]
FINISH = {"PRO": ["Titanium", "Ceramic", "Steel"], "STD": ["Aluminium", "Glass", "Anodized"]}


def make_name(tier, L, C, h, rng, used):
    if C < 0.035:
        word = rng.choice(NEUTRAL_WORDS)
    else:
        word = rng.choice(next(n for lim, n in HUE_WORDS if h < lim))
    prefix = "Deep " if L < 0.55 else "Soft " if L > 0.82 else ""
    for _ in range(20):
        name = f"{prefix}{word} {rng.choice(FINISH[tier])}"
        if name not in used:
            used.add(name)
            return name
    name = f"{prefix}{word} {len(used)}"
    used.add(name)
    return name


def swatch(hx):
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return f"\x1b[48;2;{r};{g};{b}m      \x1b[0m"


def write_html(rows, path="apple_colors_top20.html"):
    labels = dict(nov="Novelty", taste="Apple zone", gap="Hue gap",
                  sales="Sales", sys="iOS hue", nost="Nostalgia")
    def card(r, num=None, badge=False):
        bars = "".join(
            f'<div class="b"><i>{labels[k]}</i><u><em style="width:{r["f"][k]*100:.0f}%"></em></u></div>'
            for k in labels)
        tag = f'<small class="tag {r["tier"]}">{"Pro" if r["tier"] == "PRO" else "Standard"}</small>' if badge else ""
        n = r["rank"] if num is None else num
        return (f'<div class="c"><div class="s" style="background:{r["hex"]}"></div>'
                f'<b>#{n} {r["name"]}</b>{tag}<span>{r["hex"]} · index {r["score"]:.0f}</span>{bars}</div>')
    by_line = ""
    for tier, title in (("PRO", "Pro line (metallic finishes)"), ("STD", "Standard line (aluminium + glass)")):
        by_line += f"<h2>{title}</h2><div class=g>{''.join(card(r) for r in rows if r['tier']==tier)}</div>"
    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    ranking = ("<h2>All 20 colors, highest to lowest score</h2><div class=g>"
               + "".join(card(r, i, True) for i, r in enumerate(ranked, 1)) + "</div>")
    sec = (f'<div class="tabs"><button id="t1" class="on" onclick="show(1)">By line</button>'
           f'<button id="t2" onclick="show(2)">Ranking by score</button></div>'
           f'<div id="p1" class="panel on">{by_line}</div><div id="p2" class="panel">{ranking}</div>'
           '<script>function show(n){for(const i of [1,2]){'
           'document.getElementById("t"+i).classList.toggle("on",i===n);'
           'document.getElementById("p"+i).classList.toggle("on",i===n);}}</script>')
    hist = "".join(f'<div class="h" style="background:{h}" title="{y} {n}"></div>' for y, n, h, _ in HIST)
    html = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Top 20 iPhone colors</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:32px}}
h1{{font-weight:600}} h2{{font-weight:500;margin-top:40px}} .g{{display:flex;flex-wrap:wrap;gap:22px}}
.c{{width:190px;display:flex;flex-direction:column;gap:5px}} .s{{height:170px;border-radius:30px;margin-bottom:6px}}
span{{opacity:.65;font-size:12px;margin-bottom:4px}} .b{{display:flex;align-items:center;gap:6px;font-size:10px;opacity:.85}}
.b i{{width:72px;font-style:normal}} .b u{{flex:1;height:5px;background:#2a2a2a;border-radius:3px;overflow:hidden}}
.b em{{display:block;height:100%;background:#8ab4ff}} .hs{{display:flex;gap:6px;flex-wrap:wrap}}
.h{{width:30px;height:30px;border-radius:50%}}
.tabs{{display:flex;gap:8px;margin-top:28px}}
.tabs button{{background:#1d1d1f;color:#bbb;border:1px solid #333;border-radius:999px;padding:8px 18px;font:inherit;font-size:14px;cursor:pointer}}
.tabs button.on{{background:#eee;color:#111;border-color:#eee}}
.panel{{display:none}} .panel.on{{display:block}}
.tag{{align-self:flex-start;font-size:10px;padding:2px 8px;border-radius:999px;background:#2a2a2a;opacity:.9}}
.tag.PRO{{background:#2a3550}} .tag.STD{{background:#4a3350}}
</style><h1>The 20 most likely colors for the next iPhones</h1>
<p>Color history used (2019-2026):</p><div class="hs">{hist}</div>{sec}"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-tier", type=int, default=10)
    ap.add_argument("--seed", type=int, default=2027)
    ap.add_argument("--html", action="store_true")
    a = ap.parse_args()

    rng, used = random.Random(a.seed), set()
    cands = candidates()
    print(f"\n{len(cands):,} candidatos válidos | histórico: {len(HIST)} colores (2019-{NOW})")

    rows = []
    for tier, title in (("PRO", "LÍNEA PRO"), ("STD", "LÍNEA ESTÁNDAR")):
        print(f"\n== {title} ==")
        for i, (s, L, C, h, lab, lin, f) in enumerate(select(tier, cands, a.per_tier), 1):
            hx = rgb_to_hex(tuple(_to_srgb(max(0, min(1, c))) for c in lin))
            name = make_name(tier, L, C, h, rng, used)
            rows.append(dict(rank=i, tier=tier, name=name, hex=hx, score=s, f=f, L=L, C=C, h=h))
            print(f"{i:>2}. {swatch(hx)} {hx}  {name:<26} índice={s:5.1f}  "
                  f"(L={L:.2f} C={C:.2f} h={h:3.0f}°)")
    if a.html:
        print("\nHTML:", write_html(rows))


if __name__ == "__main__":
    main()
