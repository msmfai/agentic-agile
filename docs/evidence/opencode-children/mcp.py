import sys, json, time
for line in sys.stdin:
    try: m = json.loads(line)
    except Exception: continue
    i, meth = m.get("id"), m.get("method")
    def send(r): print(json.dumps({"jsonrpc":"2.0","id":i,"result":r}), flush=True)
    if meth == "initialize": send({"protocolVersion": m["params"]["protocolVersion"], "capabilities":{"tools":{}}, "serverInfo":{"name":"slowmcp","version":"1"}})
    elif meth == "tools/list": send({"tools":[{"name":"slow_echo","description":"Wait 10 seconds then echo text","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}]})
    elif meth == "tools/call":
        time.sleep(10); send({"content":[{"type":"text","text":"echo: "+str(m["params"]["arguments"].get("text"))}]})
    elif i is not None: send({})
