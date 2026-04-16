import time

try:
    from libcamera import controls
except Exception:
    controls = None

def apply_good_colour(picam2, settle_s=2.0, vivid=True, awb_mode="daylight"):
    """
    Make colour look normal & consistent:
      - enable AE/AWB, set AWB mode (daylight/indoor/auto)
      - wait for settle
      - lock ColourGains and disable AWB
      - optionally bump saturation/contrast a bit
    """

    ctrl = {
        "AeEnable": True,
        "AwbEnable": True,
    }

    if vivid:
        ctrl.update({"Saturation": 1.15, "Contrast": 1.05, "Sharpness": 1.0})
    else:
        ctrl.update({"Saturation": 1.0, "Contrast": 1.0, "Sharpness": 1.0})

    if controls is not None:
        mode_map = {
            "auto": controls.AwbModeEnum.Auto,
            "daylight": controls.AwbModeEnum.Daylight,
            "cloudy": controls.AwbModeEnum.Cloudy,
            "indoor": controls.AwbModeEnum.Indoor,
            "fluorescent": controls.AwbModeEnum.Fluorescent,
            "incandescent": controls.AwbModeEnum.Incandescent,
            "tungsten": controls.AwbModeEnum.Tungsten,
        }
        ctrl["AwbMode"] = mode_map.get(awb_mode.lower(), controls.AwbModeEnum.Daylight)

    picam2.set_controls(ctrl)
    time.sleep(settle_s)

    md = picam2.capture_metadata()
    gains = md.get("ColourGains", None)
    exp = md.get("ExposureTime", None)
    ag = md.get("AnalogueGain", None)

    # Lock colour for repeatability
    lock = {"AwbEnable": False}
    if gains is not None:
        lock["ColourGains"] = gains

    picam2.set_controls(lock)
    return {"ColourGains": gains, "ExposureTime": exp, "AnalogueGain": ag}
