import sys, time
# fake LSP: answers initialize, otherwise idle
import json
def rd():
    h={}
    while True:
        l=sys.stdin.buffer.readline()
        if not l: return None
        l=l.strip()
        if not l: break
        k,_,v=l.partition(b":"); h[k.lower()]=v.strip()
    return json.loads(sys.stdin.buffer.read(int(h[b"content-length"])))
def wr(o):
    b=json.dumps(o).encode(); sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n"%len(b)+b); sys.stdout.buffer.flush()
while True:
    m=rd()
    if m is None: break
    if m.get("method")=="initialize": wr({"jsonrpc":"2.0","id":m["id"],"result":{"capabilities":{"textDocumentSync":1}}})
    elif "id" in m and "method" in m: wr({"jsonrpc":"2.0","id":m["id"],"result":None})
