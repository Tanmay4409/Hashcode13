# =============================================================
# EADA Pro - Smart Workspace Optimizer (Hackathon Edition)
# Advanced Ergonomics + Accessibility + Performance Optimization
# =============================================================

import cv2
import numpy as np
import sounddevice as sd
import mediapipe as mp
import time
import threading
import os
import json
import vlc
import screen_brightness_control as sbc
from datetime import datetime
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

# --- MediaPipe Solutions ---
mp_face_mesh = mp.solutions.face_mesh
mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles

# Initialize models
face_mesh = mp_face_mesh.FaceMesh(
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
pose = mp_pose.Pose(
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# --- Constants ---
KNOWN_FACE_WIDTH_CM = 16.0
DEFAULT_FOCAL_LENGTH_PX = 700.0
MIN_DISTANCE_M = 0.25
MAX_DISTANCE_M = 4.0
STABLE_RANGE = 0.15
UPDATE_THRESHOLD = 0.15
STABILITY_DURATION = 2.0
NOISE_THRESHOLD = 0.01
NO_FACE_PAUSE_DELAY = 3.0

# --- Audio Capture Setup ---
AUDIO_SAMPLERATE = 22050
NOISE_SAMPLE_SEC = 0.25

class AudioMonitor:
    def __init__(self):
        self.last_noise = 0.02
        self.audio_lock = threading.Lock()
        self._running = True
        self.thread = threading.Thread(target=self._audio_worker, daemon=True)
        self.thread.start()
    
    def _audio_worker(self):
        """Continuously capture short ambient sound samples."""
        while self._running:
            try:
                audio = sd.rec(int(NOISE_SAMPLE_SEC * AUDIO_SAMPLERATE),
                               samplerate=AUDIO_SAMPLERATE, channels=1, dtype='float32')
                sd.wait()
                rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
            except Exception:
                rms = 0.0
            with self.audio_lock:
                self.last_noise = 0.25 * rms + 0.75 * self.last_noise
            time.sleep(0.05)
    
    def get_noise_level(self):
        """Get current smoothed noise level"""
        with self.audio_lock:
            return self.last_noise
    
    def stop(self):
        """Stop audio monitoring thread"""
        self._running = False
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)

# --- User Profiles ---
DEFAULT_PROFILE = {
    "name": "Default",
    "brightness_preference": 1.0,
    "volume_preference": 1.0,
    "color_temp_preference": 6500,
    "accessibility": {
        "high_contrast": False,
        "larger_text": False,
        "audio_alerts": True
    }
}

class UserProfile:
    def __init__(self, profile_path="user_profiles.json"):
        self.profile_path = profile_path
        self.current_profile = DEFAULT_PROFILE.copy()
        self.profiles = self._load_profiles()
        
    def _load_profiles(self):
        try:
            with open(self.profile_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {"default": DEFAULT_PROFILE}
    
    def save_profiles(self):
        with open(self.profile_path, 'w') as f:
            json.dump(self.profiles, f, indent=4)

class ErgonomicsMonitor:
    def __init__(self):
        # Enhanced blink detection with proper calibration
        self.EAR_THRESHOLD = 0.25  # Conservative threshold
        self.ear_history = []  # Rolling window for smoothing
        self.ear_window_size = 10  # Larger window for better stability
        self.blink_count = 0
        self.last_blink_time = time.time()
        self.blink_rate = 0
        self.consecutive_closed_frames = 0
        self.consecutive_open_frames = 0
        self.was_blinking = False
        self.blink_in_progress = False
        
        # Adaptive baseline calibration
        self.baseline_ear = None
        self.ear_samples_for_baseline = []
        self.calibration_complete = False
        self.calibration_frames_needed = 90  # 3 seconds at 30fps
        
        # Eye strain tracking with time-based accuracy
        self.eye_strain_level = 0
        self.break_reminder = False
        self.session_start_time = time.time()
        self.total_blinks = 0
        self.strain_check_interval = 10.0  # Check every 10 seconds
        self.last_strain_check = time.time()
        
        # Enhanced posture tracking
        self.posture_status = "unknown"
        self.posture_score = 0  # 0-100, higher is better
        self.forward_head_distance = 0
        self.shoulder_alignment = 0
        self.spine_angle = 0
        self.head_tilt_angle = 0
    
    def _calculate_ear(self, eye_landmarks):
        """Calculate Eye Aspect Ratio with precise calibration"""
        try:
            # Vertical eye landmarks (2 measurements for accuracy)
            v1 = np.linalg.norm(np.array([eye_landmarks[1].x, eye_landmarks[1].y]) - 
                                np.array([eye_landmarks[5].x, eye_landmarks[5].y]))
            v2 = np.linalg.norm(np.array([eye_landmarks[2].x, eye_landmarks[2].y]) - 
                                np.array([eye_landmarks[4].x, eye_landmarks[4].y]))
            
            # Horizontal eye landmark
            h = np.linalg.norm(np.array([eye_landmarks[0].x, eye_landmarks[0].y]) - 
                              np.array([eye_landmarks[3].x, eye_landmarks[3].y]))
            
            if h == 0:
                return 0.0
            
            # Calculate EAR with stability check
            ear = (v1 + v2) / (2.0 * h)
            return float(ear)
        except Exception as e:
            return 0.0
    
    def analyze_face_landmarks(self, landmarks):
        """Analyze blinks and eye strain with calibrated accuracy"""
        if not landmarks:
            return
        
        current_time = time.time()
        
        try:
            # Get eye landmarks from MediaPipe Face Mesh
            left_eye = [landmarks.landmark[i] for i in [33, 160, 158, 133, 153, 144]]
            right_eye = [landmarks.landmark[i] for i in [362, 385, 387, 263, 373, 380]]
            
            # Calculate EAR for both eyes
            left_ear = self._calculate_ear(left_eye)
            right_ear = self._calculate_ear(right_eye)
            
            if left_ear == 0.0 or right_ear == 0.0:
                return
            
            avg_ear = (left_ear + right_ear) / 2.0
            
            # Add to history for temporal smoothing
            self.ear_history.append(avg_ear)
            if len(self.ear_history) > self.ear_window_size:
                self.ear_history.pop(0)
            
            # Calculate smoothed EAR
            if len(self.ear_history) < 3:
                return  # Need minimum samples
            
            smoothed_ear = np.median(self.ear_history)  # Use median for robustness
            
            # === CALIBRATION PHASE ===
            if not self.calibration_complete:
                # Collect baseline samples (eyes should be open)
                if smoothed_ear > 0.20:  # Reasonable open eye threshold
                    self.ear_samples_for_baseline.append(smoothed_ear)
                
                if len(self.ear_samples_for_baseline) >= self.calibration_frames_needed:
                    # Calculate adaptive threshold
                    self.baseline_ear = np.mean(self.ear_samples_for_baseline)
                    std_dev = np.std(self.ear_samples_for_baseline)
                    # Set threshold at 75% of baseline for accuracy
                    self.EAR_THRESHOLD = self.baseline_ear * 0.75
                    self.calibration_complete = True
                    print(f"✅ Blink detection calibrated: baseline={self.baseline_ear:.3f}, threshold={self.EAR_THRESHOLD:.3f}")
                return  # Skip detection during calibration
            
            # === BLINK DETECTION with hysteresis ===
            if smoothed_ear < self.EAR_THRESHOLD:
                self.consecutive_closed_frames += 1
                self.consecutive_open_frames = 0
                
                # Confirm blink start (require 2 consecutive frames)
                if self.consecutive_closed_frames >= 2 and not self.blink_in_progress:
                    self.blink_in_progress = True
                    
            else:
                self.consecutive_open_frames += 1
                self.consecutive_closed_frames = 0
                
                # Confirm blink end (require 2 consecutive open frames)
                if self.consecutive_open_frames >= 2 and self.blink_in_progress:
                    # Valid blink detected
                    self.blink_count += 1
                    self.total_blinks += 1
                    self.last_blink_time = current_time
                    self.blink_in_progress = False
            
            # === BLINK RATE CALCULATION (per minute) ===
            session_duration = current_time - self.session_start_time
            if session_duration >= 60:
                # Calculate rate based on total blinks over time
                self.blink_rate = int((self.total_blinks / session_duration) * 60)
            elif session_duration >= 20:
                # Estimate after 20 seconds
                self.blink_rate = int((self.total_blinks / session_duration) * 60)
            
            # === EYE STRAIN DETECTION (every 10 seconds) ===
            if current_time - self.last_strain_check >= self.strain_check_interval:
                self.last_strain_check = current_time
                
                time_since_blink = current_time - self.last_blink_time
                strain_score = 0
                
                # Factor 1: Time since last blink
                if time_since_blink > 15:
                    strain_score += 3
                elif time_since_blink > 10:
                    strain_score += 2
                elif time_since_blink > 7:
                    strain_score += 1
                
                # Factor 2: Blink rate (healthy: 15-20/min)
                if self.blink_rate > 0:
                    if self.blink_rate < 8:
                        strain_score += 3
                    elif self.blink_rate < 12:
                        strain_score += 2
                    elif self.blink_rate < 15:
                        strain_score += 1
                
                # Factor 3: Sustained low EAR (squinting)
                if smoothed_ear < self.EAR_THRESHOLD * 1.15:
                    strain_score += 1
                
                # Update strain level (0-5)
                self.eye_strain_level = min(strain_score, 5)
                
                # Set break reminder
                if self.eye_strain_level >= 4:
                    self.break_reminder = True
                elif self.eye_strain_level <= 1:
                    self.break_reminder = False
                    
        except Exception as e:
            pass
    
    def analyze_posture(self, pose_landmarks):
        """Analyze posture using enhanced multi-metric evaluation"""
        if not pose_landmarks:
            return
        
        # Extract key landmarks for posture analysis
        landmarks = pose_landmarks.landmark
        
        # Calculate comprehensive posture metrics
        try:
            # Get key body landmarks (MediaPipe Pose indices)
            nose = landmarks[0]              # Head position
            left_shoulder = landmarks[11]    # Left shoulder
            right_shoulder = landmarks[12]   # Right shoulder
            left_hip = landmarks[23]         # Left hip
            right_hip = landmarks[24]        # Right hip
            left_ear = landmarks[7]          # Left ear
            right_ear = landmarks[8]         # Right ear
            
            # === Metric 1: Shoulder Alignment (should be level) ===
            shoulder_diff = abs(left_shoulder.y - right_shoulder.y)
            shoulder_score = max(0, 100 - shoulder_diff * 500)  # Penalize unevenness
            self.shoulder_alignment = shoulder_score
            
            # === Metric 2: Forward Head Posture ===
            # Distance between nose and vertical line through shoulders
            shoulder_mid_x = (left_shoulder.x + right_shoulder.x) / 2
            shoulder_mid_y = (left_shoulder.y + right_shoulder.y) / 2
            
            # Horizontal distance (forward lean)
            head_forward = abs(nose.x - shoulder_mid_x)
            forward_head_score = max(0, 100 - head_forward * 300)
            self.forward_head_distance = head_forward
            
            # === Metric 3: Neck Angle (vertical alignment) ===
            neck_vertical = abs(nose.y - shoulder_mid_y)
            neck_score = max(0, 100 - neck_vertical * 200)
            
            # === Metric 4: Spine Angle (hip to shoulder alignment) ===
            hip_mid_x = (left_hip.x + right_hip.x) / 2
            hip_mid_y = (left_hip.y + right_hip.y) / 2
            
            # Calculate spine angle from vertical
            spine_dx = shoulder_mid_x - hip_mid_x
            spine_dy = shoulder_mid_y - hip_mid_y
            
            if spine_dy != 0:
                spine_angle_rad = np.arctan(abs(spine_dx / spine_dy))
                self.spine_angle = np.degrees(spine_angle_rad)
                # Ideal spine angle: 0-10 degrees from vertical
                spine_score = max(0, 100 - self.spine_angle * 5)
            else:
                spine_score = 50  # Neutral if can't calculate
            
            # === Metric 5: Head Tilt (ear alignment) ===
            ear_diff = abs(left_ear.y - right_ear.y)
            head_tilt_score = max(0, 100 - ear_diff * 400)
            self.head_tilt_angle = ear_diff * 100  # Approximate angle
            
            # === Composite Posture Score (weighted average) ===
            # Weights based on ergonomic importance
            weights = {
                'shoulder': 0.20,
                'forward_head': 0.30,  # Most critical
                'neck': 0.20,
                'spine': 0.20,
                'head_tilt': 0.10
            }
            
            self.posture_score = (
                weights['shoulder'] * shoulder_score +
                weights['forward_head'] * forward_head_score +
                weights['neck'] * neck_score +
                weights['spine'] * spine_score +
                weights['head_tilt'] * head_tilt_score
            )
            
            # Update status based on composite score (0-100)
            if self.posture_score >= 85:
                self.posture_status = "excellent"
            elif self.posture_score >= 70:
                self.posture_status = "good"
            elif self.posture_score >= 50:
                self.posture_status = "fair"
            else:
                self.posture_status = "poor"
                
        except Exception as e:
            # Fallback if landmarks unavailable
            self.posture_status = "unknown"
            self.posture_score = 0

class WorkspaceOptimizer:
    def __init__(self):
        self.user_profile = UserProfile()
        self.ergonomics = ErgonomicsMonitor()
        self.audio_monitor = AudioMonitor()
        self.start_time = time.time()
        self.session_duration = 0
        self.alerts = []
        
        # Audio setup
        self._setup_audio()
        
        # Initialize workspace state with stability tracking
        self.brightness = 50
        self.volume = 50
        self.color_temp = 6500
        
        # Distance stability tracking
        self.current_distance = 1.0
        self.stable_distance = 1.0
        self.distance_history = []
        self.distance_window = 15  # ~0.5 seconds at 30fps
        self.stability_threshold = 0.15  # ±0.15m
        
        # Presence detection for media control
        self.face_detected = True
        self.last_detection_time = time.time()
        self.absence_duration = 0
        self.media_paused = False
        self.presence_resume_time = 0
        
        # Volume change dampening with more responsive settings
        self.last_volume = 50
        self.last_distance_for_volume = 1.0  # Track distance for volume calculations
        self.volume_change_threshold = 2  # Minimum 2% change to apply (more responsive)
        
    def _setup_audio(self):
        """Setup audio monitoring and control"""
        self.speakers = AudioUtilities.GetSpeakers()
        self.endpoint = self.speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        self.volume_controller = cast(self.endpoint, POINTER(IAudioEndpointVolume))
        
    def optimize_workspace(self, distance, noise_level, face_landmarks, pose_landmarks):
        """Main optimization function with stability checks"""
        # Update session metrics
        self.session_duration = time.time() - self.start_time
        current_time = time.time()
        
        # === PRESENCE DETECTION ===
        if face_landmarks is not None:
            self.face_detected = True
            self.last_detection_time = current_time
            self.absence_duration = 0
            
            # Resume media if it was paused and 2 seconds have passed
            if self.media_paused and (current_time - self.presence_resume_time) >= 2.0:
                self._resume_media()
                self.media_paused = False
                self.alerts.append("▶️ Media resumed - User detected")
        else:
            self.face_detected = False
            self.absence_duration = current_time - self.last_detection_time
            
            # Pause media after 3 seconds of absence
            if self.absence_duration >= 3.0 and not self.media_paused:
                self._pause_media()
                self.media_paused = True
                self.presence_resume_time = 0
                self.alerts.append("⏸️ Media paused - No user detected")
            
            # Set resume timer when user returns
            if self.face_detected and self.media_paused and self.presence_resume_time == 0:
                self.presence_resume_time = current_time
        
        # === DISTANCE STABILITY TRACKING ===
        self.current_distance = distance
        self.distance_history.append(distance)
        if len(self.distance_history) > self.distance_window:
            self.distance_history.pop(0)
        
        # Check if distance is stable
        if len(self.distance_history) >= self.distance_window:
            mean_distance = np.mean(self.distance_history)
            std_distance = np.std(self.distance_history)
            
            # Distance is stable if std dev is low and change from stable point is small
            distance_change = abs(mean_distance - self.stable_distance)
            
            if std_distance < 0.05 and distance_change >= self.stability_threshold:
                # Update stable distance - user has moved to new position
                self.stable_distance = mean_distance
                print(f"📍 Stable distance updated: {self.stable_distance:.2f}m")
        
        # Analyze ergonomics
        if face_landmarks:
            self.ergonomics.analyze_face_landmarks(face_landmarks)
        self.ergonomics.analyze_posture(pose_landmarks)
        
        # Adjust settings based on STABLE distance (not current jitter)
        self._adjust_display_settings(self.stable_distance)
        self._adjust_audio_settings(noise_level, self.stable_distance)
        
        # Generate alerts and recommendations
        self._check_health_alerts()
        
        return self._get_current_state()
    
    def _adjust_display_settings(self, distance):
        """Adjust display settings based on distance and user preferences"""
        base_brightness = np.interp(distance, [MIN_DISTANCE_M, 2.5], [30, 100])
        self.brightness = base_brightness * self.user_profile.current_profile["brightness_preference"]
        
        # Adjust color temperature based on time of day
        hour = datetime.now().hour
        if 20 <= hour or hour <= 6:
            self.color_temp = min(self.color_temp, 4500)  # Warmer at night
        
        # Try to set brightness with graceful fallback
        try:
            sbc.set_brightness(int(self.brightness))
        except KeyboardInterrupt:
            raise  # Allow user to quit
        except Exception as e:
            # Brightness control may not be available on all systems
            pass
    
    def _adjust_audio_settings(self, noise_level, distance):
        """Adjust audio with highly aggressive distance-based changes"""
        # Base volume from noise compensation
        noise_volume = np.interp(noise_level, [0, 0.1], [40, 80])
        
        # Calculate distance change from last adjustment
        distance_delta = distance - self.last_distance_for_volume
        
        # Distance-based volume with VERY STEEP curve
        # For every 0.05m change in distance, apply ~10% volume change (doubled from 5%)
        # Closer = lower volume, Farther = higher volume
        
        # Use square root for extremely pronounced changes
        # Map distance to volume: 0.25m → 20%, 4.0m → 100%
        distance_normalized = (distance - MIN_DISTANCE_M) / (MAX_DISTANCE_M - MIN_DISTANCE_M)
        distance_normalized = np.clip(distance_normalized, 0, 1)
        
        # Extremely steep curve: volume = 20 + 80 * (normalized^0.4)
        # This creates massive changes with small movements
        distance_volume = 20 + 80 * (distance_normalized ** 0.4)
        
        # For 0.05m change, we want ~10% volume change (doubled)
        # Amplify based on distance delta
        if abs(distance_delta) >= 0.05:
            # Calculate expected volume change: ~10% per 0.05m
            expected_change = (distance_delta / 0.05) * 10.0
            distance_volume += expected_change
            distance_volume = np.clip(distance_volume, 15, 100)
        
        # Weighted combination: 90% distance, 10% noise (distance extremely dominant)
        base_volume = 0.9 * distance_volume + 0.1 * noise_volume
        target_volume = base_volume * self.user_profile.current_profile["volume_preference"]
        
        # Apply dampening - only change if difference exceeds threshold
        volume_diff = abs(target_volume - self.last_volume)
        
        if volume_diff >= self.volume_change_threshold:
            self.volume = target_volume
            self.last_volume = self.volume
            self.last_distance_for_volume = distance  # Update tracked distance
            
            try:
                self.volume_controller.SetMasterVolumeLevelScalar(self.volume / 100.0, None)
                print(f"🔊 Volume: {self.volume:.1f}% (Δdist={distance_delta:+.2f}m, Δvol={volume_diff:+.1f}%)")
            except Exception as e:
                pass
        else:
            # Keep current volume (too small change)
            self.volume = self.last_volume
    
    def _pause_media(self):
        """Pause media playback using Windows media keys"""
        try:
            import win32api
            import win32con
            # Simulate media play/pause key (VK_MEDIA_PLAY_PAUSE = 0xB3)
            win32api.keybd_event(0xB3, 0, 0, 0)
            win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception as e:
            pass
    
    def _resume_media(self):
        """Resume media playback using Windows media keys"""
        try:
            import win32api
            import win32con
            # Simulate media play/pause key
            win32api.keybd_event(0xB3, 0, 0, 0)
            win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception as e:
            pass
    
    def _check_health_alerts(self):
        """Generate health and ergonomics alerts with eye strain"""
        # Session duration alerts
        if self.session_duration > 7200:  # 2 hours
            self.alerts.append("⚠️ URGENT: Take a 15-minute break immediately")
        elif self.session_duration > 3600:  # 1 hour
            self.alerts.append("💡 Consider taking a 5-minute break")
        
        # Eye strain alerts (only if calibration complete)
        if self.ergonomics.calibration_complete:
            if self.ergonomics.break_reminder:
                strain = self.ergonomics.eye_strain_level
                if strain >= 4:
                    self.alerts.append("🚨 HIGH eye strain - Look away NOW (20-20-20 rule)")
                elif strain >= 2:
                    self.alerts.append("👁️ Eye strain detected - Rest your eyes")
            
            # Low blink rate warning
            if self.ergonomics.blink_rate > 0 and self.ergonomics.blink_rate < 10:
                self.alerts.append(f"😐 Low blink rate ({self.ergonomics.blink_rate}/min) - Blink more")
        
        # Posture alerts with specific recommendations
        if self.ergonomics.posture_status == "poor":
            recommendations = []
            if self.ergonomics.forward_head_distance > 0.1:
                recommendations.append("pull head back")
            if self.ergonomics.shoulder_alignment < 70:
                recommendations.append("level shoulders")
            if self.ergonomics.spine_angle > 15:
                recommendations.append("straighten spine")
            
            if recommendations:
                self.alerts.append(f"🧍 Poor posture: {', '.join(recommendations)}")
            else:
                self.alerts.append("🧍 Poor posture detected - Adjust position")
        elif self.ergonomics.posture_status == "fair":
            self.alerts.append("📐 Posture could improve - Check alignment")
    
    def _get_current_state(self):
        """Return current workspace state with all metrics"""
        return {
            "brightness": self.brightness,
            "volume": self.volume,
            "color_temp": self.color_temp,
            "session_duration": self.session_duration,
            "distance": {
                "current": self.current_distance,
                "stable": self.stable_distance
            },
            "presence": {
                "detected": self.face_detected,
                "absence_duration": self.absence_duration,
                "media_paused": self.media_paused
            },
            "ergonomics_status": {
                "posture": self.ergonomics.posture_status,
                "posture_score": self.ergonomics.posture_score,
                "forward_head": self.ergonomics.forward_head_distance,
                "spine_angle": self.ergonomics.spine_angle,
                "blink_rate": self.ergonomics.blink_rate,
                "eye_strain": self.ergonomics.eye_strain_level,
                "calibrated": self.ergonomics.calibration_complete
            },
            "alerts": self.alerts.copy()
        }
    
    def _calculate_distance(self, face_results):
        """Calculate distance from face landmarks using face width"""
        if not face_results or not face_results.multi_face_landmarks:
            return 1.0  # Default fallback
        
        landmarks = face_results.multi_face_landmarks[0].landmark
        
        # Get face width using landmarks (e.g., left to right face boundary)
        # Using landmark 234 (left face) and 454 (right face)
        left_x = landmarks[234].x
        right_x = landmarks[454].x
        face_width_normalized = abs(right_x - left_x)
        
        # Assuming frame width of 640 pixels (typical 720p webcam)
        frame_width = 640
        face_width_px = face_width_normalized * frame_width
        
        # Calculate distance using known face width (16 cm average)
        if face_width_px > 0:
            KNOWN_FACE_WIDTH_CM = 16.0
            FOCAL_LENGTH_PX = 700.0
            distance_cm = (KNOWN_FACE_WIDTH_CM * FOCAL_LENGTH_PX) / face_width_px
            distance_m = distance_cm / 100.0
            # Clamp to reasonable range
            return float(np.clip(distance_m, MIN_DISTANCE_M, MAX_DISTANCE_M))
        
        return 1.0
    
    def _get_audio_level(self):
        """Get current audio noise level"""
        return self.audio_monitor.get_noise_level()

def main():
    # Initialize workspace optimizer
    optimizer = WorkspaceOptimizer()
    
    # Initialize camera
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ Error: Cannot access webcam.")
        return
    
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    print("\nEADA Pro is running... Press 'q' to quit.\n")
    
    try:
        while True:
            success, frame = cap.read()
            if not success:
                continue
            
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Process face and pose
            face_results = face_mesh.process(rgb)
            pose_results = pose.process(rgb)
            
            # Calculate distance from face landmarks
            distance = optimizer._calculate_distance(face_results)
            noise_level = optimizer._get_audio_level()
            
            # Optimize workspace
            state = optimizer.optimize_workspace(
                distance,
                noise_level,
                face_results.multi_face_landmarks[0] if face_results.multi_face_landmarks else None,
                pose_results.pose_landmarks if pose_results.pose_landmarks else None
            )
            
            # Display results
            _draw_interface(frame, state, frame_w, frame_h)
            
            cv2.imshow("EADA Pro - Smart Workspace Optimizer", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    
    finally:
        optimizer.audio_monitor.stop()
        cap.release()
        cv2.destroyAllWindows()
        print("\n✅ EADA Pro stopped cleanly.")

def _draw_interface(frame, state, width, height):
    """Draw comprehensive user interface"""
    # Basic metrics
    cv2.putText(frame, f"Brightness: {state['brightness']:.1f}%", (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
    cv2.putText(frame, f"Volume: {state['volume']:.1f}%", (20, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
    cv2.putText(frame, f"Session: {int(state['session_duration'] / 60)}min", (20, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
    
    # Distance info
    dist_stable = state['distance']['stable']
    cv2.putText(frame, f"Distance: {dist_stable:.2f}m", (20, 120),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)
    
    # Presence indicator
    presence_color = (0, 255, 0) if state['presence']['detected'] else (0, 0, 255)
    presence_text = "Present" if state['presence']['detected'] else f"Absent {state['presence']['absence_duration']:.1f}s"
    cv2.putText(frame, presence_text, (width - 180, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, presence_color, 2)
    
    # Posture status
    posture = state['ergonomics_status']['posture']
    posture_score = state['ergonomics_status']['posture_score']
    
    status_colors = {
        "excellent": (0, 255, 0),
        "good": (0, 200, 200),
        "fair": (0, 165, 255),
        "poor": (0, 0, 255),
        "unknown": (128, 128, 128)
    }
    status_color = status_colors.get(posture, (255, 255, 255))
    
    cv2.putText(frame, f"Posture: {posture} ({posture_score:.0f}/100)", (20, 150),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
    
    # Eye metrics (if calibrated)
    if state['ergonomics_status']['calibrated']:
        blink_rate = state['ergonomics_status']['blink_rate']
        eye_strain = state['ergonomics_status']['eye_strain']
        
        # Color code strain level
        strain_color = (0, 255, 0) if eye_strain <= 1 else (0, 200, 200) if eye_strain <= 2 else (0, 165, 255) if eye_strain <= 3 else (0, 0, 255)
        
        cv2.putText(frame, f"Blinks: {blink_rate}/min | Strain: {eye_strain}/5", (20, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, strain_color, 2)
    else:
        cv2.putText(frame, "Calibrating eye detection...", (20, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 165, 0), 2)
    
    # Spine angle
    spine_angle = state['ergonomics_status']['spine_angle']
    cv2.putText(frame, f"Spine: {spine_angle:.1f}deg", (20, 210),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    
    # Alerts (bottom of frame, last 3)
    for i, alert in enumerate(state['alerts'][-3:]):
        alert_text = alert[:60] + "..." if len(alert) > 60 else alert
        cv2.putText(frame, alert_text, (20, height - 90 + i*30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

if __name__ == "__main__":
    main()