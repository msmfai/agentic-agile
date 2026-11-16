import sys,re,json
sys.path.insert(0,'../opencode-children')
import replay_rules as R
def run(f,rule):
    ev,ps=R.load(f); end=ev[-1][0]; first={}; ref=None; pi=ei=0; cur=[]; t=0; busy=tr=fp=fn=0.0
    while t<=end:
        while ei<len(ev) and ev[ei][0]<=t:
            if rule=="last-event" or ev[ei][1].get("type")=="step_start": ref=ev[ei][0]
            ei+=1
        while pi<len(ps) and ps[pi][0]<=t:
            cur=ps[pi][1]
            for c,s,k in cur: first.setdefault(c,ps[pi][0])
            pi+=1
        rows=R.alive(cur)
        kids={c:k for c,k in rows.items() if ref is not None and first[c]>ref-1.0}
        det=bool(kids); truth=any(re.match(r"sleep \d",k) for k in rows.values())
        busy+=.1*det; tr+=.1*truth
        if det and not truth: fp+=.1
        if truth and not det: fn+=.1
        t+=.1
    return end,busy,tr,fp,fn
for rule in("last-event","last-step_start"):
    print(rule)
    for f in sys.argv[1:]:
        print("  %-14s run %.1f busy %.1f cmd %.1f false-busy %.1f missed %.1f"%((f.split('/')[-1],)+run(f,rule)))
