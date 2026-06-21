import cv2

# 1. Initialize camera with DirectShow backend (required on Windows for strict FOURCC control)
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)

# 2. Set the codec format to YUY2
fourcc_yuy2 = cv2.VideoWriter_fourcc('Y', 'U', 'Y', '2')
cap.set(cv2.CAP_PROP_FOURCC, fourcc_yuy2)

# 3. Optional: Configure resolution and frame rate
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_FPS, 30)

# Verify if the camera accepted the YUY2 setting
current_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
codec_chars = "".join([chr((current_fourcc >> (8 * i)) & 0xFF) for i in range(4)])
print(f"Active Camera Codec Format: {codec_chars}")

if not cap.isOpened():
    print("Error: Could not open the webcam.")
    exit()

print("Streaming live video... Press 'q' to quit.")

while True:
    # Capture frame-by-frame
    ret, frame = cap.read()
    
    if not ret:
        print("Error: Failed to grab frame.")
        break

    # --- YOUR IMAGE PROCESSING GOES HERE ---
    # At this point, 'frame' is a standard 3-channel BGR matrix 
    # ready for OpenCV functions (e.g., cv2.cvtColor, cv2.GaussianBlur)
    # ---------------------------------------

    # Display the resulting live stream
    cv2.imshow('Live YUY2 Stream (Auto-converted to BGR)', frame)

    # Break loop on 'q' key press
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Clean up and close windows
cap.release()
cv2.destroyAllWindows()