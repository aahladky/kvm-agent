import glob, os
from PIL import Image
dd = sorted(glob.glob(r"C:\Dev\vllm\runs\fixH_*"))[-1]
im = Image.open(os.path.join(dd, "01_fixed.png"))
im.crop((150, 250, 760, 560)).resize((1830, 930)).save(r"C:\Dev\vllm\_dbg\res_fixed.png")
print("done", dd)
