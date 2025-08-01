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

# Attempt to import numpy, set flag if unavailable
try:
    import numpy as np
    numpy_available = True
except ImportError:
    numpy_available = False

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
    start = time.time()
    while time.time() - start < duration and playback_active:
        time.sleep(0.001)  # Smaller sleep for more precise interruption and less CPU usage

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

# Controllers
kb_controller = KeyboardController()
mouse_controller = MouseController()

# Action types
ACTION_TYPES = ['key_action', 'mouse_move']

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
        tag = 'even' if idx % 2 == 0 else 'odd'
        tree.insert("", tk.END, iid=str(idx), values=(delay_str, action['type'], details), tags=(tag,))

def get_action_details(action):
    if action['type'] == 'key_action':
        key = action.get('key', '')
        if key.startswith('mouse.'):
            button = key[6:].capitalize()
            return f"Click Mouse Button: {button}"
        else:
            return f"Press Key: {key}"
    elif action['type'] == 'mouse_move':
        min_dur = action.get('min_move_duration', 0.0)
        max_dur = action.get('max_move_duration', 0.0)
        dur_str = f"{min_dur:.3f} - {max_dur:.3f}"
        return f"Position: ({action.get('x', 0)}, {action.get('y', 0)}), Duration: {dur_str}s"
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
    global recording
    if recording:
        key_str = str(key).replace("'", "") if hasattr(key, 'char') else str(key)
        timestamp = press_times.get(key_str, time.time() - start_time)
        actions.append({'type': 'key_action', 'key': key_str, 'timestamp': timestamp})
        press_times.pop(key_str, None)

def on_move(x, y):
    if recording:
        timestamp = time.time() - start_time
        actions.append({'type': 'mouse_move', 'x': x, 'y': y, 'timestamp': timestamp, 'min_move_duration': 0.0, 'max_move_duration': 0.0})

def on_click(x, y, button, pressed):
    if recording:
        ts = time.time() - start_time
        actions.append({'type': 'mouse_move', 'x': x, 'y': y, 'timestamp': ts - 0.001, 'min_move_duration': 0.0, 'max_move_duration': 0.0})
        button_key = f"mouse.{str(button).split('.')[-1]}"
        if pressed:
            press_times[button_key] = ts
        else:
            timestamp = press_times.get(button_key, ts)
            actions.append({'type': 'key_action', 'key': button_key, 'timestamp': timestamp})
            press_times.pop(button_key, None)

def start_recording():
    global actions, start_time, recording, listeners, press_times
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
    start_time = time.time()
    recording = True
    update_status("Recording... Press Esc to stop.")
    status_label.config(background='red', foreground='white')
    record_btn.config(text="Stop Recording (F3)", command=stop_recording, state=tk.NORMAL)

    kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click)

    kb_listener.start()
    mouse_listener.start()

    listeners['kb'] = kb_listener
    listeners['mouse'] = mouse_listener

def stop_recording():
    global recording, listeners
    recording = False
    if 'kb' in listeners:
        listeners['kb'].stop()
        del listeners['kb']
    if 'mouse' in listeners:
        listeners['mouse'].stop()
        del listeners['mouse']
    update_status("Recording stopped.")
    status_label.config(background='#f0f0f0', foreground='black')
    record_btn.config(text="Record (F3)", command=start_recording, state=tk.NORMAL)
    start_stop_btn.config(state=tk.NORMAL)
    # Post-process actions
    if actions:
        actions.sort(key=lambda x: x['timestamp'])
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
    playback_active = True
    pressed_items = []
    update_status("Playback starting in 3 seconds...")
    if not numpy_available:
        update_status("Warning: numpy not installed, mouse movements will be instant.")
    root.update()
    interruptible_sleep(3)
    root.after(0, update_ui_for_playback)

    def run_playback():
        global playback_active
        playback_start = time.time()
        current_pos = mouse_controller.position
        rep = 0
        total_seconds = repeat_value * 60 if repeat_mode == "Minutes" else float('inf')
        while playback_active:
            for action in actions:
                if not playback_active:
                    break
                if time.time() - playback_start >= total_seconds:
                    playback_active = False
                    break
                delay = random.uniform(action.get('min_delay', 0.0), action.get('max_delay', 0.0))
                interruptible_sleep(delay)
                if not playback_active:
                    break
                if action['type'] == 'key_action':
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
                    hold_duration = random.uniform(0.001, 0.3)
                    interruptible_sleep(hold_duration)
                    for ctrl, itm in reversed(items):
                        if itm is not None:
                            ctrl.release(itm)
                        if (ctrl, itm) in pressed_items:
                            pressed_items.remove((ctrl, itm))
                elif action['type'] == 'mouse_move':
                    move_dur = random.uniform(action.get('min_move_duration', 0.0), action.get('max_move_duration', 0.0))
                    human_move(current_pos[0], current_pos[1], action['x'], action['y'], move_dur, seed=hash((current_pos, (action['x'], action['y']))))
                    current_pos = (action['x'], action['y'])
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
    new_action = {'type': action_type, 'min_delay': 0.1, 'max_delay': 0.1}
    
    if action_type == 'key_action':
        new_action['key'] = 'a'
    elif action_type == 'mouse_move':
        new_action['x'] = 0
        new_action['y'] = 0
        new_action['min_move_duration'] = 0.5
        new_action['max_move_duration'] = 0.5
    
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

def update_status(text):
    status_label.config(text=text)

def on_tree_select(event):
    # Always hide the editor frame first
    editor_labelframe.pack_forget()
    selected = tree.selection()
    if len(selected) == 1:
        global selected_idx
        selected_idx = int(selected[0])
        populate_editor(actions[selected_idx])
        # Repack the frame only when a single row is selected
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
    x_var.set(str(action.get('x', 0)))
    y_var.set(str(action.get('y', 0)))
    min_move_dur_var.set(f"{action.get('min_move_duration', 0.0):.3f}")
    max_move_dur_var.set(f"{action.get('max_move_duration', 0.0):.3f}")

    # Hide all type-specific widgets first
    key_label.grid_remove()
    key_entry.grid_remove()
    capture_btn.grid_remove()
    x_label.grid_remove()
    x_entry.grid_remove()
    y_label.grid_remove()
    y_entry.grid_remove()
    capture_pos_btn.grid_remove()
    min_move_dur_label.grid_remove()
    min_move_dur_entry.grid_remove()
    max_move_dur_label.grid_remove()
    max_move_dur_entry.grid_remove()

    if action['type'] == 'key_action':
        key_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        key_entry.grid(row=2, column=1, columnspan=3, padx=5, pady=5, sticky=tk.W)
        capture_btn.grid(row=2, column=4, padx=5, pady=5)
        key_entry.config(state='normal')
        capture_btn.config(state='normal')
    elif action['type'] == 'mouse_move':
        x_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        x_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)
        y_label.grid(row=2, column=2, padx=5, pady=5, sticky=tk.E)
        y_entry.grid(row=2, column=3, padx=5, pady=5, sticky=tk.W)
        capture_pos_btn.grid(row=2, column=4, padx=5, pady=5)
        min_move_dur_label.grid(row=3, column=0, padx=5, pady=5, sticky=tk.E)
        min_move_dur_entry.grid(row=3, column=1, padx=5, pady=5, sticky=tk.W)
        max_move_dur_label.grid(row=3, column=2, padx=5, pady=5, sticky=tk.E)
        max_move_dur_entry.grid(row=3, column=3, padx=5, pady=5, sticky=tk.W)
        x_entry.config(state='normal')
        y_entry.config(state='normal')
        capture_pos_btn.config(state='normal')
        min_move_dur_entry.config(state='normal')
        max_move_dur_entry.config(state='normal')
    
    save_btn.config(state='normal')

def clear_editor():
    min_delay_var.set('')
    max_delay_var.set('')
    type_combo.set('')
    key_var.set('')
    x_var.set('')
    y_var.set('')
    min_move_dur_var.set('')
    max_move_dur_var.set('')
    min_delay_entry.config(state='disabled')
    max_delay_entry.config(state='disabled')
    type_combo.config(state='disabled')
    key_entry.config(state='disabled')
    x_entry.config(state='disabled')
    y_entry.config(state='disabled')
    min_move_dur_entry.config(state='disabled')
    max_move_dur_entry.config(state='disabled')
    capture_btn.config(state='disabled')
    capture_pos_btn.config(state='disabled')
    save_btn.config(state='disabled')
    # Hide type-specific widgets
    key_label.grid_remove()
    key_entry.grid_remove()
    capture_btn.grid_remove()
    x_label.grid_remove()
    x_entry.grid_remove()
    y_label.grid_remove()
    y_entry.grid_remove()
    capture_pos_btn.grid_remove()
    min_move_dur_label.grid_remove()
    min_move_dur_entry.grid_remove()
    max_move_dur_label.grid_remove()
    max_move_dur_entry.grid_remove()

def on_type_change(event):
    action = actions[selected_idx]
    new_type = type_combo.get()
    if new_type != action['type']:
        action['type'] = new_type
        if new_type == 'key_action':
            action['key'] = 'a'
            if 'x' in action:
                del action['x']
            if 'y' in action:
                del action['y']
            if 'min_move_duration' in action:
                del action['min_move_duration']
            if 'max_move_duration' in action:
                del action['max_move_duration']
        elif new_type == 'mouse_move':
            action['x'] = 0
            action['y'] = 0
            action['min_move_duration'] = 0.5
            action['max_move_duration'] = 0.5
            if 'key' in action:
                del action['key']
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
            min_dur = float(min_move_dur_var.get())
            max_dur = float(max_move_dur_var.get())
            if min_dur < 0 or max_dur < 0 or min_dur > max_dur:
                raise ValueError("Min duration must be <= max duration and both non-negative.")
            action['min_move_duration'] = min_dur
            action['max_move_duration'] = max_dur
            action['x'] = int(x_var.get())
            action['y'] = int(y_var.get())
        elif action['type'] == 'key_action':
            action['key'] = key_var.get().strip()
            if not action['key']:
                raise ValueError("Key cannot be empty.")
    except ValueError as e:
        messagebox.showerror("Invalid Input", str(e) or "Invalid values entered.")
        return
    update_tree()
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

def capture_position():
    def on_capture_click(x, y, button, pressed):
        if pressed:
            x_var.set(str(x))
            y_var.set(str(y))
            stop_capture()
            update_status("Mouse position captured.")
        return False

    def stop_capture():
        global capture_listener_mouse
        if capture_listener_mouse:
            capture_listener_mouse.stop()
            capture_listener_mouse = None

    global capture_listener_mouse
    stop_capture()
    update_status("Click to capture position in 3 seconds...")
    root.update()
    time.sleep(3)
    capture_listener_mouse = mouse.Listener(on_click=on_capture_click)
    capture_listener_mouse.start()
    update_status("Click to capture mouse position...")

def show_menu(event):
    menu.delete(0, tk.END)
    row = tree.identify_row(event.y)
    if row:
        if row not in tree.selection():
            tree.selection_set(row)
        selected = tree.selection()
        if len(selected) > 1:
            menu.add_command(label="Delete", command=delete_selected)
        else:
            add_menu = tk.Menu(menu, tearoff=0)
            for act_type in ACTION_TYPES:
                add_menu.add_command(label=act_type, command=lambda t=act_type: insert_action(t, after_iid=row))
            menu.add_cascade(label="Insert Below", menu=add_menu)
            menu.add_command(label="Delete", command=delete_selected)
    else:
        add_menu = tk.Menu(menu, tearoff=0)
        for act_type in ACTION_TYPES:
            add_menu.add_command(label=act_type, command=lambda t=act_type: insert_action(t, after_iid=None))
        menu.add_cascade(label="Add Action", menu=add_menu)
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

root.config(menu=menubar)

# Bind keyboard shortcuts
root.bind("<Control-n>", lambda e: new_macro())
root.bind("<Control-o>", lambda e: load_macro())
root.bind("<Control-s>", lambda e: save_macro())

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

start_stop_btn = ttk.Button(button_frame, text="Start (F1)", command=toggle_playback, style='GreenButton.TButton')
start_stop_btn.grid(row=0, column=3, padx=5)
Tooltip(start_stop_btn, "Start the macro. Click or press F1 to stop when running.")

repeat_label = ttk.Label(button_frame, text="Repeat:")
repeat_label.grid(row=0, column=4, padx=5)
mode_var = tk.StringVar(value="Loops")
mode_combo = ttk.Combobox(button_frame, values=["Loops", "Minutes"], state='readonly', textvariable=mode_var, width=10)
mode_combo.grid(row=0, column=5, padx=5)
Tooltip(mode_combo, "Repeat mode: number of loops or total minutes to run.")
repeat_var = tk.StringVar(value="1")
repeat_entry = ttk.Entry(button_frame, textvariable=repeat_var, width=5)
repeat_entry.grid(row=0, column=6, padx=5)
Tooltip(repeat_entry, "Repeat value: number of loops or minutes depending on mode.")

# Treeview for displaying actions
columns = ("delay", "type", "details")
tree = ttk.Treeview(root, columns=columns, show="headings", height=15, selectmode="extended")
tree.heading("delay", text="Delay Range (s)")
tree.heading("type", text="Action Type")
tree.heading("details", text="Details")
tree.column("delay", width=150)
tree.column("type", width=150)
tree.column("details", width=400)
tree.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
tree.tag_configure('even', background='#f4f4f4')
tree.tag_configure('odd', background='#ffffff')
tree.tag_configure('drop_target', background='lightblue')
Tooltip(tree, "List of actions. Right-click to add/insert/delete. Drag to reorder.")

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
x_var = tk.StringVar()
y_var = tk.StringVar()
min_move_dur_var = tk.StringVar()
max_move_dur_var = tk.StringVar()

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
Tooltip(type_combo, "Type of action: key press or mouse movement.")

# Type-specific widgets (created but not gridded yet)
key_label = ttk.Label(editor_frame, text="Key:")
key_entry = ttk.Entry(editor_frame, textvariable=key_var, state='disabled', width=15)
Tooltip(key_entry, "The key or button for this action.")
capture_btn = ttk.Button(editor_frame, text="Capture Input", command=capture_input, state='disabled')
Tooltip(capture_btn, "Capture a key or mouse button press.")

x_label = ttk.Label(editor_frame, text="X Position:")
x_entry = ttk.Entry(editor_frame, textvariable=x_var, state='disabled', width=15)
Tooltip(x_entry, "X coordinate for mouse move.")

y_label = ttk.Label(editor_frame, text="Y Position:")
y_entry = ttk.Entry(editor_frame, textvariable=y_var, state='disabled', width=15)
Tooltip(y_entry, "Y coordinate for mouse move.")

capture_pos_btn = ttk.Button(editor_frame, text="Capture Position", command=capture_position, state='disabled')
Tooltip(capture_pos_btn, "Capture current mouse position.")

min_move_dur_label = ttk.Label(editor_frame, text="Min Move Dur (s):")
min_move_dur_entry = ttk.Entry(editor_frame, textvariable=min_move_dur_var, state='disabled', width=15)
Tooltip(min_move_dur_entry, "Minimum time to perform the mouse movement (human-like if >0).")

max_move_dur_label = ttk.Label(editor_frame, text="Max Move Dur (s):")
max_move_dur_entry = ttk.Entry(editor_frame, textvariable=max_move_dur_var, state='disabled', width=15)
Tooltip(max_move_dur_entry, "Maximum time to perform the mouse movement (randomized between min and max).")

save_btn = ttk.Button(editor_frame, text="Save Changes", command=save_changes, state='disabled')
save_btn.grid(row=4, column=0, columnspan=5, pady=10)  # Always gridded, but state disabled when not needed
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