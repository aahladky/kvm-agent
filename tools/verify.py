"""
verify.py — verify-before-terminate guard for the physical rig.

The agent can emit terminate(success) with the WRONG result on screen (observed
false positives: display 5985 / 6195 / 595 reported as success). This guard OCRs the
calculator result region at terminate time and checks it against the task's expected
value, so a run is only counted as success if the screen actually shows the answer.

OCR reuses score_batch.py's calibrated crop + threshold so runtime and post-hoc
scoring agree. Degrades gracefully: if tesseract isn't installed where the loop runs
(e.g. the Windows desktop), verify_terminate returns verified=None ("unverified") and
still saves the crop + end frame for later sandbox scoring.
"""
import re
import cv2

try:
    import pytesseract
except ImportError:
    pytesseract = None

# Result-number crop (x1, y1, x2, y2) in 1920x1080 capture pixels. Keep in sync with
# score_batch.RESULT_BBOX. Calculator is fixed when pre-opened, so one calibration holds.
RESULT_BBOX = (510, 560, 705, 608)


def read_display(frame_bgr, bbox=RESULT_BBOX, save=None):
    """OCR the result region of a BGR capture frame. Returns digit string or None."""
    if frame_bgr is None:
        return None
    x1, y1, x2, y2 = bbox
    crop = frame_bgr[y1:y2, x1:x2]
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if save:
        cv2.imwrite(save, th)
    if pytesseract is None:
        return None
    cfg = "--psm 7 -c tessedit_char_whitelist=0123456789.-"
    return re.sub(r"[^0-9.\-]", "", pytesseract.image_to_string(th, config=cfg).strip())


def verify_terminate(frame_bgr, expected=None, save=None):
    """Decide whether a terminate(success) is trustworthy.

    Returns (verified, read):
      verified=True  -> display matches expected (or is non-empty when expected is None)
      verified=False -> display present but wrong  => FALSE-POSITIVE terminate
      verified=None  -> couldn't OCR (tesseract missing) => unverified, don't trust blindly
    """
    read = read_display(frame_bgr, save=save)
    if read is None:
        return None, None
    if expected is None:
        return (read != ""), read          # generic guard: require a non-empty result
    return (read == str(expected)), read


def ocr_available():
    return pytesseract is not None
