"""usage: record.py HOST OUT PROMPT  (HOST codex|opencode). Writes OUT with 'elapsed<TAB>epoch<TAB>line' per stdout line."""
import subprocess,sys,time,os
host,out,prompt=sys.argv[1:4]
cwd=f"/tmp/cs-{os.path.basename(out)}"
os.makedirs(cwd,exist_ok=True)
subprocess.run(["git","init","-q"],cwd=cwd)
if host=="codex":
    cmd=["codex","exec","--json","--skip-git-repo-check","--ephemeral","-s","workspace-write","-C",cwd,prompt]
else:
    cmd=["opencode","run","--pure","--auto","--format","json","-m","deepseek/deepseek-flash","--dir",cwd,prompt]
p=subprocess.Popen(cmd,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
t0=time.time(); f=open(out,"w")
for line in p.stdout:
    f.write(f"{time.time()-t0:.3f}\t{time.time():.3f}\t{line}"); f.flush()
p.wait(); f.write(f"{time.time()-t0:.3f}\t{time.time():.3f}\t#exit {p.returncode}\n")
