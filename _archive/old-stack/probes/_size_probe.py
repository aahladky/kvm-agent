import os, sys, json, re
import numpy as np
from PIL import Image
from io import BytesIO

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evocua")
sys.path.insert(0, REPO)
os.environ["OPENAI_BASE_URL"] = "http://192.168.0.155:11434/v1"
os.environ["OPENAI_API_KEY"] = "ollama"
from mm_agents.evocua.evocua_agent import EvoCUAAgent

SRC = "runs/20260618_135046/frames/step_02.png"
INSTR = "Using the open Calculator, compute 7 x 8 + 5"
SEED_ACTIONS = ["Click the '7' key.", "Click the multiplication button.", "Click the '8' key."]
N = int(os.environ.get("PROBE_N", "8"))
MODELS = os.environ.get("PROBE_MODELS", "evocua-8b-q5-clean,evocua-8b").split(",")

def load_rgb(p): return Image.open(p).convert("RGB")

def calc_bbox(img):
    a = np.array(img); nb = a.sum(2) > 40
    ys, xs = np.where(nb); m = (ys > 120) & (ys < 1000)
    xs, ys = xs[m], ys[m]; cx = int(np.median(xs))
    near = np.abs(xs - cx) < 220; xs, ys = xs[near], ys[near]
    return xs.min(), ys.min(), xs.max(), ys.max()

def make_big(img, fill=0.9):
    x0,y0,x1,y1 = calc_bbox(img)
    crop = img.crop((x0-6, y0-6, x1+6, y1+6)); cw,ch = crop.size
    s = int(1080*fill)/ch
    c2 = crop.resize((int(cw*s), int(ch*s)))
    cv = Image.new("RGB",(1920,1080),(0,0,0))
    cv.paste(c2, ((1920-c2.size[0])//2, (1080-c2.size[1])//2)); return cv

def png_bytes(img):
    b=BytesIO(); img.save(b,format="PNG"); return b.getvalue()

def cols(img):
    a=np.array(img); R,G,B=a[:,:,0].astype(int),a[:,:,1].astype(int),a[:,:,2].astype(int)
    orange=(R>200)&(G>120)&(G<200)&(B<90); ys,xs=np.where(orange)
    band=(ys>200)&(ys<1010); op_x=int(np.median(xs[band]))
    x0,_,x1,_=calc_bbox(img); pitch=(x1-x0)/4.0
    return op_x, int(op_x-2*pitch), pitch

def xy_of(codes):
    for c in codes:
        m=re.search(r"\((\d+),\s*(\d+)", str(c))
        if m: return int(m.group(1)),int(m.group(2))
    return None

def run(model,img,label,op_x,dg_x,pitch):
    ag=EvoCUAAgent(model=model,max_tokens=512,temperature=0.01,top_p=0.9,prompt_style="S2",
                   max_history_turns=0,screen_size=(1920,1080),coordinate_type="relative",resize_factor=32)
    obs={"screenshot":png_bytes(img)}; rows=[]
    for i in range(N):
        ag.reset(); ag.actions=list(SEED_ACTIONS)
        resp,codes=ag.predict(INSTR,obs); xy=xy_of(codes)
        col="?" 
        if xy: col="OP" if abs(xy[0]-op_x)<abs(xy[0]-dg_x) else "DIGIT"
        rows.append({"xy":xy,"col":col,"code":str(codes)[:70]})
        print(f"  {label:5s} {model:20s} [{i}] xy={xy} -> {col}", flush=True)
    n=sum(r["col"]=="OP" for r in rows)
    print(f"  >>> {label} {model}: {n}/{N} operator-col\n", flush=True)
    return {"model":model,"variant":label,"op_x":op_x,"digit_x":dg_x,"pitch":round(pitch,1),"n_op":n,"N":N,"rows":rows}

def main():
    small=load_rgb(SRC); big=make_big(small); big.save("runs/_size_probe_big.png")
    s=cols(small); b=cols(big)
    print(f"small op_x={s[0]} dg_x={s[1]} pitch={s[2]:.1f}px ({s[2]/1920*999:.1f} grid)",flush=True)
    print(f"big   op_x={b[0]} dg_x={b[1]} pitch={b[2]:.1f}px ({b[2]/1920*999:.1f} grid)\n",flush=True)
    out=[]
    for model in MODELS:
        out.append(run(model,small,"small",*s)); out.append(run(model,big,"big",*b))
    json.dump(out,open("runs/_size_probe_result.json","w"),indent=1)
    print("=== SUMMARY ===",flush=True)
    for o in out: print(f"  {o['variant']:5s} {o['model']:20s} {o['n_op']}/{o['N']} pitch={o['pitch']}px",flush=True)

if __name__=="__main__": main()
