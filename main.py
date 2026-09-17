#!/usr/bin/env python3 
import cv2
import requests
import base64
import json
import sys, fcntl
import threading
import tkinter as tk
from tkinter import scrolledtext
from evdev import InputDevice, categorize, ecodes

try:
      _lock_file = open('/tmp/ai_overlay_test.lock', 'w')
      fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
       sys.exit(0)

last_ai_response = ""
# ========================================================
# 1. HARDWARE CONFIGURATION
# ========================================================
DEVICE_PATH = '/dev/input/by-id/usb-Usb_KeyBoard_Usb_KeyBoard-event-kbd'


class AIOverlayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Overlay")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        
        # Dimensions, initial positions, transparency
        self.width, self.height = 550, 250
        self.x_pos, self.y_pos = 100, 100
        self.opacity = 0.85
        self.is_visible = True
        
        self.root.geometry(f"{self.width}x{self.height}+{self.x_pos}+{self.y_pos}")
        self.root.attributes("-alpha", self.opacity)

        # NEW AUTO-SCROLLING BORDERLESS TEXT WINDOW CONTAINER
        self.text_area = scrolledtext.ScrolledText(
            self.root,
            wrap=tk.WORD,
            fg="cyan",
            bg="black",
            insertbackground="cyan", # Cursor color
            font=("Courier", 14, "bold"),
            bd=0,
            highlightthickness=0
        )
        self.text_area.pack(fill="both", expand=True, padx=10, pady=10)
        self.root.configure(bg="black")
        
        # Insert initial placeholder text
        self.text_area.insert(tk.END, "[System Active]\nPress Numpad Enter to process...")
        self.text_area.configure(state='disabled') # Prevent accidental user manual edits

        # Hold tracking physics config
	#start the continuous UI movement loop clock

        self.moving_directions = {"up": False, "down": False, "left": False, "right": False}
        self.move_speed = 6  


        # Boot hardware listener threads
        self.device = None
        self.running = True
        self.input_thread = threading.Thread(target=self.listen_to_hardware, daemon=True)
        self.input_thread.start()
        self.process_movement_tick()
        self.root.mainloop()

    def process_movement_tick(self):
        if not self.running:
            return
        changed = False
        if self.moving_directions["up"]: self.y_pos -= self.move_speed; changed = True
        if self.moving_directions["down"]: self.y_pos += self.move_speed; changed = True
        if self.moving_directions["left"]: self.x_pos -= self.move_speed; changed = True
        if self.moving_directions["right"]: self.x_pos += self.move_speed; changed = True
        if changed:
            self.root.geometry(f"{self.width}x{self.height}+{self.x_pos}+{self.y_pos}")
        self.root.after(16, self.process_movement_tick)

    def update_text_safe(self, new_text, append=False):
        """ Thread-safe interaction updating text and locking scroll to the bottom """
        def action():
            self.text_area.configure(state='normal')
            if not append:
                self.text_area.delete('1.0', tk.END)
            self.text_area.insert(tk.END, new_text)
            
            # CRITICAL FIX: Push the scroll view window down to follow the newest line
            self.text_area.see(tk.END)
            
            self.text_area.configure(state='disabled')
        self.root.after(0, action)

    def run_vision_solver(self, reprompt=False):
        status = "[Re-analyzing Screen Frame...]" if reprompt else "[Analyzing Screen Frame....]"
        self.update_text_safe(status)
        
        global last_ai_response
        cap = cv2.VideoCapture(2, cv2.CAP_V4L2)
        if not cap.isOpened():  cap = cv2.VideoCapture(2)
        if not cap.isOpened():
            self.update_text_safe("Error: Cannot reach /dev/video2.\nToggle 'Virtual Camera' in OBS.")
            return

        # ===================================================================================================
        # HARDWARE BUFFER FIX: Skip stale frames and let driver stabilize
        # ===================================================================================================
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        import time
        while True: 
            grabbed = cap.grab()
            break

        ret, frame = cap.retrieve()
        cap.release()

        if not ret or frame is None:
             self.update_text_safe("Error: Capture card failed to fetch frame.")
             return

        small_frame = cv2.resize(frame, (1280, 720))
        _, buffer = cv2.imencode('.jpg', small_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        b64_image = base64.b64encode(buffer).decode('utf-8')

        #1. Builds reprompt text cleanly outside payload dictionary
        correction_header = ""
        if reprompt:
            
            if last_ai_response:
                correction_header = f"Your previous answer was inaccurate: '{last_ai_response}'. Re-analyze the frame and correct your mistakes completely. \n\n"
            else:
                correction_header = "Perform an immediate, highly accurate re-analysis of this frame. double check all details for errors. Do not answer with the same response, as it was verified as incorrect, maintain the context for future questions so that you improve your performance over time. \n\n"

        main_rules = (
                "You are a discrete mathematics text-only exam assistant. Analyze the screen image carefully. and seperate the question presented into 1 of 3 categories: Relations, Mathematical Induction, or Combinatorics."
                "After Identifying the topic and apply these rules:\n"
                "1. SCAN CHANNELS: Locate the core question text and identify if multiple-choice options are present on screen.\n"
                "2. CHAIN OF THOUGHT: Break down the problem step-by-step using plain text. For math, expand the fractions/formulas before multiplying. For code or definitions, isolate the true rule or behavior.\n"
                "3. MATCH AND VERIFY: If multiple-choice options exist, explicitly cross-reference your final logic against the choices printed on the monitor. Pay close attention to the answer choices if the answers say:'(Check all that apply)' Ensure to provide ALL correct answers. Do not invent letters or text.\n"
                "4. VISUAL FORMAT: Output your solution in clean human language text. Do not use and code syntax  when representing the question or answer. Use (+ = plus),(- =  minus),(* = multiplication),(/ = division) state which variable is the numerator and which is the denominator when reasoning through questions. Use '_' between parent and subscript for subscripted variables,operators,etc. print the answers as similar to the formatting of the answer boxes provided \n"
                "5. Before giving your final answer, spend as least as much effort attempting to disprove your answer as you spent generating it. Verify every assumption, recompute any calculations independently and search for any contradictions. Only present an answer after it has survived your own critical review."
                "6. LINE NUMBERS: Keep your final response short, structured, and under 6 total rows so it is instantly scannable. When presenting the solution if it has a corresponding multiple choice selection name that selection followed by the answer corresponding to it. If it has multiple soltuions, list which answers belong whether it be ranking or drag and drop into the correct fields."
            )

        #2. Combine them and send to your local Ollama instance
        url = "http://localhost:11434/api/generate"
        payload = {
             "model": "qwen2.5vl:7B",
             "context": [], #CRITICAL: Forcefully clears old conversation memory pipelines completely.
             "prompt": correction_header + main_rules,
             "images": [b64_image],
             "stream": True
            
        }

        try:
            response = requests.post(url, json=payload, stream=True)
            response.raise_for_status()
            
            # Clear window once streaming commences
            self.update_text_safe("")
            
            full_response_text = ""
            for line in response.iter_lines():
                if line and self.running:
                    chunk = json.loads(line.decode('utf-8'))
                    token = chunk.get('response', '')
                    full_response_text += token
                    # Append tokens one-by-one with real-time viewport auto-scroll tracking
                    self.update_text_safe(token, append=True)
                    if chunk.get('done', False):
                        last_ai_response = full_response_text
                        break
        except Exception as e:
            self.update_text_safe(f"API Connection Error:\n{str(e)}")

    def trigger_reanalysis(self):
        """ Spawns the vision solver inside a background thread with the reprompt state """
        threading.Thread(target=self.run_vision_solver, kwargs={'reprompt': True}, daemon=True).start()

    def listen_to_hardware(self):
        try:
            self.device = InputDevice(DEVICE_PATH)
            self.device.grab()
            
            for event in self.device.read_loop():
                if not self.running:
                    break
                if event.type == ecodes.EV_KEY:
                    key_event = categorize(event)
                    sc = key_event.scancode
                    
                    if key_event.keystate == key_event.key_down:
                        if sc == ecodes.KEY_KP8: self.moving_directions["up"] = True
                        elif sc == ecodes.KEY_KP9:  # Numpad 9 -> Page Up
                            self.root.after(0, lambda: self.text_area.yview_scroll(-2, "units"))
                        elif sc == ecodes.KEY_KP3:  # Numpad 3 -> Page Down
                            self.root.after(0, lambda: self.text_area.yview_scroll(2, "units"))
                        elif sc == ecodes.KEY_KP2: self.moving_directions["down"] = True
                        elif sc == ecodes.KEY_KP4: self.moving_directions["left"] = True
                        elif sc == ecodes.KEY_KP6: self.moving_directions["right"] = True
                        elif sc == ecodes.KEY_KPENTER:
                            threading.Thread(target=self.run_vision_solver, daemon=True).start()
                        elif sc == ecodes.KEY_KP0:
                            self.trigger_reanalysis()
                        elif sc == ecodes.KEY_KPPLUS:
                            self.opacity = min(1.0, self.opacity + 0.05)
                            self.root.after(0, lambda: self.root.attributes("-alpha", self.opacity))
                        elif sc == ecodes.KEY_KPMINUS:
                            self.opacity = max(0.1, self.opacity - 0.05)
                            self.root.after(0, lambda: self.root.attributes("-alpha", self.opacity))
                        elif sc == ecodes.KEY_KPDOT:
                            self.running = False
                            self.root.after(0, self.root.destroy)
                            break
                            
                    elif key_event.keystate == key_event.key_up:
                        if sc == ecodes.KEY_KP8: self.moving_directions["up"] = False
                        elif sc == ecodes.KEY_KP2: self.moving_directions["down"] = False
                        elif sc == ecodes.KEY_KP4: self.moving_directions["left"] = False
                        elif sc == ecodes.KEY_KP6: self.moving_directions["right"] = False

        except Exception as e:
            print(f"[Hardware Loop Error] {e}")
        finally:
            if self.device:
                try: self.device.ungrab()
                except: pass

if __name__ == "__main__":
    AIOverlayApp()


