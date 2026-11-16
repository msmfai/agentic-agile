import subprocess,sys,time,json,threading,os,re
prompt=sys.argv[1]; raw=sys.argv[2]; extra=sys.argv[3:]
cwd="/tmp/oc-par/proj"
p=subprocess.Popen(["opencode","run","--pure","--auto","--format","json","--dir",cwd]+extra+[prompt],cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
t0=time.time()
def ps():
    out=subprocess.run(["ps","-A","-o","pid=,ppid=,stat=,etime=,lstart=,command="],text=True,capture_output=True,env={**os.environ,"LC_ALL":"C"}).stdout
    rows={}
    for l in out.splitlines():
        m=re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\w{3} \w{3}\s+\d+ \d\d:\d\d:\d\d \d{4})\s+(.*)",l)
        if m: rows[int(m[1])]=(int(m[2]),m[3],m[4],m[5],m[6][:120])
    below={p.pid};res=[]
    for c in sorted(rows):
        if rows[c][0] in below: below.add(c);res.append([c,rows[c][1],rows[c][2],rows[c][3],rows[c][4]])
    return res
stop=False
f2=open(raw+".ps","w")
def sampler():
    while not stop:
        f2.write(f"{time.time()-t0:.3f}\t{time.time():.3f}\t"+json.dumps(ps())+"\n"); f2.flush(); time.sleep(0.25)
threading.Thread(target=sampler,daemon=True).start()
f=open(raw,"w")
for line in p.stdout:
    f.write(f"{time.time()-t0:.3f}\t{time.time():.3f}\t{line}"); f.flush()
stop=True;p.wait();print("exit",p.returncode,time.time()-t0)
