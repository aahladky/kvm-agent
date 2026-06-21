from PIL import Image
im = Image.open(r"C:\Dev\vllm\_dbg\probe_frame.png")
W, H = im.size
print("size", W, H)
# left data table, upscaled
im.crop((0, 90, 760, 760)).resize((1520, 1340)).save(r"C:\Dev\vllm\_dbg\crop_data.png")
# right task panel, upscaled
im.crop((740, 90, 1700, 720)).resize((1920, 1260)).save(r"C:\Dev\vllm\_dbg\crop_tasks.png")
print("done")
