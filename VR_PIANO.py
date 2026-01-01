"""

VR PIANO - Endless Rhythm Game with Hand Tracking


This is a bonus project combining:
- Real-time hand tracking (MediaPipe)
- Audio synthesis (pygame + numpy)
- 3D perspective projection
- Multi-threading for smooth camera processing
- Particle effects

HOW IT WORKS:
1. Camera runs in a SEPARATE THREAD so hand detection doesn't block the game
2. Notes spawn based on a musical melody (Suzume theme)
3. Player "taps" notes by curling fingers toward palm
4. Each note plays a synthesized bell/kalimba sound

CONTROLS:
- Fingers: Curl finger down = activate lane (Index=0, Middle=1, Ring=2, Pinky=3)
- Keyboard: A, S, D, F keys as fallback
- ESC: Quit

CONCEPTS DEMONSTRATED:
- Threading: Camera runs independently from game loop
- Audio Synthesis: Generating sounds mathematically (sine waves + overtones)
- 3D Projection: Converting 3D world coordinates to 2D screen positions
- Collision Detection: Checking if finger is down when note reaches target



"""

import pygame
import numpy as np
import random
import sys
import math
import cv2
import mediapipe as mp
import threading  # For running camera in background
import time


# --- Constants ---
WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 768
FPS = 60

# Neon Colors
COLOR_BG = (10, 5, 20)
COLOR_LANES = [
    (255, 0, 100),   # Pink (Lane 0 - Index)
    (0, 255, 255),   # Cyan (Lane 1 - Middle)
    (0, 255, 0),     # Green (Lane 2 - Ring)
    (255, 255, 0)    # Yellow (Lane 3 - Pinky)
]
COLOR_GRID = (50, 0, 100)
SAMPLE_RATE = 44100

# --- MUSIC DATA ---
# Extended frequency list for the full melody
NOTE_FREQS = {
    "5":  392.00,  # G4
    "6":  440.00,  # A4
    "7":  493.88,  # B4
    "1'": 523.25,  # C5
    "2'": 587.33,  # D5
    "3'": 659.25,  # E5
    "4'": 698.46,  # F5
    "5'": 783.99,  # G5
    "6'": 880.00,  # A5
    "7'": 987.77   # B5
}

# The Extended Melody (Approx 23 seconds loop)
# Format: [Time (seconds), Note Name, Recommended Lane]
RAW_MELODY = [
    # --- Phrase 1 ---
    (2.0, "2'", 1), (2.4, "6'", 3), (2.8, "6'", 3), (3.2, "4'", 2), 
    (3.6, "3'", 1), (4.0, "2'", 1), (4.4, "1'", 0), 
    
    # --- Phrase 2 ---
    (5.5, "6",  0), (5.8, "1'", 0), (6.1, "2'", 1), (6.4, "3'", 1), 
    (6.7, "4'", 2), (7.0, "5'", 2), (7.3, "4'", 2), (7.6, "3'", 1), (8.0, "2'", 1), 
    
    # Loop padding removed (Deleted as requested)
]

# Standard Loop
LOOP_DURATION = 8.0


# AUDIO SYNTHESIS

# Instead of loading audio files, we GENERATE sounds mathematically!
# This is called "additive synthesis" - combining sine waves.

class Synthesizer:
    """
    Generates musical notes using math (sine waves).
    
    HOW AUDIO SYNTHESIS WORKS:
    - Sound is vibration, measured in Hz (cycles per second)
    - A4 note = 440 Hz = 440 vibrations per second
    - We create a sine wave at that frequency
    - Add "overtones" (higher frequencies) for richer sound
    - Apply an "envelope" (volume changes over time) for natural decay
    """
    
    @staticmethod
    def generate_note(frequency, duration=1.5, volume=0.8):
        """
        Generates a bell/kalimba-like sound for the given frequency.
        
        Args:
            frequency: Hz value (e.g., 440 for A4)
            duration: How long the sound lasts (seconds)
            volume: 0.0 to 1.0
        
        Returns:
            pygame.Sound object ready to play
        """
        # Create time array: 0 to duration, with SAMPLE_RATE points per second
        n_samples = int(SAMPLE_RATE * duration)
        t = np.linspace(0, duration, n_samples, False)
        
        # FUNDAMENTAL FREQUENCY (the main note)
        # sin(2π * frequency * time) creates the basic tone
        wave = np.sin(2 * np.pi * frequency * t)
        
        # OVERTONE (the "ting" sound that makes it bell-like)
        # 6.2x the frequency = an inharmonic overtone
        # exp(-10 * t) makes it fade quickly (the initial click)
        wave += 0.2 * np.sin(2 * np.pi * frequency * 6.2 * t) * np.exp(-10 * t)
        
        # ENVELOPE (volume shape over time)
        # exp(-3 * t) = exponential decay (loud start, fades to silence)
        envelope = np.exp(-3.0 * t)
        # Soften the very beginning to prevent "click" artifacts
        envelope[:100] = np.linspace(0, 1, 100)
        
        # Apply envelope to wave
        wave *= envelope * volume
        
        # Convert to 16-bit integer format (what speakers understand)
        # Range: -32768 to 32767
        audio_data = (wave * 32767).astype(np.int16)
        
        # Create stereo sound (same audio in left and right channels)
        return pygame.sndarray.make_sound(np.column_stack((audio_data, audio_data)))



# PARTICLE SYSTEM

# Simple particles that burst when you hit a note

class Particle:
    """
    Simple particle for visual feedback when hitting notes.
    Each particle has position, velocity, and fades over time.
    """
    def __init__(self, x, y, color):
        self.x = x; self.y = y; self.color = color
        self.vx = random.uniform(-5, 5)      # Random horizontal velocity
        self.vy = random.uniform(-10, -2)    # Upward velocity
        self.life = 1.0                       # 1.0 = fully alive, 0.0 = dead
        self.size = random.randint(3, 8)
        
    def update(self):
        self.x += self.vx                    # Move horizontally
        self.y += self.vy                    # Move vertically
        self.vy += 0.5                       # Gravity pulls down
        self.life -= 0.05                    # Fade out
        
    def draw(self, surface):
        if self.life > 0:
            alpha = int(self.life * 255)     # Convert life to alpha (opacity)
            s = pygame.Surface((self.size*2, self.size*2), pygame.SRCALPHA)
            pygame.draw.circle(s, (*self.color, alpha), (self.size, self.size), self.size)
            surface.blit(s, (self.x - self.size, self.y - self.size))



# THREADING: CAMERA RUNS IN BACKGROUND

# 
# WHY THREADING?
# Without threading, the camera processing would block the game:
#   1. Game waits for camera frame (100ms)
#   2. Game processes hand detection (50ms)
#   3. Game draws graphics (16ms)
#   4. Result: Choppy 6 FPS game!
#
# With threading:
#   1. Camera thread processes frames continuously in background
#   2. Game thread renders at 60 FPS
#   3. Game reads the LATEST finger states when needed
#   4. Result: Smooth 60 FPS game!
#
# IMPORTANT CONCEPTS:
# - daemon=True: Thread dies when main program exits
# - self.lock: Prevents reading/writing data at the same time (race condition)
# - with self.lock: Automatically acquires and releases the lock

class CameraThread(threading.Thread):
    """
    Runs camera and hand detection in a separate thread.
    
    This allows the game to run at 60 FPS while hand detection
    runs at whatever speed the camera/AI can handle (~15-30 FPS).
    """
    
    def __init__(self, cam_idx=0):
        super().__init__()
        self.daemon = True                   # Thread dies when main program exits
        self.cam_idx = cam_idx
        self.running = True                  # Set to False to stop thread
        self.detected_hand = False
        self.frame_surf = None               # Latest camera frame for display
        self.finger_states = [False, False, False, False]  # [Index, Middle, Ring, Pinky]
        self.lock = threading.Lock()         # Prevents race conditions
        
    def run(self):
        """Main thread loop - runs until self.running is False"""
        print(f"Starting Camera Thread on index {self.cam_idx}...")
        cap = cv2.VideoCapture(self.cam_idx)
        if not cap.isOpened():
            # Fallback if requested camera fails
            cap = cv2.VideoCapture(0)
             
        # Initialize MediaPipe hand detector
        mp_hands = mp.solutions.hands
        hands = mp_hands.Hands(
            static_image_mode=False,         # Video mode (faster)
            max_num_hands=1,                 # Only detect one hand
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )

        
        while self.running:
            ret, frame = cap.read()
            if ret:
                frame = cv2.flip(frame, 1)  # Mirror horizontally
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = hands.process(rgb)
                
                detected = False
                states = [False, False, False, False]
                
                if results.multi_hand_landmarks:
                    detected = True
                    lms = results.multi_hand_landmarks[0].landmark
                    # Finger logic: Tip Y > PIP Y
                    states[0] = lms[8].y > lms[6].y   # Index
                    states[1] = lms[12].y > lms[10].y # Middle
                    states[2] = lms[16].y > lms[14].y # Ring
                    states[3] = lms[20].y > lms[18].y # Pinky
                    
                    mp.solutions.drawing_utils.draw_landmarks(frame, results.multi_hand_landmarks[0], mp_hands.HAND_CONNECTIONS)
                
                small = cv2.resize(frame, (360, 270))
                small_rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                surf = pygame.image.frombuffer(small_rgb.tobytes(), small_rgb.shape[1::-1], "RGB")
                
                with self.lock:
                    self.detected_hand = detected
                    self.finger_states = states
                    self.frame_surf = surf
            else:
                time.sleep(0.1)
        cap.release()

class Game:
    def __init__(self):
        pygame.init()
        pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=1024)
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("SUZUME - ENDLESS LOOP")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Arial", 20, bold=True)
        self.large_font = pygame.font.SysFont("Arial", 48, bold=True)
        
        # --- PRE-GENERATE SOUND LIBRARY ---
        print("Generating audio...")
        self.sound_library = {}
        for note_name, freq in NOTE_FREQS.items():
            self.sound_library[note_name] = Synthesizer.generate_note(freq)
        print("Audio ready.")

        self.key_map = {pygame.K_a: 0, pygame.K_s: 1, pygame.K_d: 2, pygame.K_f: 3}
        self.cam_thread = CameraThread(cam_idx=2)
        self.cam_thread.start()
        
        self.z_speed = 25.0
        self.camera_z = 0.0
        self.notes = [] 
        self.particles = []
        self.score = 0
        self.song_index = 0
        self.time_elapsed = 0.0
        self.active_lanes = [False]*4

    def spawn_from_song(self, dt):
        self.time_elapsed += dt
        travel_time = 50.0 / self.z_speed
        
        while self.song_index < len(RAW_MELODY):
            # Data format: (Time, NoteName, Lane)
            hit_time, note_name, lane = RAW_MELODY[self.song_index]
            
            if self.time_elapsed + travel_time >= hit_time:
                self.notes.append({
                    "lane": lane,
                    "z": self.camera_z + 50.0,
                    "active": True,
                    "note_name": note_name  # The note carries its own pitch name!
                })
                self.song_index += 1
            else:
                break
        
        # --- SEAMLESS LOOP LOGIC ---
        # When we hit the loop duration, we rewind time without clearing the screen
        if self.time_elapsed >= LOOP_DURATION:
            self.time_elapsed -= LOOP_DURATION
            self.song_index = 0

    def get_cam_data(self):
        with self.cam_thread.lock:
             return self.cam_thread.finger_states, self.cam_thread.detected_hand, self.cam_thread.frame_surf

    def update(self):
        dt = self.clock.tick(FPS) / 1000.0
        
        for event in pygame.event.get():
            if event.type == pygame.QUIT: self.cleanup(); sys.exit()
            if event.type == pygame.KEYDOWN:
                 if event.key == pygame.K_ESCAPE: self.cleanup(); sys.exit()
                 if event.key in self.key_map: self.active_lanes[self.key_map[event.key]] = True
            if event.type == pygame.KEYUP:
                 if event.key in self.key_map: self.active_lanes[self.key_map[event.key]] = False

        finger_states, detected, cam_surf = self.get_cam_data()
        self.cam_surf = cam_surf
        self.finger_states_display = finger_states
        
        if detected:
            for i in range(4):
                if finger_states[i]: self.active_lanes[i] = True
                else:
                    # Keyboard fallback
                    keys = pygame.key.get_pressed()
                    key_codes = [pygame.K_a, pygame.K_s, pygame.K_d, pygame.K_f]
                    if not keys[key_codes[i]]: self.active_lanes[i] = False

        # Collision Check
        perfect_z = self.camera_z + 4.0
        
        for i in range(4):
            if self.active_lanes[i]:
                for note in self.notes:
                      if note["active"] and note["lane"] == i:
                           if abs(note["z"] - perfect_z) < 2.5: # 2.5 margin
                                self.hit_note(note, i)

        self.camera_z += self.z_speed * dt
        self.spawn_from_song(dt)
        self.notes = [n for n in self.notes if n["z"] > self.camera_z - 5]
        for p in self.particles: p.update()
        self.particles = [p for p in self.particles if p.life > 0]

    def hit_note(self, note, lane):
        note["active"] = False
        
        # Play the specific note
        note_name = note["note_name"]
        if note_name in self.sound_library:
            self.sound_library[note_name].play()
            
        self.score += 100
        x, y = self.project((lane-1.5)*2.0, 0, self.camera_z+4.0)
        if x: 
             for _ in range(10): self.particles.append(Particle(x, y, COLOR_LANES[lane]))

    def project(self, x, y, z):
        rel_x = x; rel_y = y - 2.0
        rel_z = z - self.camera_z + 8.0
        if rel_z <= 0.1: return None
        scale = 600 / rel_z
        return (WINDOW_WIDTH/2 + rel_x * scale, WINDOW_HEIGHT/2 - rel_y * scale)

    def draw(self):
        self.screen.fill(COLOR_BG)
        
        # Grid
        gz = (int(self.camera_z)//10)*10
        for z in range(gx := int(gz), gx + 200, 5):
             p1 = self.project(-8, 0, z); p2 = self.project(8, 0, z)
             if p1 and p2: pygame.draw.line(self.screen, (*COLOR_GRID, 100), p1, p2, 1)
        for i in range(5):
             p_s = self.project((i-2)*2.0, 0, self.camera_z); p_e = self.project((i-2)*2.0, 0, self.camera_z+200)
             if p_s and p_e: pygame.draw.line(self.screen, COLOR_GRID, p_s, p_e, 2)
             
        # Lanes
        for i in range(4):
             p = self.project((i-1.5)*2.0, 0, self.camera_z+4.0)
             if p:
                  col = COLOR_LANES[i]
                  if self.active_lanes[i]:
                       s = pygame.Surface((60, 60), pygame.SRCALPHA)
                       pygame.draw.circle(s, (*col, 100), (30, 30), 25)
                       self.screen.blit(s, (p[0]-30, p[1]-30))
                  pygame.draw.circle(self.screen, col, (int(p[0]), int(p[1])), 15, 0 if self.active_lanes[i] else 2)

        # Notes
        for n in self.notes:
             if not n["active"]: continue
             lane = n["lane"]
             xc = (lane - 1.5) * 2.0
             p1 = self.project(xc-0.8, 0, n["z"]); p2 = self.project(xc+0.8, 0, n["z"])
             p3 = self.project(xc+0.8, 0.5, n["z"]); p4 = self.project(xc-0.8, 0.5, n["z"])
             if p1 and p3:
                  pygame.draw.polygon(self.screen, COLOR_LANES[lane], [p1, p2, p3, p4])
                  # Draw the Note Name on the block (e.g. "6", "1'")
                  if n.get("note_name"):
                       txt = self.font.render(n["note_name"], True, (0,0,0))
                       self.screen.blit(txt, (p3[0]-10, p3[1]-20))
                       
        for p in self.particles: p.draw(self.screen)
        
        # Cam Feed
        if self.cam_surf:
             self.screen.blit(self.cam_surf, (WINDOW_WIDTH - 380, WINDOW_HEIGHT - 300))
        
        # Indicators
        ind_x = WINDOW_WIDTH//2 - 100; ind_y = WINDOW_HEIGHT - 80
        for i in range(4):
             is_down = getattr(self, 'finger_states_display', [False]*4)[i]
             col = (0, 255, 0) if is_down else (50, 50, 50)
             pygame.draw.circle(self.screen, col, (ind_x + i*60, ind_y), 20)

        self.screen.blit(self.large_font.render(f"{self.score}", True, (255, 255, 255)), (30, 30))
        pygame.display.flip()

    def cleanup(self):
         self.cam_thread.running = False
         self.cam_thread.join()
         pygame.quit()

if __name__ == "__main__":
    g = Game()
    while True:
        g.update()
        g.draw()