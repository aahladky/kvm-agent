from PIL import Image
im = Image.open(r"C:\Dev\vllm\runs\practice2_20260620_235050\01_filled.png")
# columns ~E..J, header + first rows, to read Profit / Profit% / Weekday
im.crop((150, 250, 760, 560)).resize((1830, 930)).save(r"C:\Dev\vllm\_dbg\res.png")
print("done")
