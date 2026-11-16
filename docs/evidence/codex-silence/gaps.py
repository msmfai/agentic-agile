import sys,json
for fn in sys.argv[1:]:
    rows=[l.rstrip("\n").split("\t",2) for l in open(fn)]
    ts=[float(r[0]) for r in rows]; gaps=[ts[0]]+[b-a for a,b in zip(ts,ts[1:])]
    types=[]
    for r in rows:
        try:
            d=json.loads(r[2]); t=d.get("type"); it=d.get("item",{}).get("type") or d.get("part",{}).get("type")
            types.append(f"{t}/{it}" if it else str(t))
        except Exception: types.append("?")
    i=max(range(len(gaps)),key=lambda k:gaps[k])
    print(fn,"lines",len(rows),"total %.1f"%ts[-1],"maxgap %.1f before line %d (%s)"%(gaps[i],i,types[i]))
    print("   ",sorted(set(types)))
