"""Generate media/loop.svg, the loop diagram in the README.

Run: python3 media/loop.py
"""
from pathlib import Path

BG, PANEL, LINE = "#0b1018", "#111a26", "#2b3a4d"
TEXT, MUTED, TEAL, AMBER, GREEN, RED = "#e7edf5", "#8fa3b8", "#67e8df", "#f5b942", "#4ade80", "#f87171"
COLORS = {"teal": TEAL, "muted": MUTED, "dim": LINE, "green": GREEN}
W, H = 1000, 720
CW, CH = 400, 236
CARDS = {"hyp": (40, 40), "choose": (560, 40), "do": (560, 388), "keep": (40, 388)}
DASH = ' stroke-dasharray="4 4"'

# The small work graph drawn in three of the cards: six nodes, six edges.
NW, NH = 58, 26
POS = {"a": (0, 0), "b": (0, 46), "c": (0, 92), "d": (122, 18), "e": (122, 74), "f": (244, 46)}
EDGES = [("a", "d"), ("b", "d"), ("b", "e"), ("c", "e"), ("d", "f"), ("e", "f")]

out = []
a = out.append


def graph(ox, oy, nodes, edge="muted", dashed=False):
    """nodes maps a node (or "*") to (stroke, fill, dashed, opacity)."""
    for s, t in EDGES:
        x1, y1 = ox + POS[s][0] + NW, oy + POS[s][1] + NH / 2
        x2, y2 = ox + POS[t][0] - 4, oy + POS[t][1] + NH / 2
        a(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COLORS[edge]}" stroke-width="1.6"'
          f'{DASH if dashed else ""} marker-end="url(#a-{edge})"/>')
    for n, (x, y) in POS.items():
        stroke, fill, dash, opacity = nodes.get(n, nodes["*"])
        a(f'<rect x="{ox + x}" y="{oy + y}" width="{NW}" height="{NH}" rx="6" fill="{fill}" stroke="{stroke}" '
          f'stroke-width="1.8" opacity="{opacity}"{DASH if dash else ""}/>')


def card(key, number, title, caption):
    x, y = CARDS[key]
    a(f'<rect x="{x}" y="{y}" width="{CW}" height="{CH}" rx="14" fill="{PANEL}" stroke="{LINE}" stroke-width="1.5"/>')
    a(f'<circle cx="{x + 30}" cy="{y + 32}" r="14" fill="none" stroke="{TEAL}" stroke-width="1.8"/>')
    a(f'<text x="{x + 30}" y="{y + 37}" text-anchor="middle" font-size="15" font-weight="700" fill="{TEAL}">{number}</text>')
    a(f'<text x="{x + 54}" y="{y + 38}" font-size="20" font-weight="700" fill="{TEXT}">{title}</text>')
    a(f'<text x="{x + 24}" y="{y + 66}" font-size="13.5" fill="{MUTED}">{caption}</text>')
    return x, y


def footnote(x, y, text, color=MUTED, bold=False):
    weight = ' font-weight="700"' if bold else ""
    a(f'<text x="{x}" y="{y}" text-anchor="middle" font-size="12.5"{weight} fill="{color}">{text}</text>')


def arrow(x1, y1, x2, y2):
    a(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{TEAL}" stroke-width="3" marker-end="url(#a-teal)"/>')


a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
  'aria-labelledby="t d" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif">')
a('<title id="t">The Agentic Agile loop</title>')
a('<desc id="d">A loop of four steps around a fixed goal. One: write the plan as a graph of tasks. '
  'Two: choose the ready task that can teach you the most, shown as one green task in the graph. '
  'Three: do that one task and check how the result works. Four: keep the finding and clear the rest of the plan, '
  'then go back to step one. When the plan is empty and the goal is met, the work is done.</desc>')
a("<defs>")
for name, color in COLORS.items():
    a(f'<marker id="a-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
      f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{color}"/></marker>')
a("</defs>")
a(f'<rect width="{W}" height="{H}" rx="18" fill="{BG}"/>')

# 1. Hypothesise: the whole work graph.
x, y = card("hyp", 1, "Write the plan", "Write the tasks that the goal needs, and their order.")
graph(x + 49, y + 88, {"*": (TEAL, "none", False, 1)})
footnote(x + CW / 2, y + 222, "a graph of tasks, your best estimate from what you know now")

# 2. Choose: ready nodes outlined, the most informative one green.
x, y = card("choose", 2, "Choose a task", "Choose the ready task that can teach you the most.")
graph(x + 49, y + 88, {"*": (LINE, "none", False, 1), "a": (AMBER, "none", False, 1),
                       "c": (AMBER, "none", False, 1), "b": (GREEN, GREEN, False, 1)}, edge="dim")
bx, by = x + 49 + POS["b"][0], y + 88 + POS["b"][1]
a(f'<text x="{bx + NW / 2}" y="{by + 18}" text-anchor="middle" font-size="12" font-weight="700" fill="{BG}">next</text>')
for lx, stroke, fill, label in ((24, GREEN, GREEN, "teaches the most"), (160, AMBER, "none", "ready"),
                                (232, LINE, "none", "waits for other tasks")):
    a(f'<rect x="{x + lx}" y="{y + 211}" width="12" height="12" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>')
    a(f'<text x="{x + lx + 18}" y="{y + 222}" font-size="12.5" fill="{MUTED}">{label}</text>')

# 3. Do one block: the block, then what actually happened.
x, y = card("do", 3, "Do one task", "Do that one task. Check how the result works.")
a(f'<rect x="{x + 38}" y="{y + 116}" width="96" height="42" rx="8" fill="{GREEN}"/>')
a(f'<text x="{x + 86}" y="{y + 142}" text-anchor="middle" font-size="14" font-weight="700" fill="{BG}">one task</text>')
a(f'<line x1="{x + 138}" y1="{y + 137}" x2="{x + 182}" y2="{y + 137}" stroke="{MUTED}" stroke-width="1.8" marker-end="url(#a-muted)"/>')
a(f'<rect x="{x + 190}" y="{y + 90}" width="180" height="94" rx="8" fill="{BG}" stroke="{LINE}" stroke-width="1.5"/>')
a(f'<text x="{x + 204}" y="{y + 111}" font-size="10.5" font-weight="700" fill="{MUTED}" letter-spacing="1">WHAT HAPPENED</text>')
for i, (mark, color, label) in enumerate((("✓", GREEN, "tests pass"), ("✓", GREEN, "result checked"),
                                          ("?", AMBER, "open question"))):
    a(f'<text x="{x + 206}" y="{y + 134 + i * 19}" font-size="13" font-weight="700" fill="{color}">{mark}</text>')
    a(f'<text x="{x + 224}" y="{y + 134 + i * 19}" font-size="13" fill="{TEXT}">{label}</text>')
footnote(x + CW / 2, y + 222, "look at how it works, in addition to the tests")

# 4. Keep the finding, discard the rest of the graph.
x, y = card("keep", 4, "Keep the finding. Clear the plan.", "The finding is permanent. The plan was an estimate.")
fx, fy = x + 30, y + 88
a(f'<path d="M{fx} {fy}h62l18 18v84h-80z" fill="{BG}" stroke="{TEAL}" stroke-width="1.8"/>')
a(f'<path d="M{fx + 62} {fy}v18h18" fill="none" stroke="{TEAL}" stroke-width="1.8"/>')
for i, w in enumerate((48, 56, 40, 52)):
    a(f'<line x1="{fx + 12}" y1="{fy + 36 + i * 15}" x2="{fx + 12 + w}" y2="{fy + 36 + i * 15}" stroke="{TEAL}" '
      'stroke-width="2.4" stroke-linecap="round"/>')
footnote(fx + 40, y + 222, "finding · kept", TEAL, True)
gx, gy = x + 150, y + 88
graph(gx, gy, {"*": (MUTED, "none", True, 0.6), "b": (GREEN, "none", False, 1)}, edge="dim", dashed=True)
a(f'<text x="{gx + NW / 2}" y="{gy + POS["b"][1] + 18}" text-anchor="middle" font-size="13" font-weight="700" fill="{GREEN}">✓</text>')
for x1, x2 in ((gx + 96, gx + 244 + NW), (gx + 244 + NW, gx + 96)):
    a(f'<line x1="{x1}" y1="{gy - 2}" x2="{x2}" y2="{gy + 120}" stroke="{RED}" stroke-width="2.2" opacity="0.85" stroke-linecap="round"/>')
footnote(gx + 190, y + 222, "waiting tasks · cleared", RED, True)

# The loop, clockwise.
(hx, hy), (cx, cy), (dx, dy), (kx, ky) = CARDS["hyp"], CARDS["choose"], CARDS["do"], CARDS["keep"]
arrow(hx + CW + 6, hy + 40, cx - 8, cy + 40)
arrow(cx + CW - 60, cy + CH + 6, dx + CW - 60, dy - 8)
arrow(dx - 6, dy + 40, kx + CW + 8, ky + 40)
arrow(kx + 60, ky - 6, hx + 60, hy + CH + 8)
mid = (hy + CH + ky) / 2
a(f'<text x="{kx + 76}" y="{mid + 5}" font-size="13" fill="{TEAL}">then write</text>')
a(f'<text x="{kx + 76}" y="{mid + 22}" font-size="13" fill="{TEAL}">a new plan</text>')

# The goal sits in the middle of the loop and stays fixed.
mx = W / 2 + 40
a(f'<rect x="{mx - 150}" y="{mid - 30}" width="300" height="60" rx="30" fill="{BG}" stroke="{AMBER}" stroke-width="2"/>')
a(f'<text x="{mx}" y="{mid - 4}" text-anchor="middle" font-size="17" font-weight="700" fill="{AMBER}">THE GOAL</text>')
a(f'<text x="{mx}" y="{mid + 16}" text-anchor="middle" font-size="12.5" fill="{MUTED}">you set it · it stays fixed</text>')

# Exit.
a(f'<line x1="{kx + 200}" y1="{ky + CH + 4}" x2="{kx + 200}" y2="{ky + CH + 34}" stroke="{GREEN}" stroke-width="2.4" '
  f'stroke-dasharray="5 4" marker-end="url(#a-green)"/>')
a(f'<rect x="{kx + 30}" y="{ky + CH + 40}" width="340" height="36" rx="18" fill="none" stroke="{GREEN}" stroke-width="1.8"/>')
a(f'<text x="{kx + 200}" y="{ky + CH + 63}" text-anchor="middle" font-size="13.5" fill="{GREEN}">the plan is empty and the goal is met → done</text>')
a("</svg>")

Path(__file__).with_name("loop.svg").write_text("\n".join(out) + "\n", encoding="utf-8")
