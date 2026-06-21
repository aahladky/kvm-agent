from PIL import Image
im = Image.open(r"C:\Dev\vllm\runs\practice_20260620_234300\01_filled.png")
print("size", im.size)
# header + first ~12 data rows across columns A..I (left portion of the grid)
im.crop((20, 250, 700, 640)).resize((2040, 1170)).save(r"C:\Dev\vllm\_dbg\diag.png")
print("done")
