import csv
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
from picamera2 import Picamera2

ROI_CFG = Path("config/roi.json")

# Display window size (resized)
DISP_W, DISP_H = 1280, 720

# ---- Replace these with your real sensor readers when ready ----
def read_mass_g() -> float:
    return float("nan")

def read_temp_c() -> float:
    return float("nan")
# ---------------------------------------------------------------


def load_cfg():
    if not ROI_CFG.exists():
        raise SystemExit("ROI config not found. Run scripts/roi_calibrate.py first.")
    return json.loads(ROI_CFG.read_text())


def main():
    cfg = load_cfg()

    FULL_W = int(cfg.get("full_w", 2304))
    FULL_H = int(cfg.get("full_h", 1296))
    x1, y1, x2, y2 = int(cfg["x1"]), int(cfg["y1"]), int(cfg["x2"]), int(cfg["y2"])

    # Basic sanity
    x1, x2 = sorted([max(0, x1), min(FULL_W - 1, x2)])
    y1, y2 = sorted([max(0, y1), min(FULL_H - 1, y2)])

    dataset_name = input("Dataset name (e.g. pan_binary): ").strip()
    label = input("Label/folder (e.g. pan_present, pan_absent): ").strip()

    out_dir = Path("data/raw") / dataset_name / label
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = Path("data/raw") / dataset_name / "labels.csv"
    new_file = not log_path.exists()

    picam2 = Picamera2()

    # Use same full-FOV size as ROI calibration.
    # On your system capture_array() returns BGR already, so do NOT convert channels.
    config = picam2.create_preview_configuration(
        main={"size": (FULL_W, FULL_H), "format": "RGB888"}
    )
    picam2.configure(config)
    picam2.start()

    # Let AE/AWB settle so colours are stable
    time.sleep(1.0)
    picam2.set_controls({"AeEnable": True, "AwbEnable": True})
    time.sleep(0.5)

    print("\nControls: [SPACE]=capture  [R]=toggle rapid capture  [Q]=quit")
    rapid = False
    last_rapid_t = 0.0
    rapid_hz = 3.0  # captures/sec in rapid mode

    with log_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp_iso", "filename", "dataset", "label", "mass_g", "temp_c"])

        while True:
            frame_bgr = picam2.capture_array()  # <-- treat as BGR

            # Crop ROI in FULL-RES coords
            roi = frame_bgr[y1:y2, x1:x2]

            # Build a resized display for comfort
            disp = cv2.resize(frame_bgr, (DISP_W, DISP_H), interpolation=cv2.INTER_AREA)

            # Draw ROI rectangle in DISPLAY coords
            sx = DISP_W / FULL_W
            sy = DISP_H / FULL_H
            dx1, dy1 = int(x1 * sx), int(y1 * sy)
            dx2, dy2 = int(x2 * sx), int(y2 * sy)
            cv2.rectangle(disp, (dx1, dy1), (dx2, dy2), (0, 255, 0), 2)

            cv2.putText(disp, f"{dataset_name} | {label}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(disp, f"Rapid={rapid} (R)  SPACE=capture  Q=quit", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            cv2.imshow("capture_dataset", disp)
            cv2.imshow("roi", roi)

            k = cv2.waitKey(1) & 0xFF
            now = time.time()

            do_capture = False
            if k in (ord('q'), 27):
                break
            if k == ord('r'):
                rapid = not rapid
                print(f"Rapid capture: {rapid}")
            if k == ord(' '):
                do_capture = True
            if rapid and (now - last_rapid_t) >= (1.0 / rapid_hz):
                do_capture = True
                last_rapid_t = now

            if do_capture:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                fname = f"{ts}.jpg"
                fpath = out_dir / fname

                mass = read_mass_g()
                temp = read_temp_c()

                # Save ROI only (keeps dataset small + consistent)
                cv2.imwrite(str(fpath), roi)

                w.writerow([datetime.now().isoformat(), str(fpath), dataset_name, label, mass, temp])
                f.flush()
                print(f"Saved {fpath}")

    cv2.destroyAllWindows()
    picam2.stop()


if __name__ == "__main__":
    main()
