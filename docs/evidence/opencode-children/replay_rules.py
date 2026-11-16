"""Replay filter rules for opencode over recorded runs.
Inputs per run: RAW (tab: seconds, event json) and RAW.ps (tab: seconds, PS, json [[pid, stat, command]]).
truth = a 'sleep N' row is present (the command the prompt asked for)."""
import json, re, sys, glob, os
IGNORE = re.compile(r"cmux|node_repl|cua_node|cua-repl|ChatGPT\.app")
def load(f):
    ev = []
    for l in open(f):
        t, _, r = l.partition("\t")
        try: ev.append((float(t), json.loads(r)))
        except Exception: pass
    ps = []
    for l in open(f + ".ps"):
        t, _, r = l.split("\t", 2); ps.append((float(t), json.loads(r)))
    return ev, ps
def alive(rows):
    return {c: k for c, s, k in rows if not s.startswith("Z") and "<defunct>" not in k and not re.fullmatch(r"\([^ ]+\)", k) and not IGNORE.search(k)}
def run(f, rule):
    ev, ps = load(f); end = ev[-1][0] if ev else ps[-1][0]
    first = {}; base = None; last_ev = None; pi = ei = 0; cur = []
    step = 0.1; t = 0.0; busy = truth_s = fp = fn = 0.0; fp_after = 0.0
    while t <= end:
        while ei < len(ev) and ev[ei][0] <= t:
            last_ev = ev[ei][0]; ei += 1
            if base is None: base = set(alive(cur))
        while pi < len(ps) and ps[pi][0] <= t:
            cur = ps[pi][1]
            for c, s, k in cur: first.setdefault(c, ps[pi][0])
            pi += 1
        rows = alive(cur)
        if rule == "ignore-only": kids = rows
        elif rule == "baseline": kids = {c: k for c, k in rows.items() if base is not None and c not in base} if base is not None else {}
        elif rule == "younger": kids = {c: k for c, k in rows.items() if last_ev is not None and first[c] > last_ev}
        elif rule == "younger+baseline": kids = {c: k for c, k in rows.items() if last_ev is not None and first[c] > last_ev and c not in (base or ())}
        det = bool(kids)
        tr = any(re.match(r"sleep \d", k) for c, k in rows.items())
        busy += step * det; truth_s += step * tr
        if det and not tr: fp += step
        if tr and not det: fn += step
        t += step
    return end, busy, truth_s, fp, fn
if __name__ == "__main__":
    files = sys.argv[1:]
    for rule in ("ignore-only", "baseline", "younger", "younger+baseline"):
        print("RULE", rule)
        for f in files:
            end, busy, tr, fp, fn = run(f, rule)
            print(f"  {os.path.basename(f):16s} run {end:5.1f}s  counted-busy {busy:5.1f}s  command-running {tr:5.1f}s  false-busy {fp:5.1f}s  missed {fn:5.1f}s")
