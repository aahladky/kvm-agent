from PIL import Image
im = Image.open(r"C:\Dev\vllm\runs\recon_20260620_233754\02_end.png")
# Name box (top-left, below ribbon)
im.crop((0, 44, 150, 78)).resize((600, 136)).save(r"C:\Dev\vllm\_dbg\namebox.png")
# bottom-left data + row numbers
im.crop((0, 250, 520, 460)).resize((1560, 630)).save(r"C:\Dev\vllm\_dbg\bottomrows.png")
# sheet tabs at the very bottom
im.crop((0, 455, 360, 490)).resize((1080, 105)).save(r"C:\Dev\vllm\_dbg\tabs.png")
print("done")
