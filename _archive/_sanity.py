import sys, os, json, urllib.request, socket
os.chdir(r"C:\Dev\vllm")
print("PY", sys.version.split()[0])
print("CWD", os.getcwd(), "measure.py?", os.path.exists("measure.py"))
for m in ("cv2", "numpy", "PIL"):
    try:
        __import__(m); print("dep ok:", m)
    except Exception as e:
        print("dep ERR:", m, repr(e))
try:
    import pico_env, r4_client
    print("rig import ok; PICO", r4_client.R4_IP, r4_client.R4_PORT)
except Exception as e:
    print("rig import ERR:", repr(e))
try:
    r = urllib.request.urlopen("http://192.168.0.155:11434/api/tags", timeout=10)
    names = sorted(x["name"] for x in json.load(r).get("models", []))
    print("OLLAMA ok, models:", names)
except Exception as e:
    print("OLLAMA ERR:", repr(e))
try:
    s = socket.create_connection((r4_client.R4_IP, r4_client.R4_PORT), timeout=4)
    s.close(); print("PICO tcp ok:", r4_client.R4_IP)
except Exception as e:
    print("PICO tcp ERR:", repr(e))
print("SANITY DONE")
