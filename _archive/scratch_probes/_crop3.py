from PIL import Image
im = Image.open(r"C:\Dev\vllm\runs\recon_20260620_233754\01_home.png")
# top-left: column-letter row + row-number column + first ~9 rows, columns A-F
im.crop((0, 92, 470, 250)).resize((1410, 474)).save(r"C:\Dev\vllm\_dbg\topleft.png")
print("done")
