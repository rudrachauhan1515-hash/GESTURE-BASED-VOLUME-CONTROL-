"""
finger_volume_full_control_with_mute.py

- Auto-calibrates thumb-index min/max distances (press 'r' to recalibrate).
- Maps distance -> system volume 0..100 (close -> 0%, far -> 100%).
- Press 'm' to toggle MUTE/unmute.
- Press 'q' to quit.
"""

import cv2
import mediapipe as mp
import numpy as np
import time
import platform
import subprocess
import pyautogui  # fallback if direct methods fail

# ---------- Config (tweak if needed) ----------
CALIBRATE_SECONDS = 2.5      # seconds to auto-calibrate on start / when pressing 'r'
SMOOTHING_ALPHA = 0.70       # 0..1 : higher = smoother (less jumpy)
MIN_DISTANCE_CLAMP = 6       # avoid divide-by-zero
SET_INTERVAL = 0.06          # seconds between actual system volume sets
# ------------------------------------------------

OS = platform.system()

# ---------------- Platform-specific volume setters ----------------
def set_volume_windows(percent: int):
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume.iid, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        volume.SetMasterVolumeLevelScalar(max(0.0, min(1.0, percent / 100.0)), None)
        return True
    except Exception as e:
        # print("pycaw error:", e)
        return False

def set_volume_macos(percent: int):
    try:
        subprocess.run(["osascript", "-e", f"set volume output volume {int(percent)}"], check=True)
        return True
    except Exception as e:
        # print("osascript error:", e)
        return False

def set_volume_linux(percent: int):
    try:
        # Try Master first; change 'Master' if needed on your distro
        subprocess.run(["amixer", "sset", "Master", f"{int(percent)}%"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

def set_system_volume(percent: int):
    percent = int(round(max(0, min(100, percent))))
    ok = False
    if OS == "Windows":
        ok = set_volume_windows(percent)
    elif OS == "Darwin":
        ok = set_volume_macos(percent)
    elif OS == "Linux":
        ok = set_volume_linux(percent)

    if not ok:
        # fallback coarse approach using keyboard keys
        try:
            # press volume up/down roughly to approach target (coarse) — limited fallback
            # This tries to move volume toward target from center (50) by presses
            # Not perfect but better than nothing
            base = 50
            presses = max(1, int(abs(percent - base) / 5))
            key = "volumeup" if percent > base else "volumedown"
            for _ in range(presses):
                pyautogui.press(key)
            return False
        except Exception:
            return False
    return True
# ------------------------------------------------------------------

# MediaPipe hands init
mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils
hands = mp_hands.Hands(static_image_mode=False,
                       max_num_hands=1,
                       min_detection_confidence=0.55,
                       min_tracking_confidence=0.55)

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    raise Exception("Camera not detected. Connect a camera and try again.")

print("Start: Show thumb + index clearly to calibrate.")
print("Press 'r' to recalibrate anytime, 'm' to mute/unmute, 'q' to quit.")

# calibration vars
collecting = True
calib_start = time.time()
observed_min = 9999
observed_max = 0

mapped_volume = 0
smoothed_volume = None
last_set_time = 0.0

muted = False
saved_volume_before_mute = None

def map_distance_to_percent(dist, dmin, dmax):
    # ensure sensible ranges
    dmin = max(dmin, MIN_DISTANCE_CLAMP)
    dmax = max(dmax, dmin + 1)
    # normalized 0..1 (close = dmin -> 0 ; far = dmax -> 1)
    norm = (dist - dmin) / (dmax - dmin)
    percent = norm * 100.0
    return int(round(max(0, min(100, percent))))

while True:
    success, frame = cap.read()
    if not success:
        break

    frame = cv2.flip(frame, 1)
    h, w, _ = frame.shape
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = hands.process(rgb)

    key = cv2.waitKey(1) & 0xFF

    # re-calibrate on 'r'
    if key == ord('r'):
        collecting = True
        calib_start = time.time()
        observed_min = 9999
        observed_max = 0
        print("Re-calibration started... show pinch (min) and open (max) positions.")

    # toggle mute on 'm'
    if key == ord('m'):
        muted = not muted
        if muted:
            # store current smoothed_volume and set system vol to 0
            try:
                saved_volume_before_mute = int(round(smoothed_volume)) if smoothed_volume is not None else 0
            except Exception:
                saved_volume_before_mute = 0
            set_system_volume(0)
        else:
            # unmuting: restore to current mapped/smoothed value
            # if smoothed_volume exists use it, else restore saved
            to_set = int(round(smoothed_volume)) if smoothed_volume is not None else (saved_volume_before_mute or 30)
            set_system_volume(to_set)

    if results.multi_hand_landmarks:
        hand = results.multi_hand_landmarks[0]
        lm_thumb = hand.landmark[4]
        lm_index = hand.landmark[8]
        tx, ty = int(lm_thumb.x * w), int(lm_thumb.y * h)
        ix, iy = int(lm_index.x * w), int(lm_index.y * h)
        dist = int(np.hypot(tx - ix, ty - iy))

        # draw indicators
        cv2.circle(frame, (tx, ty), 8, (0, 255, 0), -1)
        cv2.circle(frame, (ix, iy), 8, (0, 255, 0), -1)
        cv2.line(frame, (tx, ty), (ix, iy), (0, 255, 255), 2)
        cv2.putText(frame, f"RawDist: {dist}px", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220,220,220), 2)

        # calibration collecting
        if collecting:
            elapsed = time.time() - calib_start
            observed_min = min(observed_min, dist)
            observed_max = max(observed_max, dist)
            cv2.putText(frame, f"Calibrating... {elapsed:.1f}/{CALIBRATE_SECONDS}s", (10, 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (10,200,200), 2)
            cv2.putText(frame, f"Observed min/max: {observed_min}/{observed_max}", (10, 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200,200,100), 2)
            if elapsed >= CALIBRATE_SECONDS:
                collecting = False
                if observed_min >= observed_max:
                    observed_min = max(5, observed_max - 20)
                    observed_max = observed_min + 50
                print(f"Calibration done. Range: {observed_min}px .. {observed_max}px")
        else:
            # map current distance to 0..100%
            mapped = map_distance_to_percent(dist, observed_min, observed_max)
            mapped_volume = max(0, min(100, mapped))

            # smoothing
            if smoothed_volume is None:
                smoothed_volume = mapped_volume
            else:
                # SMOOTHING_ALPHA nearer to 1 -> smoother/slower changes
                smoothed_volume = SMOOTHING_ALPHA * smoothed_volume + (1 - SMOOTHING_ALPHA) * mapped_volume

            now = time.time()
            # only set system volume if not muted and rate limit not exceeded
            if not muted and (now - last_set_time) >= SET_INTERVAL:
                to_set = int(round(smoothed_volume))
                set_system_volume(to_set)
                last_set_time = now

            # HUD: show mapped & smoothed
            cv2.putText(frame, f"Mapped: {mapped_volume}%  Smoothed: {int(round(smoothed_volume))}%",
                        (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,150), 2)

    else:
        cv2.putText(frame, "No hand detected", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,100,255), 2)

    # MUTE / instructions HUD
    mute_text = "MUTED" if muted else "UNMUTED"
    cv2.putText(frame, f"[m] Mute Toggle: {mute_text}", (10, h - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 200, 255) if not muted else (0, 80, 255), 2)
    cv2.putText(frame, "[r] Recalibrate  |  [q] Quit", (10, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220,220,220), 1)

    # Draw volume progress bar (left)
    bar_x = w - 70
    bar_y = 30
    bar_h = 200
    cv2.rectangle(frame, (bar_x - 2, bar_y - 2), (bar_x + 52, bar_y + bar_h + 2), (50,50,50), -1)
    # fill based on smoothed_volume (if available) else 0
    fill = int((smoothed_volume if smoothed_volume is not None else 0) / 100.0 * bar_h)
    cv2.rectangle(frame, (bar_x, bar_y + bar_h - fill), (bar_x + 50, bar_y + bar_h), (0,200,0), -1)
    cv2.putText(frame, f"{int(round(smoothed_volume)) if smoothed_volume is not None else 0}%",
                (bar_x - 10, bar_y + bar_h + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220,220,220), 2)

    cv2.imshow("Full-range Finger Distance Volume Control + Mute", frame)

    if key == ord('q'):
        break

# cleanup
cap.release()
cv2.destroyAllWindows()
hands.close()