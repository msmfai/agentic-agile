import json,re,sys,time,calendar
IGN=re.compile(r"cmux|node_repl|cua_node|cua-repl|ChatGPT\.app")
def parse_ls(s): # local time lstart
    return time.mktime(time.strptime(s,"%a %b %d %H:%M:%S %Y"))
def et(x):
    d=0
    if '-' in x: d,x=x.split('-'); d=int(d)
    p=[int(v) for v in x.split(':')]
    while len(p)<3: p.insert(0,0)
    return d*86400+p[0]*3600+p[1]*60+p[2]
def load(f):
    ev=[];ps=[]
    for l in open(f):
        t,w,r=l.split('\t',2)
        try: j=json.loads(r)
        except: continue
        ev.append((float(t),float(w),j))
    for l in open(f+".ps"):
        t,w,r=l.split('\t',2); ps.append((float(t),float(w),json.loads(r)))
    return ev,ps
def alive(rows):
    return [r for r in rows if not r[1].startswith("Z") and "<defunct>" not in r[4] and not re.fullmatch(r"\([^ ]+\)",r[4]) and not IGN.search(r[4])]
def run(f,rule,slack=1.0):
    ev,ps=load(f); end=ev[-1][0]; pi=ei=0; cur=[]; refw=None; step=0.1; t=0; busy=tr=fp=fn=0.0
    while t<=end:
        while ei<len(ev) and ev[ei][0]<=t:
            typ=ev[ei][2].get("type")
            if rule=="last-event" or typ=="step_start": refw=ev[ei][1]
            ei+=1
        while pi<len(ps) and ps[pi][0]<=t:
            cur=ps[pi]; pi+=1
        rows=alive(cur[2]) if cur else []; w=cur[1] if cur else 0
        kids=[r for r in rows if refw is not None and (w-et(r[2]))>=refw-slack]
        det=bool(kids); truth=any(re.match(r"sleep \d",r[4]) for r in rows)
        busy+=step*det; tr+=step*truth
        if det and not truth: fp+=step
        if truth and not det: fn+=step
        t+=step
    return end,busy,tr,fp,fn
if __name__=="__main__":
    for rule in ("last-event","last-step_start"):
        print(rule)
        for f in sys.argv[1:]:
            print("  %s run %.1f busy %.1f cmd %.1f false-busy %.1f missed %.1f"%((f,)+run(f,rule)))
