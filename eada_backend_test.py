# =============================================================
# EADA - Edge AI Display Assistant (Final Version)
# Adaptive Brightness + Volume Control + VLC Auto Pause/Resume
# =============================================================

import cv2
import numpy as np
import sounddevice as sd
import mediapipe as mp
import time
import threading
import os
import vlc
import screen_brightness_control as sbc
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

# --- VLC Setup ---
os.add_dll_directory(r"C:\Program Files\VideoLAN\VLC")
video_path = r"C:\Users\Asus\Downloads\For A Reason (Official Video) Karan Aujla _ Tania  _ Ikky _ Latest Punjabi Songs 2025.mp4"   # 👈 change to your video file
player = vlc.MediaPlayer(video_path)
player.play()
print("▶ VLC started...")

# --- MediaPipe Face Detection ---
mp_face_detection = mp.solutions.face_detection
mp_drawing = mp.solutions.drawing_utils
face_detection = mp_face_detection.FaceDetection(model_selection=0, min_detection_confidence=0.55)

# --- Constants ---
KNOWN_FACE_WIDTH_CM = 16.0
DEFAULT_FOCAL_LENGTH_PX = 700.0
MIN_DISTANCE_M = 0.25
MAX_DISTANCE_M = 4.0
STABLE_RANGE = 0.15
UPDATE_THRESHOLD = 0.15
STABILITY_DURATION = 2.0
NOISE_THRESHOLD = 0.01
NO_FACE_PAUSE_DELAY = 3.0  # 3 seconds grace before pausing

# --- Audio Capture Setup ---
AUDIO_SAMPLERATE = 22050
NOISE_SAMPLE_SEC = 0.25
audio_lock = threading.Lock()
last_noise = 0.02
_audio_thread_running = True


def audio_worker():
    """Continuously capture short ambient sound samples."""
    global last_noise, _audio_thread_running
    while _audio_thread_running:
        try:
            audio = sd.rec(int(NOISE_SAMPLE_SEC * AUDIO_SAMPLERATE),
                           samplerate=AUDIO_SAMPLERATE, channels=1, dtype='float32')
            sd.wait()
            rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        except Exception:
            rms = 0.0
        with audio_lock:
            last_noise = 0.25 * rms + 0.75 * last_noise
        time.sleep(0.05)


audio_thread = threading.Thread(target=audio_worker, daemon=True)
audio_thread.start()

# --- System Volume Control (working pycaw version) ---
speakers = AudioUtilities.GetSpeakers()
endpoint = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
volume = cast(endpoint, POINTER(IAudioEndpointVolume))

# --- Variables ---
focal_length_px = DEFAULT_FOCAL_LENGTH_PX
last_distance = 0.8
prev_measured_distance = last_distance
prev_measured_noise = last_noise
last_update_distance = last_distance
display_brightness = 50.0
display_volume = 50.0
stability_status = 0
stable_start_time = None
no_face_last_seen = time.time()
paused = False


def estimate_distance(bbox_width_px):
    """Estimate real-world distance (in meters) based on face box width."""
    if bbox_width_px <= 0:
        return last_distance
    distance_cm = (KNOWN_FACE_WIDTH_CM * focal_length_px) / bbox_width_px
    return float(np.clip(distance_cm / 100.0, MIN_DISTANCE_M, MAX_DISTANCE_M))


def set_system_brightness(value):
    """Change laptop display brightness."""
    try:
        sbc.set_brightness(int(value))
    except Exception as e:
        print(f"⚠ Brightness control error: {e}")


def set_system_volume(value):
    """Change Windows system master volume (0–100)."""
    try:
        volume.SetMasterVolumeLevelScalar(value / 100.0, None)
    except Exception as e:
        print(f"⚠ Volume control error: {e}")


# --- Camera Setup ---
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("❌ Error: Cannot access webcam.")
    _audio_thread_running = False
    audio_thread.join(timeout=1.0)
    exit()

frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
print("\nEADA is running... Press 'q' to quit.\n")

try:
    while True:
        success, frame = cap.read()
        if not success:
            continue

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_detection.process(rgb)

        # --- Detect faces and calculate distance ---
        distances = []
        if results.detections:
            no_face_last_seen = time.time()
            for det in results.detections:
                bbox = det.location_data.relative_bounding_box
                bbox_w_px = bbox.width * frame_w
                d_m = estimate_distance(bbox_w_px)
                distances.append(d_m)
                mp_drawing.draw_detection(frame, det)

        face_detected = bool(distances)

        # --- VLC Auto Pause/Resume ---
        if not face_detected:
            if time.time() - no_face_last_seen > NO_FACE_PAUSE_DELAY:
                if player.is_playing():
                    player.pause()
                    paused = True
                    set_system_brightness(30)
                    set_system_volume(20)
        else:
            if paused:
                player.play()
                paused = False

        # --- Distance & Stability Calculation ---
        if distances:
            measured_distance = float(np.median(distances))
            last_distance = 0.2 * measured_distance + 0.8 * last_distance
        else:
            measured_distance = last_distance

        with audio_lock:
            measured_noise = float(last_noise)

        dist_diff = abs(measured_distance - prev_measured_distance)
        noise_diff = abs(measured_noise - prev_measured_noise)
        overall_change = abs(measured_distance - last_update_distance)

        if overall_change >= UPDATE_THRESHOLD:
            if dist_diff < STABLE_RANGE and noise_diff < NOISE_THRESHOLD:
                if stable_start_time is None:
                    stable_start_time = time.time()
                    stability_status = 1
                elif time.time() - stable_start_time >= STABILITY_DURATION:
                    # --- Compute and Apply System Brightness + Volume ---
                    display_brightness = float(np.interp(last_distance, [MIN_DISTANCE_M, 2.5], [30, 100]))
                    display_volume = float(np.interp(last_distance, [MIN_DISTANCE_M, MAX_DISTANCE_M], [20, 60])
                                           + measured_noise * 50.0)
                    display_volume = np.clip(display_volume, 0, 100)

                    set_system_brightness(display_brightness)
                    set_system_volume(display_volume)

                    last_update_distance = last_distance
                    stability_status = 2
                    stable_start_time = None
            else:
                stable_start_time = None
                stability_status = 0
        else:
            stability_status = 2

        prev_measured_distance = measured_distance
        prev_measured_noise = measured_noise

        # --- On-screen display ---
        cv2.putText(frame, f"Faces: {len(distances)}", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(frame, f"Distance: {last_distance:.2f} m", (20, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, f"Noise: {measured_noise:.3f}", (20, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, f"Brightness: {display_brightness:.1f}", (20, 130),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
        cv2.putText(frame, f"Volume: {display_volume:.1f}", (20, 160),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

        if paused:
            cv2.putText(frame, "⏸  Paused - No Viewer", (frame_w // 2 - 200, frame_h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
        else:
            cv2.putText(frame, "▶  Playing", (frame_w // 2 - 80, frame_h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 3)

        # --- Indicator Light ---
        if stability_status == 0:
            color, text = (0, 0, 255), "Unstable"
        elif stability_status == 1:
            color, text = (0, 255, 255), "Stable - Waiting"
        else:
            color, text = (0, 255, 0), "Stable - Updated"

        cv2.circle(frame, (frame_w - 40, 40), 18, color, -1)
        cv2.putText(frame, text, (frame_w - 240, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        cv2.imshow("EADA - Adaptive Display Assistant", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    _audio_thread_running = False
    audio_thread.join(timeout=1.0)
    cap.release()
    player.stop()
    cv2.destroyAllWindows()
    print("\n✅ EADA stopped cleanly.")
