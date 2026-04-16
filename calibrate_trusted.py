import time, json
import RPi.GPIO as GPIO

CAL_PATH = "/home/leomcgriskin/hx711_cal.json"

DOUT = 5
SCK  = 6

GPIO.setmode(GPIO.BCM)
GPIO.setup(SCK, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(DOUT, GPIO.IN)

def wait_ready(timeout=2.0):
    t0 = time.time()
    while GPIO.input(DOUT) == 1:
        if time.time() - t0 > timeout:
            return False
    return True

def read_raw_once():
    if not wait_ready(2.0):
        raise TimeoutError("HX711 not ready (DT stayed high). Check power/wiring.")
    data = 0
    for _ in range(24):
        GPIO.output(SCK, 1)
        data = (data << 1) | GPIO.input(DOUT)
        GPIO.output(SCK, 0)
    GPIO.output(SCK, 1)  # 25th pulse: A/128
    GPIO.output(SCK, 0)
    if data & 0x800000:
        data -= 1 << 24
    return data

def read_avg(n=60, settle_s=1.5):
    # Let things settle, then average a bunch of samples
    time.sleep(settle_s)
    s = 0
    for _ in range(n):
        s += read_raw_once()
    return s / n

def prompt(msg):
    input(f"\n{msg}\nPress Enter when ready...")

def fit_slope_through_origin(counts_list, grams_list):
    # grams ≈ a * net_counts
    # a = Σ(g*c) / Σ(c^2)
    num = sum(g*c for g, c in zip(grams_list, counts_list))
    den = sum(c*c for c in counts_list)
    if den == 0:
        raise ValueError("Denominator is zero; check readings.")
    return num / den

try:
    print("Trusted-weight calibration (Pi5 + HX711)")
    print("We will use: phone=217.9g, weight=500g, both=717.9g")
    print("Place items in the CENTRE each time.")

    # 1) Tare (plate-only)
    prompt("REMOVE all loads. Leave ONLY the plate (your normal zero state).")
    offset = read_avg(n=80, settle_s=2.0)
    print(f"Offset (counts): {offset:.2f}")

    # 2) Measure net counts for trusted masses
    points = [
        ("PHONE only", 217.9),
        ("500g only", 500.0),
        ("PHONE + 500g together", 717.9),
    ]

    net_counts = []
    grams = []

    for label, g in points:
        prompt(f"PLACE: {label} ({g} g)")
        raw = read_avg(n=80, settle_s=2.0)
        net = raw - offset
        net_counts.append(net)
        grams.append(g)
        print(f"{label}: raw={raw:.2f}, net={net:.2f}")

    # 3) Fit slope through origin
    a = fit_slope_through_origin(net_counts, grams)
    b = 0.0

    print("\nNew calibration:")
    print(f"a (grams/count) = {a:.12e}")
    print(f"b (grams)       = {b:.2f}")

    cal = {
        "gpio": {"dout": DOUT, "sck": SCK},
        "offset_counts": float(offset),
        "fit": {"a_grams_per_count": float(a), "b_grams": float(b)},
        "points": [{"label": lbl, "grams": g, "net_counts": float(c)} for (lbl, g), c in zip(points, net_counts)],
        "note": "Calibrated using trusted masses: phone=217.9g (external scale), weight=500.0g."
    }

    with open(CAL_PATH, "w") as f:
        json.dump(cal, f, indent=2)

    print(f"\nSaved: {CAL_PATH}")
    print("Now run mass_runtime.py and test 500g again.")

finally:
    GPIO.cleanup()