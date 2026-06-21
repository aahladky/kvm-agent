from PIL import Image
im = Image.open(r"C:\Dev\vllm\runs\recover_20260620_234802\00_after_undo.png")
# column-letter row + title + header(row4) + first data rows, columns A..J
im.crop((0, 104, 800, 340)).resize((2000, 590)).save(r"C:\Dev\vllm\_dbg\colmap.png")
print("done")
