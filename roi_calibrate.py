import json
import time
from pathlib import Path

import cv2
from picamera2 import Picamera2

CFG_PATH = Path("config/roi.json")

# We will use sensor mode index 1: 2304x1296 (full FOV) on IMX708
SENSOR_MODE_INDEX = 1
FULL_W, FULL_H = 2304, 1296

# Display window size (resized)
DISP_W, DISP_H = 1280, 720

pts_disp = []  # clicks in DISPLAY coords

def on_mouse(event, x, y, flags, param):
    global pts_disp
    if event == cv2.EVENT_LBUTTONDOWN:
        pts_disp.append((x, y))
        print(f"Clicked (display coords): {(x, y)}")
        if len(pts_disp) > 2:
            pts_disp = pts_disp[-2:]

def disp_to_full(x, y):
    """Convert display coords -> full sensor coords."""
    sx = FULL_W / DISP_W
    sy = FULL_H / DISP_H
    return int(round(x * sx)), int(round(y * sy))

def main():
    picam2 = Picamera2()

    mode = picam2.sensor_modes[SENSOR_MODE_INDEX]
    print("Using sensor mode:", mode)

    # Force full-FOV mode via main size matching mode size
    config = picam2.create_preview_configuration(
        main={"size": (FULL_W, FULL_H), "format": "RGB888"}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(0.3)

    cv2.namedWindow("roi_calibrate")
    cv2.setMouseCallback("roi_calibrate", on_mouse)

    while True:
        frame = picam2.capture_array()  # RGB
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # Resize for display
        disp = cv2.resize(frame_bgr, (DISP_W, DISP_H), interpolation=cv2.INTER_AREA)

        # Draw ROI rectangle if we have 2 points
        if len(pts_disp) == 2:
            (dx1, dy1), (dx2, dy2) = pts_disp
            dx1, dx2 = sorted([dx1, dx2])
            dy1, dy2 = sorted([dy1, dy2])

            cv2.rectangle(disp, (dx1, dy1), (dx2, dy2), (0, 255, 0), 2)
            cv2.putText(disp, "Press S to save ROI", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        cv2.putText(disp, "Click TL then BR of plate ROI. Q=quit", (10, DISP_H - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imshow("roi_calibrate", disp)
        k = cv2.waitKey(1) & 0xFF

        if k in (ord('q'), 27):
            break

        if k == ord('s') and len(pts_disp) == 2:
            (dx1, dy1), (dx2, dy2) = pts_disp
            dx1, dx2 = sorted([dx1, dx2])
            dy1, dy2 = sorted([dy1, dy2])

            # Convert display coords -> full coords
            (x1, y1) = disp_to_full(dx1, dy1)
            (x2, y2) = disp_to_full(dx2, dy2)

            x1, x2 = sorted([x1, x2])
            y1, y2 = sorted([y1, y2])

            CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CFG_PATH.write_text(json.dumps({
                "sensor_mode_index": SENSOR_MODE_INDEX,
                "full_w": FULL_W, "full_h": FULL_H,
                "disp_w": DISP_W, "disp_h": DISP_H,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2
            }, indent=2))
            print(f"Saved ROI (full coords) -> {(x1, y1, x2, y2)} into {CFG_PATH}")

    cv2.destroyAllWindows()
    picam2.stop()

if __name__ == "__main__":
    main()
