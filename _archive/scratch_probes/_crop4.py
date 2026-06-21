from PIL import Image
im = Image.open(r"C:\Dev\vllm\runs\recon_20260620_233754\01_home.png")
# row-number gutter + first columns + first ~16 rows
im.crop((0, 244, 560, 420)).resize((1680, 528)).save(r"C:\Dev\vllm\_dbg\rownums.png")
print("done")
