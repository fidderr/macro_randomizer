import json
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, Menu, Spinbox
from pynput import keyboard, mouse
from pynput.keyboard import Controller as KeyboardController, Key
from pynput.keyboard import GlobalHotKeys
from pynput.mouse import Controller as MouseController, Button
import random  # For seeding randomness
import copy

# Attempt to import numpy, set flag if unavailable
try:
    import numpy as np
    numpy_available = True
except ImportError:
    numpy_available = False

# Attempt to import PIL for color checking
try:
    from PIL import ImageGrab
    pil_available = True
except ImportError:
    pil_available = False

# Warm up numpy random number generator to avoid delay on first use
if numpy_available:
    np.random.seed(0)  # Dummy seed for warmup
    np.random.random()  # Initialize RNG to prevent lag during first playback

# WindMouse constants and function (only if numpy is available)
if numpy_available:
    sqrt3 = np.sqrt(3)
    sqrt5 = np.sqrt(5)

    def wind_mouse(start_x, start_y, dest_x, dest_y, G_0=9, W_0=3, M_0=15, D_0=12, move_mouse=lambda x,y: None):
        '''
        WindMouse algorithm. Calls the move_mouse kwarg with each new step.
        Released under the terms of the GPLv3 license.
        G_0 - magnitude of the gravitational force
        W_0 - magnitude of the wind force fluctuations
        M_0 - maximum step size (velocity clip threshold)
        D_0 - distance where wind behavior changes from random to damped
        '''
        current_x, current_y = start_x, start_y
        v_x = v_y = W_x = W_y = 0
        while (dist := np.hypot(dest_x - start_x, dest_y - start_y)) >= 1:
            W_mag = min(W_0, dist)
            if dist >= D_0:
                W_x = W_x / sqrt3 + (2 * np.random.random() - 1) * W_mag / sqrt5
                W_y = W_y / sqrt3 + (2 * np.random.random() - 1) * W_mag / sqrt5
            else:
                W_x /= sqrt3
                W_y /= sqrt3
                if M_0 < 3:
                    M_0 = np.random.random() * 3 + 3
                else:
                    M_0 /= sqrt5
            v_x += W_x + G_0 * (dest_x - start_x) / dist
            v_y += W_y + G_0 * (dest_y - start_y) / dist
            v_mag = np.hypot(v_x, v_y)
            if v_mag > M_0:
                v_clip = M_0 / 2 + np.random.random() * M_0 / 2
                v_x = (v_x / v_mag) * v_clip
                v_y = (v_y / v_mag) * v_clip
            start_x += v_x
            start_y += v_y
            move_x = int(np.round(start_x))
            move_y = int(np.round(start_y))
            if current_x != move_x or current_y != move_y:
                move_mouse(current_x := move_x, current_y := move_y)
        return current_x, current_y

def human_move(start_x, start_y, dest_x, dest_y, duration, seed=42):
    if duration <= 0 or not numpy_available:
        mouse_controller.position = (dest_x, dest_y)
        return
    # Fix seed for reproducible paths (no randomness throwing off paths across runs)
    seed = abs(seed) % (2**32)  # Ensure seed is in 0 to 2**32 - 1
    np.random.seed(seed)
    random.seed(seed)
    # Use fixed parameters for consistency, but allow slight variation if desired
    G_0 = 9
    W_0 = 3
    M_0 = 15
    D_0 = 12
    path = []
    def collect(x, y):
        path.append((x, y))
    wind_mouse(start_x, start_y, dest_x, dest_y, G_0=G_0, W_0=W_0, M_0=M_0, D_0=D_0, move_mouse=collect)
    if not path:
        mouse_controller.position = (dest_x, dest_y)
        return
    num_steps = len(path)
    step_time = duration / num_steps
    for px, py in path:
        if not playback_active:
            break
        mouse_controller.position = (px, py)
        interruptible_sleep(step_time)

def interruptible_sleep(duration):
    if duration <= 0:
        return
    start = time.time()
    end_time = start + duration
    if duration < 0.01:  # Use busy wait for small durations to avoid sleep resolution issues
        while time.time() < end_time and playback_active:
            pass
    else:
        while time.time() < end_time and playback_active:
            remaining = end_time - time.time()
            if remaining > 0:
                time.sleep(min(0.001, remaining))

# Global variables
actions = []  # List to store recorded/edited actions
start_time = None
recording = False
listeners = {}
current_filename = None
drag_data = {"source": None}
selected_idx = None
press_times = {}  # Track press times for timestamp
capture_listener_kb = None
capture_listener_mouse = None
playback_active = False  # Track playback state
playback_thread = None  # Track playback thread
hotkey_listener = None  # Global hotkey listener
pressed_items = []  # List of (controller, key_or_button)
repeat_mode = "Loops"  # Default repeat mode
repeat_value = 1.0  # Default repeat value (loops or minutes)
prev_target = None
potential_source = None
drag_initiated = False
press_y = 0
overlay = None
canvas = None
preview_overlay = None
preview_canvas = None
sparse_recording = False
last_ts = None
copied_actions = []  # Clipboard for copied actions
drag_start_pos = None  # For detecting drag during clicks in recording
drag_rect = None

# Controllers
kb_controller = KeyboardController()
mouse_controller = MouseController()

# Action types
ACTION_TYPES = ['key_action', 'mouse_move', 'color_check', 'loop_start', 'loop_end']

# Tooltip class for user-friendly hints (modified to use a fixed label at the bottom)
class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        tooltip_var.set(self.text)

    def hide(self, event=None):
        tooltip_var.set("")

def update_tree():
    tree.delete(*tree.get_children())
    for idx, action in enumerate(actions):
        min_delay = action.get('min_delay', 0.0)
        max_delay = action.get('max_delay', 0.0)
        delay_str = f"{min_delay:.3f} - {max_delay:.3f}"
        details = get_action_details(action)
        comment = action.get('comment', '')
        tag = 'even' if idx % 2 == 0 else 'odd'
        tree.insert("", tk.END, iid=str(idx), values=(delay_str, action['type'], details, comment), tags=(tag,))

def get_action_details(action):
    if action['type'] == 'key_action':
        key = action.get('key', '')
        if key.startswith('mouse.'):
            button = key[6:].capitalize()
            return f"Click Mouse Button: {button}"
        else:
            return f"Press Key: {key}"
    elif action['type'] == 'mouse_move':
        min_x = action.get('min_x', 0)
        max_x = action.get('max_x', 0)
        min_y = action.get('min_y', 0)
        max_y = action.get('max_y', 0)
        return f"Position: ({min_x}-{max_x}, {min_y}-{max_y})"
    elif action['type'] == 'color_check':
        color = action.get('expected_color', '#000000')
        return f"Expected Color: {color}"
    elif action['type'] == 'loop_start':
        name = action.get('name', '')
        min_loops = action.get('min_loops', 1)
        max_loops = action.get('max_loops', 1)
        return f"Start Loop '{name}' {min_loops}-{max_loops} times"
    elif action['type'] == 'loop_end':
        name = action.get('name', '')
        return f"End Loop '{name}'"
    return ""

def on_press(key):
    global recording
    if recording:
        key_str = str(key).replace("'", "") if hasattr(key, 'char') else str(key)
        press_times[key_str] = time.time() - start_time
        if key == keyboard.Key.esc:
            stop_recording()
            return False

def on_release(key):
    global recording, last_ts
    if recording:
        key_str = str(key).replace("'", "") if hasattr(key, 'char') else str(key)
        timestamp = press_times.get(key_str, time.time() - start_time)
        actions.append({'type': 'key_action', 'key': key_str, 'timestamp': timestamp, 'comment': ''})
        press_times.pop(key_str, None)
        if sparse_recording:
            last_ts = time.time() - start_time

def on_move(x, y):
    if recording and not sparse_recording:
        timestamp = time.time() - start_time
        actions.append({'type': 'mouse_move', 'min_x': x, 'max_x': x, 'min_y': y, 'max_y': y, 'timestamp': timestamp, 'comment': ''})

def on_move_sparse(x, y):
    global drag_rect, overlay, canvas
    if recording and sparse_recording and drag_start_pos:
        if block_underlying_var.get():  # Only create/draw if block is on
            if not overlay:
                create_recording_overlay()
            if canvas:
                if drag_rect:
                    canvas.delete(drag_rect)
                sx, sy = drag_start_pos
                drag_rect = canvas.create_rectangle(sx, sy, x, y, outline='red', width=2)

def on_click(x, y, button, pressed):
    global drag_start_pos, drag_rect
    if recording:
        ts = time.time() - start_time
        button_key = f"mouse.{str(button).split('.')[-1]}"
        if pressed:
            drag_start_pos = (x, y)
            drag_rect = None
            press_times[button_key] = ts
            if not sparse_recording:
                actions.append({'type': 'mouse_move', 'min_x': x, 'max_x': x, 'min_y': y, 'max_y': y, 'timestamp': ts - 0.001, 'comment': ''})
        else:
            if sparse_recording:
                if drag_start_pos:
                    sx, sy = drag_start_pos
                    min_x = min(sx, x)
                    max_x = max(sx, x)
                    min_y = min(sy, y)
                    max_y = max(sy, y)
                    drag_width = max_x - min_x
                    drag_height = max_y - min_y
                    radius = 0
                    try:
                        radius = int(click_radius_var.get())
                        if radius < 0:
                            radius = 0
                    except ValueError:
                        pass
                    if drag_width <= 2 and drag_height <= 2:
                        # Treat as just click, apply radius
                        center_x = (min_x + max_x) // 2
                        center_y = (min_y + max_y) // 2
                        min_x = center_x - radius
                        max_x = center_x + radius
                        min_y = center_y - radius
                        max_y = center_y + radius
                        if block_underlying_var.get():
                            if not overlay:
                                create_recording_overlay()
                            if drag_rect:
                                canvas.delete(drag_rect)
                                drag_rect = None
                            if min_x == max_x and min_y == max_y:  # radius == 0
                                half = 10
                                canvas.create_line(min_x - half, min_y, min_x + half, min_y, fill='red', width=2)
                                canvas.create_line(min_x, min_y - half, min_x, min_y + half, fill='red', width=2)
                            else:
                                drag_rect = canvas.create_rectangle(min_x, min_y, max_x, max_y, outline='red', width=2)
                            overlay.after(500, destroy_recording_overlay)
                    else:
                        # Drag detected, destroy overlay immediately
                        if overlay:
                            destroy_recording_overlay()
                else:
                    min_x = x
                    max_x = x
                    min_y = y
                    max_y = y
                actions.append({'type': 'mouse_move', 'min_x': min_x, 'max_x': max_x, 'min_y': min_y, 'max_y': max_y, 'timestamp': press_times[button_key], 'comment': ''})
            else:
                actions.append({'type': 'mouse_move', 'min_x': x, 'max_x': x, 'min_y': y, 'max_y': y, 'timestamp': ts - 0.001, 'comment': ''})
            timestamp = press_times.get(button_key, ts)
            actions.append({'type': 'key_action', 'key': button_key, 'timestamp': timestamp, 'comment': ''})
            press_times.pop(button_key, None)
            if sparse_recording:
                last_ts = ts
            drag_start_pos = None

def create_recording_overlay():
    global overlay, canvas
    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.attributes('-topmost', True)
    overlay.attributes('-alpha', 0.3)  # Always dim when created
    w = root.winfo_screenwidth()
    h = root.winfo_screenheight()
    overlay.geometry(f"{w}x{h}+0+0")
    canvas = tk.Canvas(overlay, bg='black', highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

def destroy_recording_overlay():
    global overlay, canvas, drag_rect
    if overlay:
        overlay.destroy()
        overlay = None
        canvas = None
        drag_rect = None

def start_recording():
    global actions, start_time, recording, listeners, press_times, sparse_recording, last_ts, drag_start_pos, overlay, canvas, drag_rect
    if recording:
        messagebox.showwarning("Already Recording", "Recording is already in progress.")
        return
    if playback_active:
        messagebox.showwarning("Playback Active", "Cannot record while playback is running.")
        return
    if actions and messagebox.askyesno("Unsaved Actions", "Current actions will be cleared. Save first?"):
        save_macro()
    update_status("Recording starts in 3 seconds...")
    status_label.config(background='#ffdddd', foreground='black')
    if not numpy_available:
        update_status("Warning: numpy not installed, mouse movements will be instant.")
    record_btn.config(state=tk.DISABLED)
    start_stop_btn.config(state=tk.DISABLED)
    root.update()
    time.sleep(3)
    actions = []  # Reset for new recording
    press_times = {}
    drag_start_pos = None
    overlay = None
    canvas = None
    drag_rect = None
    start_time = time.time()
    sparse_recording = sparse_var.get()
    if sparse_recording:
        last_ts = 0.0
    else:
        last_ts = None
    recording = True
    update_status("Recording... Press Esc to stop.")
    status_label.config(background='red', foreground='white')
    record_btn.config(text="Stop Recording (F3)", command=stop_recording, state=tk.NORMAL)

    kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    if sparse_recording:
        mouse_listener = mouse.Listener(on_move=on_move_sparse, on_click=on_click)
    else:
        mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click)

    kb_listener.start()
    mouse_listener.start()

    listeners['kb'] = kb_listener
    listeners['mouse'] = mouse_listener

def stop_recording():
    global recording, listeners, overlay, canvas, drag_rect
    recording = False
    if 'kb' in listeners:
        listeners['kb'].stop()
        del listeners['kb']
    if 'mouse' in listeners:
        listeners['mouse'].stop()
        del listeners['mouse']
    if overlay:
        destroy_recording_overlay()
    update_status("Recording stopped.")
    status_label.config(background='#f0f0f0', foreground='black')
    record_btn.config(text="Record (F3)", command=start_recording, state=tk.NORMAL)
    start_stop_btn.config(state=tk.NORMAL)
    # Post-process actions
    if actions:
        actions.sort(key=lambda x: x['timestamp'])
        duration_extra = 0.0
        try:
            duration_extra = float(duration_extra_var.get())
            if duration_extra < 0:
                duration_extra = 0.0
        except ValueError:
            pass
        if sparse_recording:
            prev_ts = 0.0
            for action in actions:
                ts = action['timestamp']
                d = ts - prev_ts
                if action['type'] == 'mouse_move':
                    action['min_delay'] = d
                    action['max_delay'] = d + duration_extra
                else:
                    action['min_delay'] = 0.0
                    action['max_delay'] = 0.0
                prev_ts = ts
        else:
            actions[0]['min_delay'] = 0.0
            actions[0]['max_delay'] = 0.0
            prev_ts = actions[0]['timestamp']
            for i in range(1, len(actions)):
                delay = actions[i]['timestamp'] - prev_ts
                actions[i]['min_delay'] = delay
                actions[i]['max_delay'] = delay
                prev_ts = actions[i]['timestamp']
        # Remove timestamp
        for action in actions:
            del action['timestamp']
    update_tree()

def new_macro():
    global actions, current_filename
    if actions and messagebox.askyesno("Unsaved Changes", "Create new will clear current actions. Save first?"):
        save_macro()
    actions = []
    current_filename = None
    update_tree()
    update_status("New macro created.")

def load_macro():
    global actions, current_filename
    if actions and messagebox.askyesno("Unsaved Changes", "Load will overwrite current actions. Save first?"):
        save_macro()
    filename = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
    if not filename:
        return
    try:
        with open(filename, 'r') as f:
            actions = json.load(f)
        current_filename = filename
        update_tree()
        update_status("Macro loaded.")
    except Exception as e:
        messagebox.showerror("Error", f"Failed to load: {e}")

def save_macro():
    global current_filename
    if not actions:
        messagebox.showwarning("No Actions", "No actions to save.")
        return
    filename = filedialog.asksaveasfilename(initialfile=current_filename, defaultextension=".json", filetypes=[("JSON files", "*.json")])
    if filename:
        with open(filename, 'w') as f:
            json.dump(actions, f)
        current_filename = filename
        messagebox.showinfo("Saved", f"Macro saved to {filename}")

def playback_macro():
    global playback_active, playback_thread, pressed_items, repeat_mode, repeat_value
    if not actions:
        messagebox.showwarning("No Actions", "No actions to playback.")
        return
    if recording:
        messagebox.showwarning("Recording Active", "Cannot play back while recording.")
        return
    repeat_mode = mode_var.get()
    try:
        repeat_value = float(repeat_var.get())
        if repeat_value <= 0:
            raise ValueError
    except ValueError:
        messagebox.showerror("Invalid Input", "Repeat value must be a positive number.")
        return
    try:
        speed_perc = float(speed_var.get())
        if speed_perc < 1 or speed_perc > 200:
            raise ValueError
    except ValueError:
        messagebox.showerror("Invalid Speed", "Speed must be between 1 and 200.")
        return
    time_multiplier = 100.0 / speed_perc
    playback_active = True
    pressed_items = []
    update_status("Playback starting in 3 seconds...")
    if not numpy_available:
        update_status("Warning: numpy not installed, mouse movements will be instant.")
    if not pil_available:
        update_status("Warning: PIL not installed, color checks will be skipped.")
    root.update()
    interruptible_sleep(3)
    root.after(0, update_ui_for_playback)

    def run_playback():
        global playback_active
        playback_start = time.time()
        current_pos = mouse_controller.position
        rep = 0
        total_seconds = repeat_value * 60 if repeat_mode == "Minutes" else float('inf')
        min_sec_per_px = 0.00100001
        max_sec_per_px = 0.00200001
        while playback_active:
            loop_stack = []
            i = 0
            while i < len(actions) and playback_active:
                if time.time() - playback_start >= total_seconds:
                    break
                action = actions[i]
                delay = random.uniform(action.get('min_delay', 0.0), action.get('max_delay', 0.0)) * time_multiplier
                if not playback_active:
                    break
                if action['type'] == 'key_action':
                    interruptible_sleep(delay)
                    if not playback_active:
                        break
                    key = action['key']
                    items = []
                    def get_key(kstr):
                        if kstr.startswith('Key.'):
                            return Key.__dict__.get(kstr.split('.')[-1])
                        return kstr if len(kstr) == 1 else None  # Assume single char or Key
                    if key.startswith('mouse.'):
                        button_name = key[6:]
                        button = Button.__dict__.get(button_name)
                        items = [(mouse_controller, button)]
                    elif ' + ' in key:
                        modifier_str, main_key_str = key.split(' + ')
                        modifier = get_key(modifier_str)
                        main_key = get_key(main_key_str)
                        items = [(kb_controller, modifier), (kb_controller, main_key)]
                    else:
                        key_obj = get_key(key)
                        items = [(kb_controller, key_obj)]
                    for ctrl, itm in items:
                        if itm is not None:
                            ctrl.press(itm)
                            pressed_items.append((ctrl, itm))
                    hold_duration = random.uniform(0.001, 0.3) * time_multiplier
                    interruptible_sleep(hold_duration)
                    for ctrl, itm in reversed(items):
                        if itm is not None:
                            ctrl.release(itm)
                        if (ctrl, itm) in pressed_items:
                            pressed_items.remove((ctrl, itm))
                elif action['type'] == 'mouse_move':
                    dest_x = random.uniform(action['min_x'], action['max_x'])
                    dest_y = random.uniform(action['min_y'], action['max_y'])
                    dist = np.hypot(dest_x - current_pos[0], dest_y - current_pos[1])
                    if dist > 0:
                        max_possible_sec_per_px = delay / dist
                        if max_possible_sec_per_px >= min_sec_per_px:
                            low = min_sec_per_px
                            high = min(max_sec_per_px, max_possible_sec_per_px)
                            sec_per_px = random.uniform(low, high)
                            move_time = dist * sec_per_px
                            pause_before = delay - move_time
                        else:
                            move_time = delay
                            pause_before = 0
                        interruptible_sleep(pause_before)
                        if not playback_active:
                            break
                        if numpy_available:
                            human_move(current_pos[0], current_pos[1], dest_x, dest_y, move_time, seed=hash((current_pos, (dest_x, dest_y))))
                        else:
                            mouse_controller.position = (dest_x, dest_y)
                    else:
                        interruptible_sleep(delay)
                    current_pos = (dest_x, dest_y)
                elif action['type'] == 'color_check':
                    interruptible_sleep(delay)
                    if not playback_active:
                        break
                    if not pil_available:
                        update_status("Skipping color check: PIL not available.")
                        i += 1
                        continue
                    actual_color = ImageGrab.grab().getpixel(mouse_controller.position)
                    expected_hex = action['expected_color']
                    expected = tuple(int(expected_hex[j:j+2], 16) for j in (1, 3, 5))
                    if actual_color != expected:
                        playback_active = False
                        root.after(0, lambda: messagebox.showinfo("Color Mismatch", "Color check failed. Playback stopped."))
                        root.after(0, lambda: update_status("Playback stopped due to color mismatch."))
                        root.after(0, update_ui_for_playback)
                        break
                elif action['type'] == 'loop_start':
                    interruptible_sleep(delay)
                    if not playback_active:
                        break
                    loops = random.randint(action.get('min_loops', 1), action.get('max_loops', 1))
                    loop_stack.append({'start': i, 'remaining': loops, 'name': action.get('name', '')})
                elif action['type'] == 'loop_end':
                    interruptible_sleep(delay)
                    if not playback_active:
                        break
                    if not loop_stack or loop_stack[-1]['name'] != action.get('name', ''):
                        playback_active = False
                        root.after(0, lambda: update_status("Mismatched loop names. Playback stopped."))
                        root.after(0, update_ui_for_playback)
                        break
                    current_loop = loop_stack[-1]
                    if current_loop['remaining'] > 1:
                        current_loop['remaining'] -= 1
                        i = current_loop['start'] + 1
                        continue
                    else:
                        loop_stack.pop()
                i += 1
            rep += 1
            if repeat_mode == "Loops" and rep >= repeat_value:
                break
            if time.time() - playback_start >= total_seconds:
                break
        if playback_active:
            playback_active = False
            root.after(0, lambda: messagebox.showinfo("Finished", "Playback finished."))
            root.after(0, lambda: update_status("Ready"))
            root.after(0, update_ui_for_playback)
        pressed_items.clear()

    playback_thread = threading.Thread(target=run_playback)
    playback_thread.daemon = True
    playback_thread.start()

def stop_playback():
    global playback_active, pressed_items
    if playback_active:
        playback_active = False
        for controller, item in pressed_items:
            controller.release(item)
        pressed_items.clear()
        update_status("Playback stopped.")
        if playback_thread and playback_thread.is_alive():
            playback_thread.join(timeout=1.0)
        root.after(0, update_ui_for_playback)

def toggle_playback():
    if playback_active:
        stop_playback()
    else:
        playback_macro()

def update_ui_for_playback():
    if playback_active:
        start_stop_btn.config(text="Stop (F1)", style='RedButton.TButton')
        record_btn.config(state=tk.DISABLED)
        repeat_entry.config(state=tk.DISABLED)
        mode_combo.config(state=tk.DISABLED)
        save_btn.config(state=tk.DISABLED)
    else:
        start_stop_btn.config(text="Start (F1)", style='GreenButton.TButton')
        record_btn.config(state=tk.NORMAL)
        repeat_entry.config(state='normal')
        mode_combo.config(state='readonly')
        save_btn.config(state=tk.NORMAL)

def hotkey_f1():
    try:
        root.after(0, toggle_playback)
    except Exception:
        pass

def hotkey_f3():
    try:
        if not recording:
            root.after(0, start_recording)
        elif recording:
            root.after(0, stop_recording)
    except Exception:
        pass

def insert_action(action_type, after_iid=None):
    new_action = {'type': action_type, 'min_delay': 0.1, 'max_delay': 0.3, 'comment': ''}
    
    if action_type == 'key_action':
        new_action['key'] = 'a'
    elif action_type == 'mouse_move':
        new_action['min_x'] = 0
        new_action['max_x'] = 0
        new_action['min_y'] = 0
        new_action['max_y'] = 0
    elif action_type == 'color_check':
        new_action['expected_color'] = '#ffffff'  # Default white
    elif action_type == 'loop_start':
        new_action['name'] = 'loop1'
        new_action['min_loops'] = 1
        new_action['max_loops'] = 1
    elif action_type == 'loop_end':
        new_action['name'] = 'loop1'
    
    if after_iid is None:
        pos = len(actions)
        actions.append(new_action)
    else:
        pos = int(after_iid) + 1
        actions.insert(pos, new_action)
    update_tree()
    tree.selection_set(str(pos))  # Automatically select the new row
    update_status("Action added. Select to edit.")

def delete_selected():
    selected = tree.selection()
    if not selected:
        return
    indices = sorted([int(sel) for sel in selected], reverse=True)
    for idx in indices:
        del actions[idx]
    update_tree()
    update_status("Action(s) deleted.")
    clear_editor()

def copy_selected():
    global copied_actions
    selected = tree.selection()
    if not selected:
        return
    indices = sorted([int(sel) for sel in selected])
    copied_actions = copy.deepcopy([actions[i] for i in indices])
    update_status("Selected actions copied.")

def paste_after(after_iid):
    global copied_actions
    if not copied_actions:
        return
    pos = int(after_iid) + 1
    for act in copied_actions:
        actions.insert(pos, copy.deepcopy(act))
        pos += 1
    update_tree()
    update_status("Actions pasted.")

def paste_at_end():
    global copied_actions
    if not copied_actions:
        return
    for act in copied_actions:
        actions.append(copy.deepcopy(act))
    update_tree()
    update_status("Actions pasted at end.")

def paste_smart():
    global copied_actions
    if not copied_actions:
        return
    selected = tree.selection()
    if selected:
        last = max([int(s) for s in selected])
        paste_after(str(last))
    else:
        paste_at_end()

def update_status(text):
    status_label.config(text=text)

def hide_preview():
    global preview_overlay, preview_canvas
    if preview_overlay:
        preview_overlay.destroy()
        preview_overlay = None
        preview_canvas = None

def show_preview(min_x, max_x, min_y, max_y):
    global preview_overlay, preview_canvas
    hide_preview()
    trans_color = '#ab23ff'  # Unique transparent color
    preview_overlay = tk.Toplevel(root)
    preview_overlay.overrideredirect(True)
    preview_overlay.attributes('-topmost', True)
    preview_overlay.attributes('-transparentcolor', trans_color)
    w = root.winfo_screenwidth()
    h = root.winfo_screenheight()
    preview_overlay.geometry(f"{w}x{h}+0+0")
    preview_canvas = tk.Canvas(preview_overlay, bg=trans_color, highlightthickness=0)
    preview_canvas.pack(fill=tk.BOTH, expand=True)

    # Draw the zone
    if min_x == max_x and min_y == max_y:
        # Draw a cross for single point
        half = 10
        preview_canvas.create_line(min_x - half, min_y, min_x + half, min_y, fill='red', width=2)
        preview_canvas.create_line(min_x, min_y - half, min_x, min_y + half, fill='red', width=2)
    else:
        preview_canvas.create_rectangle(min_x, min_y, max_x, max_y, outline='red', width=2)

    preview_canvas.bind("<Button-1>", lambda e: hide_preview())

def on_tree_select(event):
    editor_labelframe.pack_forget()
    hide_preview()
    selected = tree.selection()
    if len(selected) == 1:
        global selected_idx
        selected_idx = int(selected[0])
        populate_editor(actions[selected_idx])
        editor_labelframe.pack(pady=10, padx=10, fill=tk.X)
    else:
        clear_editor()

def populate_editor(action):
    min_delay_var.set(f"{action.get('min_delay', 0.0):.3f}")
    max_delay_var.set(f"{action.get('max_delay', 0.0):.3f}")
    type_combo.set(action['type'])
    min_delay_entry.config(state='normal')
    max_delay_entry.config(state='normal')
    type_combo.config(state='readonly')

    key_var.set(action.get('key', ''))
    min_x_var.set(str(action.get('min_x', 0)))
    max_x_var.set(str(action.get('max_x', 0)))
    min_y_var.set(str(action.get('min_y', 0)))
    max_y_var.set(str(action.get('max_y', 0)))
    hex_var.set(action.get('expected_color', '#ffffff'))
    check_x_var.set('')
    check_y_var.set('')
    loop_name_var.set(action.get('name', ''))
    min_loops_var.set(str(action.get('min_loops', 1)))
    max_loops_var.set(str(action.get('max_loops', 1)))
    comment_var.set(action.get('comment', ''))

    # Hide all type-specific widgets first
    key_label.grid_remove()
    key_entry.grid_remove()
    capture_btn.grid_remove()
    min_x_label.grid_remove()
    min_x_entry.grid_remove()
    max_x_label.grid_remove()
    max_x_entry.grid_remove()
    min_y_label.grid_remove()
    min_y_entry.grid_remove()
    max_y_label.grid_remove()
    max_y_entry.grid_remove()
    capture_zone_btn.grid_remove()
    hex_label.grid_remove()
    hex_entry.grid_remove()
    capture_on_click_btn.grid_remove()
    check_x_label.grid_remove()
    check_x_entry.grid_remove()
    check_y_label.grid_remove()
    check_y_entry.grid_remove()
    capture_at_coord_btn.grid_remove()
    loop_name_label.grid_remove()
    loop_name_entry.grid_remove()
    min_loops_label.grid_remove()
    min_loops_entry.grid_remove()
    max_loops_label.grid_remove()
    max_loops_entry.grid_remove()

    if action['type'] == 'key_action':
        key_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        key_entry.grid(row=2, column=1, columnspan=3, padx=5, pady=5, sticky=tk.W)
        capture_btn.grid(row=2, column=4, padx=5, pady=5)
        key_entry.config(state='normal')
        capture_btn.config(state='normal')
    elif action['type'] == 'mouse_move':
        min_x_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        min_x_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)
        max_x_label.grid(row=2, column=2, padx=5, pady=5, sticky=tk.E)
        max_x_entry.grid(row=2, column=3, padx=5, pady=5, sticky=tk.W)
        min_y_label.grid(row=3, column=0, padx=5, pady=5, sticky=tk.E)
        min_y_entry.grid(row=3, column=1, padx=5, pady=5, sticky=tk.W)
        max_y_label.grid(row=3, column=2, padx=5, pady=5, sticky=tk.E)
        max_y_entry.grid(row=3, column=3, padx=5, pady=5, sticky=tk.W)
        capture_zone_btn.grid(row=3, column=4, padx=5, pady=5)
        min_x_entry.config(state='normal')
        max_x_entry.config(state='normal')
        min_y_entry.config(state='normal')
        max_y_entry.config(state='normal')
        capture_zone_btn.config(state='normal')
        show_preview(action['min_x'], action['max_x'], action['min_y'], action['max_y'])
        update_status("Previewing mouse zone. Click on the preview to close.")
    elif action['type'] == 'color_check':
        hex_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        hex_entry.grid(row=2, column=1, columnspan=3, padx=5, pady=5, sticky=tk.W)
        capture_on_click_btn.grid(row=2, column=4, padx=5, pady=5)
        check_x_label.grid(row=3, column=0, padx=5, pady=5, sticky=tk.E)
        check_x_entry.grid(row=3, column=1, padx=5, pady=5, sticky=tk.W)
        check_y_label.grid(row=3, column=2, padx=5, pady=5, sticky=tk.E)
        check_y_entry.grid(row=3, column=3, padx=5, pady=5, sticky=tk.W)
        capture_at_coord_btn.grid(row=3, column=4, padx=5, pady=5)
        hex_entry.config(state='normal')
        capture_on_click_btn.config(state='normal')
        check_x_entry.config(state='normal')
        check_y_entry.config(state='normal')
        capture_at_coord_btn.config(state='normal')
    elif action['type'] == 'loop_start':
        loop_name_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        loop_name_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)
        min_loops_label.grid(row=2, column=2, padx=5, pady=5, sticky=tk.E)
        min_loops_entry.grid(row=2, column=3, padx=5, pady=5, sticky=tk.W)
        max_loops_label.grid(row=2, column=4, padx=5, pady=5, sticky=tk.E)
        max_loops_entry.grid(row=2, column=5, padx=5, pady=5, sticky=tk.W)
        loop_name_entry.config(state='normal')
        min_loops_entry.config(state='normal')
        max_loops_entry.config(state='normal')
    elif action['type'] == 'loop_end':
        loop_name_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        loop_name_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)
        loop_name_entry.config(state='normal')
    
    # Show comment field (common to all types)
    comment_label.grid(row=5, column=0, padx=5, pady=5, sticky=tk.E)
    comment_entry.grid(row=5, column=1, columnspan=4, padx=5, pady=5, sticky=tk.W)
    comment_entry.config(state='normal')
    
    save_btn.config(state='normal')

def clear_editor():
    min_delay_var.set('')
    max_delay_var.set('')
    type_combo.set('')
    key_var.set('')
    min_x_var.set('')
    max_x_var.set('')
    min_y_var.set('')
    max_y_var.set('')
    hex_var.set('')
    check_x_var.set('')
    check_y_var.set('')
    loop_name_var.set('')
    min_loops_var.set('')
    max_loops_var.set('')
    comment_var.set('')
    min_delay_entry.config(state='disabled')
    max_delay_entry.config(state='disabled')
    type_combo.config(state='disabled')
    key_entry.config(state='disabled')
    min_x_entry.config(state='disabled')
    max_x_entry.config(state='disabled')
    min_y_entry.config(state='disabled')
    max_y_entry.config(state='disabled')
    hex_entry.config(state='disabled')
    check_x_entry.config(state='disabled')
    check_y_entry.config(state='disabled')
    loop_name_entry.config(state='disabled')
    min_loops_entry.config(state='disabled')
    max_loops_entry.config(state='disabled')
    comment_entry.config(state='disabled')
    capture_btn.config(state='disabled')
    capture_zone_btn.config(state='disabled')
    capture_on_click_btn.config(state='disabled')
    capture_at_coord_btn.config(state='disabled')
    save_btn.config(state='disabled')
    # Hide type-specific widgets
    key_label.grid_remove()
    key_entry.grid_remove()
    capture_btn.grid_remove()
    min_x_label.grid_remove()
    min_x_entry.grid_remove()
    max_x_label.grid_remove()
    max_x_entry.grid_remove()
    min_y_label.grid_remove()
    min_y_entry.grid_remove()
    max_y_label.grid_remove()
    max_y_entry.grid_remove()
    capture_zone_btn.grid_remove()
    hex_label.grid_remove()
    hex_entry.grid_remove()
    capture_on_click_btn.grid_remove()
    check_x_label.grid_remove()
    check_x_entry.grid_remove()
    check_y_label.grid_remove()
    check_y_entry.grid_remove()
    capture_at_coord_btn.grid_remove()
    loop_name_label.grid_remove()
    loop_name_entry.grid_remove()
    min_loops_label.grid_remove()
    min_loops_entry.grid_remove()
    max_loops_label.grid_remove()
    max_loops_entry.grid_remove()
    comment_label.grid_remove()
    comment_entry.grid_remove()
    hide_preview()

def on_type_change(event):
    action = actions[selected_idx]
    new_type = type_combo.get()
    if new_type != action['type']:
        action['type'] = new_type
        if new_type == 'key_action':
            action['key'] = 'a'
            keys_to_del = ['min_x', 'max_x', 'min_y', 'max_y', 'expected_color', 'name', 'min_loops', 'max_loops']
            for k in keys_to_del:
                if k in action:
                    del action[k]
        elif new_type == 'mouse_move':
            action['min_x'] = 0
            action['max_x'] = 0
            action['min_y'] = 0
            action['max_y'] = 0
            keys_to_del = ['key', 'expected_color', 'name', 'min_loops', 'max_loops']
            for k in keys_to_del:
                if k in action:
                    del action[k]
        elif new_type == 'color_check':
            action['expected_color'] = '#ffffff'
            keys_to_del = ['key', 'min_x', 'max_x', 'min_y', 'max_y', 'name', 'min_loops', 'max_loops']
            for k in keys_to_del:
                if k in action:
                    del action[k]
        elif new_type == 'loop_start':
            action['name'] = 'loop1'
            action['min_loops'] = 1
            action['max_loops'] = 1
            keys_to_del = ['key', 'min_x', 'max_x', 'min_y', 'max_y', 'expected_color']
            for k in keys_to_del:
                if k in action:
                    del action[k]
        elif new_type == 'loop_end':
            action['name'] = 'loop1'
            keys_to_del = ['key', 'min_x', 'max_x', 'min_y', 'max_y', 'expected_color', 'min_loops', 'max_loops']
            for k in keys_to_del:
                if k in action:
                    del action[k]
        populate_editor(action)
        update_tree()

def save_changes():
    action = actions[selected_idx]
    try:
        min_delay = float(min_delay_var.get())
        max_delay = float(max_delay_var.get())
        if min_delay < 0 or max_delay < 0 or min_delay > max_delay:
            raise ValueError("Min delay must be <= max delay and both non-negative.")
        action['min_delay'] = min_delay
        action['max_delay'] = max_delay
        if action['type'] == 'mouse_move':
            min_x = int(min_x_var.get())
            max_x = int(max_x_var.get())
            min_y = int(min_y_var.get())
            max_y = int(max_y_var.get())
            if min_x > max_x or min_y > max_y:
                raise ValueError("Min X/Y must be <= Max X/Y.")
            action['min_x'] = min_x
            action['max_x'] = max_x
            action['min_y'] = min_y
            action['max_y'] = max_y
        elif action['type'] == 'key_action':
            action['key'] = key_var.get().strip()
            if not action['key']:
                raise ValueError("Key cannot be empty.")
        elif action['type'] == 'color_check':
            expected_color = hex_var.get().strip()
            if not expected_color.startswith('#') or len(expected_color) != 7:
                raise ValueError("Invalid hex color format. Use #RRGGBB.")
            try:
                int(expected_color[1:], 16)
            except ValueError:
                raise ValueError("Invalid hex color value.")
            action['expected_color'] = expected_color
        elif action['type'] == 'loop_start':
            name = loop_name_var.get().strip()
            if not name:
                raise ValueError("Loop name cannot be empty.")
            min_loops = int(min_loops_var.get())
            max_loops = int(max_loops_var.get())
            if min_loops < 1 or max_loops < 1 or min_loops > max_loops:
                raise ValueError("Min loops must be <= max loops and both at least 1.")
            action['name'] = name
            action['min_loops'] = min_loops
            action['max_loops'] = max_loops
        elif action['type'] == 'loop_end':
            name = loop_name_var.get().strip()
            if not name:
                raise ValueError("Loop name cannot be empty.")
            action['name'] = name
        action['comment'] = comment_var.get().strip()
    except ValueError as e:
        messagebox.showerror("Invalid Input", str(e) or "Invalid values entered.")
        return
    update_tree()
    tree.selection_set(str(selected_idx))
    if action['type'] == 'mouse_move':
        hide_preview()
        show_preview(action['min_x'], action['max_x'], action['min_y'], action['max_y'])
    update_status("Changes saved.")

def capture_input():
    modifier = None
    def on_capture_press(key):
        nonlocal modifier
        key_str = str(key).replace("'", "") if hasattr(key, 'char') else str(key)
        if key_str in ['Key.shift', 'Key.ctrl', 'Key.alt']:
            modifier = key_str
            root.after(3000, stop_capture)
            update_status("Modifier captured. Press another key within 3 seconds...")
            return
        else:
            if modifier:
                key_var.set(f"{modifier} + {key_str}")
            else:
                key_var.set(key_str)
            update_status("Input captured.")
            stop_capture()
            return False

    def on_capture_release(key):
        stop_capture()
        return False

    def on_capture_click(x, y, button, pressed):
        if pressed:
            key_var.set(f"mouse.{str(button).split('.')[-1]}")
            update_status("Mouse input captured.")
            stop_capture()
        return False

    def stop_capture():
        global capture_listener_kb, capture_listener_mouse
        if capture_listener_kb:
            capture_listener_kb.stop()
            capture_listener_kb = None
        if capture_listener_mouse:
            capture_listener_mouse.stop()
            capture_listener_mouse = None

    global capture_listener_kb, capture_listener_mouse
    stop_capture()
    capture_listener_kb = keyboard.Listener(on_press=on_capture_press, on_release=on_capture_release)
    capture_listener_mouse = mouse.Listener(on_click=on_capture_click)
    capture_listener_kb.start()
    capture_listener_mouse.start()
    update_status("Press a key or mouse button to capture...")

def capture_zone():
    global overlay, canvas
    start_pos = [None]
    drag_rect = [None]

    def on_capture_click(x, y, button, pressed):
        if button == Button.left:
            if pressed:
                start_pos[0] = (x, y)
                drag_rect[0] = None
                update_status("Drag to select zone... Release to finish.")
            else:
                if start_pos[0]:
                    end_x, end_y = x, y
                    sx, sy = start_pos[0]
                    min_x_var.set(str(min(sx, end_x)))
                    max_x_var.set(str(max(sx, end_x)))
                    min_y_var.set(str(min(sy, end_y)))
                    max_y_var.set(str(max(sy, end_y)))
                    update_status("Mouse zone captured.")
                    stop_capture()
                    return False
        return True

    def on_capture_move(x, y):
        if start_pos[0] and canvas:  # Only draw if canvas exists (i.e., block on)
            if drag_rect[0]:
                canvas.delete(drag_rect[0])
            sx, sy = start_pos[0]
            drag_rect[0] = canvas.create_rectangle(sx, sy, x, y, outline='red', width=2)

    def stop_capture():
        global capture_listener_mouse, overlay, canvas
        if capture_listener_mouse:
            capture_listener_mouse.stop()
            capture_listener_mouse = None
        if overlay:
            overlay.destroy()
            overlay = None
            canvas = None

    stop_capture()
    update_status("Hold left mouse and drag to select zone in 3 seconds...")
    root.update()
    time.sleep(3)
    # Only create overlay if block is on (dim with feedback); else, no overlay/no feedback
    if block_underlying_var.get():
        overlay = tk.Toplevel(root)
        overlay.overrideredirect(True)
        overlay.attributes('-topmost', True)
        overlay.attributes('-alpha', 0.3)
        w = root.winfo_screenwidth()
        h = root.winfo_screenheight()
        overlay.geometry(f"{w}x{h}+0+0")
        canvas = tk.Canvas(overlay, bg='black', highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
    else:
        overlay = None
        canvas = None
    capture_listener_mouse = mouse.Listener(on_click=on_capture_click, on_move=on_capture_move)
    capture_listener_mouse.start()
    update_status("Hold left mouse and drag to select zone...")

def capture_color_on_click():
    if not pil_available:
        messagebox.showwarning("PIL not available", "Cannot capture color without PIL.")
        return
    global capture_listener_mouse
    if capture_listener_mouse:
        capture_listener_mouse.stop()
    update_status("In 3 seconds, click on the screen to capture color...")
    root.update()
    time.sleep(3)
    def on_click(x, y, button, pressed):
        if pressed and button == Button.left:
            color = ImageGrab.grab().getpixel((x, y))
            hex_color = f'#{color[0]:02x}{color[1]:02x}{color[2]:02x}'
            hex_var.set(hex_color)
            update_status("Color captured.")
            capture_listener_mouse.stop()
            return False
    capture_listener_mouse = mouse.Listener(on_click=on_click)
    capture_listener_mouse.start()

def capture_color_at_coord():
    if not pil_available:
        messagebox.showwarning("PIL not available", "Cannot capture color without PIL.")
        return
    try:
        x = int(check_x_var.get())
        y = int(check_y_var.get())
    except ValueError:
        messagebox.showerror("Invalid Input", "Invalid coordinates.")
        return
    update_status(f"Capturing color at ({x}, {y}) in 3 seconds...")
    root.update()
    time.sleep(3)
    color = ImageGrab.grab().getpixel((x, y))
    hex_color = f'#{color[0]:02x}{color[1]:02x}{color[2]:02x}'
    hex_var.set(hex_color)
    update_status("Color captured.")

def show_menu(event):
    menu.delete(0, tk.END)
    row = tree.identify_row(event.y)
    if row:
        if row not in tree.selection():
            tree.selection_set(row)
        selected = tree.selection()
        add_menu = tk.Menu(menu, tearoff=0)
        for act_type in ACTION_TYPES:
            add_menu.add_command(label=act_type, command=lambda t=act_type: insert_action(t, after_iid=row))
        menu.add_cascade(label="Insert Below", menu=add_menu)
        menu.add_command(label="Delete", command=delete_selected)
        menu.add_separator()
        if selected:
            menu.add_command(label="Copy Selected", command=copy_selected)
        if copied_actions:
            menu.add_command(label="Paste After", command=lambda: paste_after(row))
    else:
        add_menu = tk.Menu(menu, tearoff=0)
        for act_type in ACTION_TYPES:
            add_menu.add_command(label=act_type, command=lambda t=act_type: insert_action(t, after_iid=None))
        menu.add_cascade(label="Add Action", menu=add_menu)
        if copied_actions:
            menu.add_separator()
            menu.add_command(label="Paste at End", command=paste_at_end)
    menu.post(event.x_root, event.y_root)

def on_b1_motion(event):
    global drag_initiated, prev_target
    if not drag_initiated:
        if abs(event.y - press_y) > 5:
            drag_initiated = True
            row = potential_source
            tree.selection_set(row)
            drag_data["source"] = row
            tree.config(cursor="exchange")
            update_status("Dragging row...")
    if drag_initiated:
        target = tree.identify_row(event.y)
        if target != prev_target:
            if prev_target:
                current_tags = tree.item(prev_target, "tags")
                current_tags = tuple(t for t in current_tags if t != "drop_target")
                tree.item(prev_target, tags=current_tags)
            if target and target != drag_data["source"]:
                current_tags = tree.item(target, "tags")
                tree.item(target, tags=current_tags + ("drop_target",))
            prev_target = target
        if target:
            bbox = tree.bbox(target)
            if bbox:
                half = bbox[3] / 2
                rel_y = event.y - bbox[1]
                position = "after" if rel_y > half else "before"
                update_status(f"Will insert {position} this row")
        else:
            update_status("Will append to end")

def on_button_press(event):
    global potential_source, drag_initiated, press_y
    row = tree.identify_row(event.y)
    if row:
        potential_source = row
        drag_initiated = False
        press_y = event.y
        tree.bind("<B1-Motion>", on_b1_motion)

def on_button_release(event):
    global prev_target
    tree.unbind("<B1-Motion>")
    tree.config(cursor="")
    if prev_target:
        current_tags = tree.item(prev_target, "tags")
        current_tags = tuple(t for t in current_tags if t != "drop_target")
        tree.item(prev_target, tags=current_tags)
        prev_target = None
    if drag_initiated:
        source = drag_data["source"]
        drag_data["source"] = None
        target = tree.identify_row(event.y)
        if target == source or not source:
            update_status("Ready")
            return
        insert_after = True
        target_idx = None
        if target:
            target_idx = int(target)
            bbox = tree.bbox(target)
            if bbox:
                half = bbox[3] / 2
                rel_y = event.y - bbox[1]
                insert_after = rel_y > half
        else:
            action = actions.pop(int(source))
            actions.append(action)
            new_pos = len(actions) - 1
            update_tree()
            tree.selection_set(str(new_pos))
            update_status("Action moved.")
            return
        source_idx = int(source)
        action = actions.pop(source_idx)
        if source_idx < target_idx:
            target_idx -= 1
        insert_pos = target_idx + 1 if insert_after else target_idx
        actions.insert(insert_pos, action)
        new_pos = insert_pos
        update_tree()
        tree.selection_set(str(new_pos))
        update_status("Action moved.")
    else:
        update_status("Ready")

# GUI setup
root = tk.Tk()
root.title("Advanced Macro Recorder")
root.geometry("900x700")
root.configure(bg="#f0f0f0")  # Light background for modern look

# Modern style
style = ttk.Style()
style.theme_use('clam')
style.configure("Treeview", font=("Arial", 10), rowheight=25, background="#ffffff", foreground="#000000", fieldbackground="#ffffff")
style.configure("Treeview.Heading", font=("Arial", 12, "bold"), background="#4a90e2", foreground="#ffffff")
style.map("Treeview", background=[('selected', '#4a90e2')], foreground=[('selected', '#ffffff')])
style.configure("Horizontal.TProgressbar", background='#4a90e2')
style.configure('TButton', font=('Arial', 12), padding=10)
style.configure('TLabel', font=('Arial', 12), background='#f0f0f0')
style.configure('TEntry', font=('Arial', 12))
style.configure('TCombobox', font=('Arial', 12))
style.configure('GreenButton.TButton', background='#27ae60', foreground='white')
style.configure('RedButton.TButton', background='#e74c3c', foreground='white')

# Menu bar for better UX
menubar = Menu(root)
file_menu = Menu(menubar, tearoff=0)
file_menu.add_command(label="New Macro", command=new_macro, accelerator="Ctrl+N")
file_menu.add_command(label="Load Macro", command=load_macro, accelerator="Ctrl+O")
file_menu.add_command(label="Save Macro", command=save_macro, accelerator="Ctrl+S")
menubar.add_cascade(label="File", menu=file_menu)
edit_menu = Menu(menubar, tearoff=0)
edit_menu.add_command(label="Copy", command=copy_selected, accelerator="Ctrl+C")
edit_menu.add_command(label="Paste", command=paste_smart, accelerator="Ctrl+V")
edit_menu.add_command(label="Delete", command=delete_selected, accelerator="Del")
menubar.add_cascade(label="Edit", menu=edit_menu)
root.config(menu=menubar)

# Bind keyboard shortcuts
root.bind("<Control-n>", lambda e: new_macro())
root.bind("<Control-o>", lambda e: load_macro())
root.bind("<Control-s>", lambda e: save_macro())
root.bind("<Control-c>", lambda e: copy_selected())
root.bind("<Control-v>", lambda e: paste_smart())
root.bind("<Delete>", lambda e: delete_selected())

# Status label
status_label = ttk.Label(root, text="Ready")
status_label.pack(pady=10, fill=tk.X)

# Tooltip label at the bottom (fixed position)
tooltip_var = tk.StringVar(value="")
tooltip_label = ttk.Label(root, textvariable=tooltip_var, relief="sunken", anchor="w", padding=5, background="#ffffe0", font=("Arial", 10))
tooltip_label.pack(side="bottom", fill=tk.X)

# Frame for buttons and repeat
button_frame = ttk.Frame(root, padding=10)
button_frame.pack(fill=tk.X)

record_btn = ttk.Button(button_frame, text="Record (F3)", command=start_recording)
record_btn.grid(row=0, column=0, padx=5)
Tooltip(record_btn, "Start recording mouse and keyboard actions. Press Esc to stop.")

sparse_var = tk.BooleanVar(value=False)
sparse_check = ttk.Checkbutton(button_frame, text="Sparse Recording", variable=sparse_var)
sparse_check.grid(row=0, column=3, padx=5)
Tooltip(sparse_check, "Optional: Only record mouse positions at clicks, setting move duration to time between clicks. Dragging while clicking defines a random zone for the click location with visual red outline feedback. Use Dur Extra to add randomness to max duration.")

block_underlying_var = tk.BooleanVar(value=True)
block_check = ttk.Checkbutton(button_frame, text="Block During Capture", variable=block_underlying_var)
block_check.grid(row=0, column=4, padx=5)
Tooltip(block_check, "Use a dim overlay during zone capture for visual feedback and to prevent unintended interactions with underlying applications.")

start_stop_btn = ttk.Button(button_frame, text="Start (F1)", command=toggle_playback, style='GreenButton.TButton')
start_stop_btn.grid(row=0, column=5, padx=5)
Tooltip(start_stop_btn, "Start the macro. Click or press F1 to stop when running.")

repeat_label = ttk.Label(button_frame, text="Repeat:")
repeat_label.grid(row=0, column=6, padx=5)
mode_var = tk.StringVar(value="Loops")
mode_combo = ttk.Combobox(button_frame, values=["Loops", "Minutes"], state='readonly', textvariable=mode_var, width=10)
mode_combo.grid(row=0, column=7, padx=5)
Tooltip(mode_combo, "Repeat mode: number of loops or total minutes to run.")
repeat_var = tk.StringVar(value="1")
repeat_entry = ttk.Entry(button_frame, textvariable=repeat_var, width=5)
repeat_entry.grid(row=0, column=8, padx=5)
Tooltip(repeat_entry, "Repeat value: number of loops or minutes depending on mode.")

duration_extra_label = ttk.Label(button_frame, text="Dur Extra (s):")
duration_extra_label.grid(row=0, column=1, padx=5)
duration_extra_var = tk.StringVar(value="0.0")
duration_extra_entry = ttk.Entry(button_frame, textvariable=duration_extra_var, width=5)
duration_extra_entry.grid(row=0, column=2, padx=5)
Tooltip(duration_extra_entry, "Extra duration added to max move duration in sparse recording (min = recorded duration, max = recorded + extra).")

click_radius_label = ttk.Label(button_frame, text="Click Radius (px):")
click_radius_label.grid(row=0, column=9, padx=5)
click_radius_var = tk.StringVar(value="0")
click_radius_entry = ttk.Entry(button_frame, textvariable=click_radius_var, width=5)
click_radius_entry.grid(row=0, column=10, padx=5)
Tooltip(click_radius_entry, "In sparse recording, for clicks without drag (or small jiggle), expand the mouse zone by this radius in all directions to create a square random area.")

speed_label = ttk.Label(button_frame, text="Speed (%):")
speed_label.grid(row=0, column=11, padx=5)
speed_var = tk.StringVar(value="100")
speed_spin = Spinbox(button_frame, from_=1, to=200, textvariable=speed_var, width=5)
speed_spin.grid(row=0, column=12, padx=5)
Tooltip(speed_spin, "Playback speed percentage: 100% normal, >100% faster, <100% slower.")

# Treeview for displaying actions
columns = ("delay", "type", "details", "comment")
tree = ttk.Treeview(root, columns=columns, show="headings", height=15, selectmode="extended")
tree.heading("delay", text="Delay Range (s)")
tree.heading("type", text="Action Type")
tree.heading("details", text="Details")
tree.heading("comment", text="Comment")
tree.column("delay", width=150)
tree.column("type", width=150)
tree.column("details", width=300)
tree.column("comment", width=200)
tree.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
tree.tag_configure('even', background='#f4f4f4')
tree.tag_configure('odd', background='#ffffff')
tree.tag_configure('drop_target', background='lightblue')
Tooltip(tree, "List of actions. Right-click to add/insert/delete. Drag to reorder. Select to edit.")

# Scrollbar for treeview
scrollbar = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
tree.configure(yscroll=scrollbar.set)
scrollbar.pack(side="right", fill="y")

# Editor frame
editor_labelframe = ttk.LabelFrame(root, text="Edit Selected Action", padding=10)
# Note: We pack this dynamically in on_tree_select, so no initial pack here

editor_frame = ttk.Frame(editor_labelframe)
editor_frame.pack(fill=tk.X)

# Variables
min_delay_var = tk.StringVar()
max_delay_var = tk.StringVar()
key_var = tk.StringVar()
min_x_var = tk.StringVar()
max_x_var = tk.StringVar()
min_y_var = tk.StringVar()
max_y_var = tk.StringVar()
hex_var = tk.StringVar()
check_x_var = tk.StringVar()
check_y_var = tk.StringVar()
loop_name_var = tk.StringVar()
min_loops_var = tk.StringVar()
max_loops_var = tk.StringVar()
comment_var = tk.StringVar()

# Fields with tooltips (grid only common ones initially; type-specific gridded in populate_editor)
min_delay_label = ttk.Label(editor_frame, text="Min Delay (s):")
min_delay_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.E)
min_delay_entry = ttk.Entry(editor_frame, textvariable=min_delay_var, state='disabled', width=15)
min_delay_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
Tooltip(min_delay_entry, "Minimum time delay before this action starts.")

max_delay_label = ttk.Label(editor_frame, text="Max Delay (s):")
max_delay_label.grid(row=0, column=2, padx=5, pady=5, sticky=tk.E)
max_delay_entry = ttk.Entry(editor_frame, textvariable=max_delay_var, state='disabled', width=15)
max_delay_entry.grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)
Tooltip(max_delay_entry, "Maximum time delay before this action starts (randomized between min and max).")

type_label = ttk.Label(editor_frame, text="Action Type:")
type_label.grid(row=1, column=0, padx=5, pady=5, sticky=tk.E)
type_combo = ttk.Combobox(editor_frame, values=ACTION_TYPES, state='disabled', width=15)
type_combo.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
type_combo.bind("<<ComboboxSelected>>", on_type_change)
Tooltip(type_combo, "Type of action: key press, mouse movement, color check, loop start, or loop end.")

# Type-specific widgets (created but not gridded yet)
key_label = ttk.Label(editor_frame, text="Key:")
key_entry = ttk.Entry(editor_frame, textvariable=key_var, state='disabled', width=15)
Tooltip(key_entry, "The key or button for this action.")
capture_btn = ttk.Button(editor_frame, text="Capture Input", command=capture_input, state='disabled')
Tooltip(capture_btn, "Capture a key or mouse button press.")

min_x_label = ttk.Label(editor_frame, text="Min X:")
min_x_entry = ttk.Entry(editor_frame, textvariable=min_x_var, state='disabled', width=15)
Tooltip(min_x_entry, "Minimum X coordinate for mouse zone.")

max_x_label = ttk.Label(editor_frame, text="Max X:")
max_x_entry = ttk.Entry(editor_frame, textvariable=max_x_var, state='disabled', width=15)
Tooltip(max_x_entry, "Maximum X coordinate for mouse zone.")

min_y_label = ttk.Label(editor_frame, text="Min Y:")
min_y_entry = ttk.Entry(editor_frame, textvariable=min_y_var, state='disabled', width=15)
Tooltip(min_y_entry, "Minimum Y coordinate for mouse zone.")

max_y_label = ttk.Label(editor_frame, text="Max Y:")
max_y_entry = ttk.Entry(editor_frame, textvariable=max_y_var, state='disabled', width=15)
Tooltip(max_y_entry, "Maximum Y coordinate for mouse zone.")

capture_zone_btn = ttk.Button(editor_frame, text="Capture Zone", command=capture_zone, state='disabled')
Tooltip(capture_zone_btn, "Capture a mouse zone by dragging.")

hex_label = ttk.Label(editor_frame, text="Hex Color:")
hex_entry = ttk.Entry(editor_frame, textvariable=hex_var, state='disabled', width=15)
Tooltip(hex_entry, "Expected color in hex format (#RRGGBB).")

capture_on_click_btn = ttk.Button(editor_frame, text="Capture on Click", command=capture_color_on_click, state='disabled')
Tooltip(capture_on_click_btn, "After 3s delay, click to capture color at clicked position.")

check_x_label = ttk.Label(editor_frame, text="X:")
check_x_entry = ttk.Entry(editor_frame, textvariable=check_x_var, state='disabled', width=10)
Tooltip(check_x_entry, "X coordinate for capture (optional).")

check_y_label = ttk.Label(editor_frame, text="Y:")
check_y_entry = ttk.Entry(editor_frame, textvariable=check_y_var, state='disabled', width=10)
Tooltip(check_y_entry, "Y coordinate for capture (optional).")

capture_at_coord_btn = ttk.Button(editor_frame, text="Capture at Coord", command=capture_color_at_coord, state='disabled')
Tooltip(capture_at_coord_btn, "After 3s delay, capture color at specified coordinates.")

loop_name_label = ttk.Label(editor_frame, text="Loop Name:")
loop_name_entry = ttk.Entry(editor_frame, textvariable=loop_name_var, state='disabled', width=15)
Tooltip(loop_name_entry, "Name of the loop for matching start and end.")

min_loops_label = ttk.Label(editor_frame, text="Min Loops:")
min_loops_entry = ttk.Entry(editor_frame, textvariable=min_loops_var, state='disabled', width=10)
Tooltip(min_loops_entry, "Minimum number of loop iterations.")

max_loops_label = ttk.Label(editor_frame, text="Max Loops:")
max_loops_entry = ttk.Entry(editor_frame, textvariable=max_loops_var, state='disabled', width=10)
Tooltip(max_loops_entry, "Maximum number of loop iterations (random between min and max).")

comment_label = ttk.Label(editor_frame, text="Comment:")
comment_entry = ttk.Entry(editor_frame, textvariable=comment_var, state='disabled', width=50)
Tooltip(comment_entry, "Optional comment to describe what this action does.")

save_btn = ttk.Button(editor_frame, text="Save Changes", command=save_changes, state='disabled')
save_btn.grid(row=6, column=0, columnspan=5, pady=10)  # Always gridded, but state disabled when not needed
Tooltip(save_btn, "Save edits to the selected action.")

# Bindings
tree.bind("<<TreeviewSelect>>", on_tree_select)
menu = Menu(root, tearoff=0)
tree.bind("<Button-3>", show_menu)
tree.bind("<Button-1>", on_button_press)
tree.bind("<ButtonRelease-1>", on_button_release)

# Start global hotkey listener
hotkey_listener = GlobalHotKeys({
    '<f1>': hotkey_f1,
    '<f3>': hotkey_f3,
})
hotkey_listener.start()

# Handle window close
def on_closing():
    global hotkey_listener
    if recording:
        stop_recording()
    if playback_active:
        stop_playback()
    if hotkey_listener:
        hotkey_listener.stop()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_closing)

if __name__ == "__main__":
    clear_editor()  # Initial clear
    root.mainloop()