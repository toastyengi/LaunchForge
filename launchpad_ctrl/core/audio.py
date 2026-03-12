"""
Audio engine for playback, recording, and device management.
Supports WAV, OGG, and MP3 (via pydub/ffmpeg).

Device enumeration uses PulseAudio/PipeWire (via pactl) when available so that
virtual sinks/sources (e.g. created by module-null-sink, module-remap-source)
are visible alongside hardware devices.  Falls back to pure sounddevice
enumeration when pactl is not present.
"""

import os
import subprocess
import threading
import time
import json
from typing import Optional, Dict, List, Callable, Union

import numpy as np

try:
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False

try:
    import soundfile as sf
    SF_AVAILABLE = True
except ImportError:
    SF_AVAILABLE = False

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False


def _pactl_available() -> bool:
    """Return True if the pactl CLI is reachable."""
    try:
        subprocess.run(
            ["pactl", "info"],
            capture_output=True, timeout=3,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _parse_pactl_list(kind: str) -> List[Dict]:
    """Parse ``pactl list sinks/sources`` into a list of device dicts.

    *kind* must be ``"sinks"`` or ``"sources"``.
    Returns a list of dicts with keys:
        pa_name  – PulseAudio/PipeWire device name (used to open the device)
        name     – human-readable description
        channels – number of channels
        samplerate – sample-rate (float)
        state    – device state string (e.g. "RUNNING", "IDLE", "SUSPENDED")
    """
    try:
        result = subprocess.run(
            ["pactl", "list", kind],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    devices: List[Dict] = []
    current: Dict = {}

    for line in result.stdout.splitlines():
        stripped = line.strip()

        # New device block
        if stripped.startswith(("Sink #", "Source #")):
            if current.get("pa_name"):
                devices.append(current)
            current = {}
            continue

        if stripped.startswith("Name:"):
            current["pa_name"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Description:"):
            current["name"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("State:"):
            current["state"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Sample Specification:"):
            # e.g. "s16le 2ch 48000Hz"
            spec = stripped.split(":", 1)[1].strip()
            parts = spec.split()
            for p in parts:
                if p.endswith("ch"):
                    try:
                        current["channels"] = int(p[:-2])
                    except ValueError:
                        pass
                elif p.endswith("Hz"):
                    try:
                        current["samplerate"] = float(p[:-2])
                    except ValueError:
                        pass

    # Don't forget the last block
    if current.get("pa_name"):
        devices.append(current)

    # Fill in defaults for any missing fields
    for d in devices:
        d.setdefault("name", d["pa_name"])
        d.setdefault("channels", 2)
        d.setdefault("samplerate", 48000.0)
        d.setdefault("state", "UNKNOWN")

    return devices


def _pulse_env_set(pa_name: Optional[str], direction: str):
    """Set or clear the ``PULSE_SINK`` / ``PULSE_SOURCE`` env-var.

    When PortAudio opens a stream through PulseAudio/PipeWire it honours
    these variables to route *that specific stream* to the named device
    **without** changing the system-wide default.

    *direction* must be ``"output"`` (sets ``PULSE_SINK``) or
    ``"input"`` (sets ``PULSE_SOURCE``).

    NOTE: On Arch Linux with PipeWire, PortAudio typically uses the ALSA
    backend (via pipewire-alsa), which ignores these env-vars.  We set them
    anyway as a best-effort, but the real routing is done by
    ``_pactl_move_stream_to_device()`` after the stream is opened.
    """
    env_key = "PULSE_SINK" if direction == "output" else "PULSE_SOURCE"
    if pa_name:
        os.environ[env_key] = pa_name
        print(f"[Audio][DEBUG] Set {env_key}={pa_name}")
    else:
        os.environ.pop(env_key, None)
        print(f"[Audio][DEBUG] Cleared {env_key}")


def _pactl_move_stream_to_device(pa_name: str, direction: str,
                                  known_stream_ids: Optional[set] = None):
    """Move our process's PulseAudio/PipeWire stream(s) to the given device.

    After PortAudio opens a stream, PipeWire registers it as a sink-input
    (playback) or source-output (recording).  This function identifies our
    stream and moves it to *pa_name*.

    Identification strategy (PipeWire+ALSA-backend doesn't expose PID):
      1. Match by ``application.process.id`` (works on native PulseAudio).
      2. Match by ``application.name`` containing "python" – this is what
         PipeWire's ALSA backend sets for Python programs (e.g.
         ``PipeWire ALSA [python3.10]``).
      3. Use *known_stream_ids* (IDs that existed before we opened our
         stream) to find the NEW stream that appeared since.

    *direction* must be ``"output"`` or ``"input"``.
    Returns the set of stream IDs that were successfully moved.
    """
    our_pid = str(os.getpid())

    if direction == "output":
        list_cmd = ["pactl", "list", "sink-inputs"]
        move_cmd_prefix = ["pactl", "move-sink-input"]
        stream_label = "Sink Input"
        id_prefix = "Sink Input #"
    else:
        list_cmd = ["pactl", "list", "source-outputs"]
        move_cmd_prefix = ["pactl", "move-source-output"]
        stream_label = "Source Output"
        id_prefix = "Source Output #"

    try:
        result = subprocess.run(
            list_cmd, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            print(f"[Audio][DEBUG] pactl list failed: {result.stderr.strip()}")
            return set()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[Audio][DEBUG] pactl not available: {e}")
        return set()

    print(f"[Audio][DEBUG] Looking for {stream_label}s (PID={our_pid})")

    # Parse all stream blocks
    streams = _parse_stream_blocks(result.stdout, id_prefix)

    if streams:
        print(f"[Audio][DEBUG] All {stream_label}s currently registered:")
        for s in streams:
            print(f"[Audio][DEBUG]   #{s['id']}  PID={s['pid']}  "
                  f"app={s['app']!r}  binary={s['binary']!r}")

    # --- Strategy 1: exact PID match ---
    matched = [s for s in streams if s["pid"] == our_pid]

    # --- Strategy 2: app name contains "python" (PipeWire ALSA backend) ---
    if not matched:
        matched = [s for s in streams
                   if "python" in s["app"].lower()]
        if matched:
            print(f"[Audio][DEBUG] PID not found, matched {len(matched)} stream(s) "
                  f"by app name containing 'python'")

    # --- Strategy 3: new stream ID not in known_stream_ids ---
    if not matched and known_stream_ids is not None:
        matched = [s for s in streams
                   if s["id"] not in known_stream_ids]
        if matched:
            print(f"[Audio][DEBUG] Matched {len(matched)} NEW stream(s) "
                  f"(not in pre-existing set)")

    moved_ids = set()
    for s in matched:
        if _do_move(move_cmd_prefix, s["id"], pa_name, stream_label):
            moved_ids.add(s["id"])

    if not moved_ids:
        print(f"[Audio][DEBUG] WARNING: Could not find/move any {stream_label}s")
    else:
        print(f"[Audio][DEBUG] Moved {len(moved_ids)} {stream_label}(s) -> {pa_name}")

    return moved_ids


def _parse_stream_blocks(pactl_output: str, id_prefix: str) -> List[Dict]:
    """Parse ``pactl list sink-inputs / source-outputs`` into a list of dicts.

    Each dict has keys: id, pid, app, binary.
    """
    streams: List[Dict] = []
    current: Optional[Dict] = None

    for line in pactl_output.splitlines():
        stripped = line.strip()

        if stripped.startswith(id_prefix):
            if current is not None:
                streams.append(current)
            current = {
                "id": stripped[len(id_prefix):],
                "pid": "?",
                "app": "?",
                "binary": "?",
            }
            continue

        if current is None:
            continue

        # Properties we care about
        if "application.process.id" in stripped:
            val = _prop_value(stripped)
            if val:
                current["pid"] = val
        elif "application.name" in stripped and "icon" not in stripped:
            val = _prop_value(stripped)
            if val:
                current["app"] = val
        elif "application.process.binary" in stripped:
            val = _prop_value(stripped)
            if val:
                current["binary"] = val

    if current is not None:
        streams.append(current)
    return streams


def _prop_value(line: str) -> Optional[str]:
    """Extract the value from a ``key = "value"`` pactl property line."""
    parts = line.split("=", 1)
    if len(parts) == 2:
        return parts[1].strip().strip('"').strip("'")
    return None


def _do_move(cmd_prefix: list, stream_id: str, pa_name: str,
             label: str) -> bool:
    """Execute ``pactl move-sink-input / move-source-output``.

    Returns True on success.
    """
    cmd = cmd_prefix + [stream_id, pa_name]
    print(f"[Audio][DEBUG] Running: {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            print(f"[Audio][DEBUG] Move failed: {r.stderr.strip()}")
            return False
        print(f"[Audio][DEBUG] Successfully moved {label} #{stream_id} -> {pa_name}")
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[Audio][DEBUG] Move error: {e}")
        return False


def _get_current_stream_ids(direction: str) -> set:
    """Return the set of currently registered stream IDs.

    Used to snapshot IDs *before* opening a new stream, so we can identify
    the new one by diffing.
    """
    if direction == "output":
        list_cmd = ["pactl", "list", "short", "sink-inputs"]
    else:
        list_cmd = ["pactl", "list", "short", "source-outputs"]

    try:
        r = subprocess.run(list_cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return set()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()

    ids = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts:
            ids.add(parts[0])
    print(f"[Audio][DEBUG] Pre-existing stream IDs ({direction}): {ids}")
    return ids


def _sd_device_for_pa(pa_name: str, pa_description: str,
                      direction: str) -> Optional[int]:
    """Try to find a sounddevice index matching a PulseAudio device.

    Matches by comparing the PA *description* (human-readable) against
    sounddevice's device name, which PortAudio typically populates from the
    same description string.  *direction* must be ``"input"`` or ``"output"``.
    """
    if not SD_AVAILABLE:
        return None
    try:
        devices = sd.query_devices()
    except Exception:
        return None

    chan_key = ("max_input_channels" if direction == "input"
                else "max_output_channels")
    pa_desc_lower = pa_description.lower()

    for i, d in enumerate(devices):
        if d[chan_key] <= 0:
            continue
        sd_name_lower = d["name"].lower()
        # Exact or substring match between PA description and sd name
        if pa_desc_lower in sd_name_lower or sd_name_lower in pa_desc_lower:
            return i
        # Also try the PA internal name as a fallback
        if pa_name.lower() in sd_name_lower:
            return i
    return None


class AudioDevice:
    """Manages audio device selection and enumeration.

    Prefers PulseAudio/PipeWire enumeration (via ``pactl``) so that virtual
    devices such as null-sinks, remap-sources, and loopback monitors are
    visible.  Falls back to ``sounddevice.query_devices()`` when ``pactl`` is
    not available.

    Each device dict contains:
        index      – sounddevice index (int) or ``None`` for PA-only devices
        pa_name    – PulseAudio device name (str) or ``None``
        name       – human-readable label
        channels   – channel count
        samplerate – default sample-rate
    """

    # Cached flag – computed once per session, refreshable.
    _pa_ok: Optional[bool] = None

    @classmethod
    def _has_pa(cls) -> bool:
        if cls._pa_ok is None:
            cls._pa_ok = _pactl_available()
        return cls._pa_ok

    @classmethod
    def refresh(cls):
        """Force re-enumeration (e.g. after creating new virtual devices)."""
        cls._pa_ok = None
        if SD_AVAILABLE:
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass

    @staticmethod
    def _sd_input_devices() -> List[Dict]:
        if not SD_AVAILABLE:
            return []
        devices = sd.query_devices()
        return [
            {"index": i, "pa_name": None, "name": d["name"],
             "channels": d["max_input_channels"],
             "samplerate": d["default_samplerate"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]

    @staticmethod
    def _sd_output_devices() -> List[Dict]:
        if not SD_AVAILABLE:
            return []
        devices = sd.query_devices()
        return [
            {"index": i, "pa_name": None, "name": d["name"],
             "channels": d["max_output_channels"],
             "samplerate": d["default_samplerate"]}
            for i, d in enumerate(devices)
            if d["max_output_channels"] > 0
        ]

    @classmethod
    def list_input_devices(cls) -> List[Dict]:
        """Return all available input (source) devices."""
        if cls._has_pa():
            pa_sources = _parse_pactl_list("sources")
            results = []
            for d in pa_sources:
                pa_name = d["pa_name"]
                sd_idx = _sd_device_for_pa(pa_name, d["name"], "input")
                results.append({
                    "index": sd_idx,
                    "pa_name": pa_name,
                    "name": d["name"],
                    "channels": d["channels"],
                    "samplerate": d["samplerate"],
                })
            return results

        return cls._sd_input_devices()

    @classmethod
    def list_output_devices(cls) -> List[Dict]:
        """Return all available output (sink) devices."""
        if cls._has_pa():
            pa_sinks = _parse_pactl_list("sinks")
            print(f"[Audio][DEBUG] pactl found {len(pa_sinks)} sink(s):")
            results = []
            for d in pa_sinks:
                sd_idx = _sd_device_for_pa(d["pa_name"], d["name"], "output")
                print(f"[Audio][DEBUG]   sink: {d['pa_name']!r} "
                      f"desc={d['name']!r} sd_idx={sd_idx}")
                results.append({
                    "index": sd_idx,
                    "pa_name": d["pa_name"],
                    "name": d["name"],
                    "channels": d["channels"],
                    "samplerate": d["samplerate"],
                })
            return results

        print("[Audio][DEBUG] pactl not available, falling back to sounddevice")
        return cls._sd_output_devices()

    # Track the currently selected PA names so start()/start_recording()
    # can set up the default sink/source before opening streams.
    _selected_output_pa: Optional[str] = None
    _selected_input_pa: Optional[str] = None

    @classmethod
    def set_input_device(cls, device_id, pa_name: Optional[str] = None):
        """Set default input device.

        *device_id* is a sounddevice index (int) or ``None``.
        *pa_name* is the PulseAudio device name (str) or ``None``.
        """
        print(f"[Audio][DEBUG] set_input_device(device_id={device_id!r}, pa_name={pa_name!r})")
        cls._selected_input_pa = pa_name
        if SD_AVAILABLE:
            if isinstance(device_id, int):
                sd.default.device[0] = device_id
            else:
                sd.default.device[0] = None
        # Set env-var (best-effort; real routing done via pactl move)
        _pulse_env_set(pa_name, "input")

    @classmethod
    def set_output_device(cls, device_id, pa_name: Optional[str] = None):
        """Set default output device.

        *device_id* is a sounddevice index (int) or ``None``.
        *pa_name* is the PulseAudio device name (str) or ``None``.
        """
        print(f"[Audio][DEBUG] set_output_device(device_id={device_id!r}, pa_name={pa_name!r})")
        cls._selected_output_pa = pa_name
        if SD_AVAILABLE:
            if isinstance(device_id, int):
                sd.default.device[1] = device_id
            else:
                sd.default.device[1] = None
        # Set env-var (best-effort; real routing done via pactl move)
        _pulse_env_set(pa_name, "output")

    @staticmethod
    def get_default_devices():
        if SD_AVAILABLE:
            return sd.default.device
        return [None, None]

    @staticmethod
    def resolve_device_info(dev: Dict):
        """Return ``(device_id, pa_name)`` for the combo-box item data.

        *device_id* is an int sounddevice index when available, else ``None``.
        *pa_name* is the PulseAudio name string, else ``None``.
        """
        sd_idx = dev.get("index")  # int or None
        pa_name = dev.get("pa_name")  # str or None
        return (sd_idx, pa_name)


class SoundLoader:
    """Loads audio files into numpy arrays. Supports WAV, OGG, MP3."""

    _cache: Dict[str, tuple] = {}  # path -> (data, samplerate)

    @classmethod
    def load(cls, filepath: str, use_cache: bool = True) -> Optional[tuple]:
        """Load an audio file. Returns (numpy_array, samplerate) or None."""
        if use_cache and filepath in cls._cache:
            return cls._cache[filepath]

        if not os.path.exists(filepath):
            print(f"[Audio] File not found: {filepath}")
            return None

        ext = os.path.splitext(filepath)[1].lower()
        data = None
        sr = 44100

        try:
            if ext in (".wav", ".ogg", ".flac"):
                if SF_AVAILABLE:
                    data, sr = sf.read(filepath, dtype="float32")
                else:
                    print("[Audio] soundfile not available")
                    return None
            elif ext == ".mp3":
                if PYDUB_AVAILABLE:
                    audio = AudioSegment.from_mp3(filepath)
                    sr = audio.frame_rate
                    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
                    samples /= 2 ** (audio.sample_width * 8 - 1)
                    if audio.channels == 2:
                        samples = samples.reshape((-1, 2))
                    data = samples
                else:
                    print("[Audio] pydub not available for MP3")
                    return None
            else:
                # Try soundfile as fallback
                if SF_AVAILABLE:
                    data, sr = sf.read(filepath, dtype="float32")

            if data is not None:
                # Ensure stereo
                if data.ndim == 1:
                    data = np.column_stack([data, data])
                if use_cache:
                    cls._cache[filepath] = (data, sr)
                return (data, sr)

        except Exception as e:
            print(f"[Audio] Error loading {filepath}: {e}")

        return None

    @classmethod
    def clear_cache(cls):
        cls._cache.clear()


class PlaybackInstance:
    """A single sound playback instance."""

    def __init__(self, data: np.ndarray, samplerate: int, volume: float = 1.0,
                 loop: bool = False, on_finish: Optional[Callable] = None):
        self.data = data
        self.samplerate = samplerate
        self.volume = volume
        self.loop = loop
        self.on_finish = on_finish
        self.position = 0
        self.active = True
        self._id = id(self)

    def get_samples(self, frames: int) -> np.ndarray:
        """Get the next chunk of samples."""
        if not self.active:
            return np.zeros((frames, 2), dtype=np.float32)

        output = np.zeros((frames, 2), dtype=np.float32)
        remaining = frames
        write_pos = 0

        while remaining > 0 and self.active:
            available = len(self.data) - self.position
            to_copy = min(remaining, available)

            if to_copy > 0:
                chunk = self.data[self.position : self.position + to_copy]
                if chunk.ndim == 1:
                    chunk = np.column_stack([chunk, chunk])
                elif chunk.shape[1] == 1:
                    chunk = np.column_stack([chunk, chunk])
                # Handle channel mismatch
                output[write_pos : write_pos + to_copy, :2] = chunk[:, :2] * self.volume
                self.position += to_copy
                write_pos += to_copy
                remaining -= to_copy

            if self.position >= len(self.data):
                if self.loop:
                    self.position = 0
                else:
                    self.active = False
                    if self.on_finish:
                        self.on_finish(self._id)
                    break

        return output

    def stop(self):
        self.active = False


class AudioEngine:
    """Main audio engine managing playback and recording."""

    def __init__(self, samplerate: int = 44100, blocksize: int = 512):
        self.samplerate = samplerate
        self.blocksize = blocksize
        self._playback_instances: Dict[int, PlaybackInstance] = {}
        self._stream = None
        self._recording = False
        self._record_buffer: List[np.ndarray] = []
        self._record_stream = None
        self._master_volume = 0.8
        self._lock = threading.Lock()
        self._on_record_complete: Optional[Callable] = None

    @property
    def master_volume(self):
        return self._master_volume

    @master_volume.setter
    def master_volume(self, value: float):
        self._master_volume = max(0.0, min(1.0, value))

    def start(self):
        """Start the audio output stream."""
        if not SD_AVAILABLE:
            print("[Audio] sounddevice not available")
            return

        pa_out = AudioDevice._selected_output_pa
        print(f"[Audio][DEBUG] start() called — selected PA output: {pa_out!r}")
        print(f"[Audio][DEBUG] sd.default.device = {sd.default.device}")

        # Set env-var (works when PortAudio uses PulseAudio backend)
        _pulse_env_set(pa_out, "output")

        # Snapshot existing stream IDs so we can find the NEW one we create
        pre_ids = _get_current_stream_ids("output") if pa_out else set()

        try:
            kwargs = {
                "samplerate": self.samplerate,
                "blocksize": self.blocksize,
                "channels": 2,
                "dtype": "float32",
                "callback": self._audio_callback,
            }
            dev = sd.default.device[1]
            if isinstance(dev, int):
                kwargs["device"] = dev
                print(f"[Audio][DEBUG] Using sounddevice index {dev}")
            else:
                print("[Audio][DEBUG] No sd index — using system default device")

            self._stream = sd.OutputStream(**kwargs)
            self._stream.start()
            print("[Audio] Engine started")

            # On PipeWire/ALSA-backend systems, PULSE_SINK is ignored.
            # Move our stream to the target sink via pactl after it's opened.
            if pa_out:
                threading.Thread(
                    target=self._move_output_stream,
                    args=(pa_out, pre_ids),
                    daemon=True,
                ).start()

        except Exception as e:
            print(f"[Audio] Failed to start: {e}")
            import traceback
            traceback.print_exc()

    def _move_output_stream(self, pa_name: str, pre_ids: set):
        """Move our output stream to *pa_name* (runs in a background thread).

        Retries a few times because PipeWire may take a moment to register the
        new PortAudio stream as a sink-input.
        """
        for attempt in range(5):
            time.sleep(0.2 * (attempt + 1))  # 200ms, 400ms, 600ms …
            print(f"[Audio][DEBUG] move-output attempt {attempt + 1}/5")
            moved = _pactl_move_stream_to_device(pa_name, "output", pre_ids)
            if moved:
                print(f"[Audio][DEBUG] Stream successfully routed to {pa_name}")
                return
        print(f"[Audio][DEBUG] WARNING: Could not move stream to {pa_name} "
              f"after 5 attempts")

    def restart(self):
        """Restart the audio output stream (e.g., after device change)."""
        print("[Audio][DEBUG] restart() called — stopping current stream and re-opening")
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.start()

    def stop(self):
        """Stop the audio engine."""
        self.stop_all()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._record_stream:
            self._record_stream.stop()
            self._record_stream.close()
            self._record_stream = None
        print("[Audio] Engine stopped")

    def _audio_callback(self, outdata, frames, time_info, status):
        """Audio stream callback - mixes all active playback instances."""
        output = np.zeros((frames, 2), dtype=np.float32)

        with self._lock:
            dead = []
            for pid, instance in self._playback_instances.items():
                if instance.active:
                    output += instance.get_samples(frames)
                else:
                    dead.append(pid)
            for pid in dead:
                del self._playback_instances[pid]

        output *= self._master_volume
        # Clip to prevent distortion
        np.clip(output, -1.0, 1.0, out=output)
        outdata[:] = output

    def play_sound(self, filepath: str, volume: float = 1.0, loop: bool = False) -> Optional[int]:
        """Play a sound file. Returns playback instance ID."""
        result = SoundLoader.load(filepath)
        if result is None:
            return None

        data, sr = result
        # Resample if needed (simple linear interpolation)
        if sr != self.samplerate:
            ratio = self.samplerate / sr
            new_length = int(len(data) * ratio)
            indices = np.linspace(0, len(data) - 1, new_length)
            data = np.array([np.interp(indices, np.arange(len(data)), data[:, ch])
                            for ch in range(data.shape[1])]).T.astype(np.float32)

        instance = PlaybackInstance(data, self.samplerate, volume, loop)

        with self._lock:
            self._playback_instances[instance._id] = instance

        return instance._id

    def play_data(self, data: np.ndarray, samplerate: Optional[int] = None,
                  volume: float = 1.0, loop: bool = False) -> Optional[int]:
        """Play an in-memory audio buffer. Returns playback instance ID."""
        if data is None or len(data) == 0:
            return None

        play_data = np.asarray(data, dtype=np.float32)
        if play_data.ndim == 1:
            play_data = np.column_stack([play_data, play_data])
        elif play_data.shape[1] == 1:
            play_data = np.column_stack([play_data, play_data])

        source_rate = samplerate or self.samplerate
        if source_rate != self.samplerate:
            ratio = self.samplerate / source_rate
            new_length = int(len(play_data) * ratio)
            indices = np.linspace(0, len(play_data) - 1, new_length)
            play_data = np.array([
                np.interp(indices, np.arange(len(play_data)), play_data[:, ch])
                for ch in range(play_data.shape[1])
            ]).T.astype(np.float32)

        instance = PlaybackInstance(play_data, self.samplerate, volume, loop)
        with self._lock:
            self._playback_instances[instance._id] = instance
        return instance._id

    def stop_sound(self, instance_id: int):
        """Stop a specific playback instance."""
        with self._lock:
            if instance_id in self._playback_instances:
                self._playback_instances[instance_id].stop()
                del self._playback_instances[instance_id]

    def stop_all(self):
        """Stop all playing sounds (panic button)."""
        with self._lock:
            for instance in self._playback_instances.values():
                instance.stop()
            self._playback_instances.clear()

    def is_playing(self) -> bool:
        with self._lock:
            return len(self._playback_instances) > 0

    def active_count(self) -> int:
        with self._lock:
            return len(self._playback_instances)

    # --- Recording ---

    def start_recording(self, input_device=None, channels: int = 1):
        """Start recording from microphone."""
        if self._recording:
            return
        if not SD_AVAILABLE:
            print("[Audio] sounddevice not available for recording")
            return

        pa_in = AudioDevice._selected_input_pa
        print(f"[Audio][DEBUG] start_recording() — selected PA input: {pa_in!r}")

        # Set env-var (works when PortAudio uses PulseAudio backend)
        _pulse_env_set(pa_in, "input")

        # Snapshot existing stream IDs so we can find the NEW one
        pre_ids = _get_current_stream_ids("input") if pa_in else set()

        self._record_buffer = []
        self._recording = True

        kwargs = {
            "samplerate": self.samplerate,
            "blocksize": self.blocksize,
            "channels": channels,
            "dtype": "float32",
            "callback": self._record_callback,
        }
        if isinstance(input_device, int):
            kwargs["device"] = input_device
            print(f"[Audio][DEBUG] Recording with explicit device index: {input_device}")
        else:
            dev = sd.default.device[0]
            if isinstance(dev, int):
                kwargs["device"] = dev
                print(f"[Audio][DEBUG] Recording with sd default index: {dev}")
            else:
                print("[Audio][DEBUG] Recording with system default device")

        try:
            self._record_stream = sd.InputStream(**kwargs)
            self._record_stream.start()
            print("[Audio] Recording started")

            # Move to the correct source via pactl (PipeWire/ALSA-backend fix)
            if pa_in:
                threading.Thread(
                    target=self._move_input_stream,
                    args=(pa_in, pre_ids),
                    daemon=True,
                ).start()

        except Exception as e:
            print(f"[Audio] Record start error: {e}")
            import traceback
            traceback.print_exc()
            self._recording = False

    def _move_input_stream(self, pa_name: str, pre_ids: set):
        """Move our input stream to *pa_name* (runs in a background thread)."""
        for attempt in range(5):
            time.sleep(0.2 * (attempt + 1))
            print(f"[Audio][DEBUG] move-input attempt {attempt + 1}/5")
            moved = _pactl_move_stream_to_device(pa_name, "input", pre_ids)
            if moved:
                print(f"[Audio][DEBUG] Input stream routed to {pa_name}")
                return
        print(f"[Audio][DEBUG] WARNING: Could not move input stream to {pa_name} "
              f"after 5 attempts")

    def stop_recording(self) -> Optional[np.ndarray]:
        """Stop recording and return the recorded data."""
        if not self._recording:
            return None

        self._recording = False
        if self._record_stream:
            self._record_stream.stop()
            self._record_stream.close()
            self._record_stream = None

        if self._record_buffer:
            data = np.concatenate(self._record_buffer, axis=0)
            self._record_buffer = []
            print(f"[Audio] Recording stopped: {len(data)} samples")
            return data
        return None

    def _record_callback(self, indata, frames, time_info, status):
        """Recording stream callback."""
        if self._recording:
            self._record_buffer.append(indata.copy())

    @property
    def is_recording(self):
        return self._recording

    def save_recording(self, data: np.ndarray, filepath: str, samplerate: Optional[int] = None):
        """Save recorded audio to a file."""
        if not SF_AVAILABLE:
            print("[Audio] soundfile not available for saving")
            return False
        sr = samplerate or self.samplerate
        try:
            sf.write(filepath, data, sr)
            print(f"[Audio] Saved recording to {filepath}")
            return True
        except Exception as e:
            print(f"[Audio] Save error: {e}")
            return False


class VirtualAudioRouter:
    """Manages virtual PulseAudio/PipeWire sinks and sources for routing
    application audio into a virtual microphone (e.g. for Discord, games).

    Creates:
      - *SoundboardSink*  – null sink that the app routes its audio into
      - *VirtualMix*      – null sink that combines soundboard audio + real mic
      - *VirtualMic*      – remap-source (virtual microphone) from VirtualMix

    Loopbacks:
      - SoundboardSink.monitor → VirtualMix   (app audio into virtual mic)
      - real mic              → VirtualMix   (voice into virtual mic)
      - SoundboardSink.monitor → real output  (so the user still hears audio)
    """

    SOUNDBOARD_SINK = "LF_SoundboardSink"
    MIX_SINK = "LF_VirtualMix"
    VIRTUAL_MIC = "LF_VirtualMic"

    def __init__(self):
        # Module IDs returned by pactl load-module, used for teardown
        self._module_ids: List[int] = []
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    # pactl helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_sink(name: str) -> bool:
        try:
            r = subprocess.run(
                ["pactl", "list", "short", "sinks"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[1] == name:
                    return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return False

    @staticmethod
    def _has_source(name: str) -> bool:
        try:
            r = subprocess.run(
                ["pactl", "list", "short", "sources"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[1] == name:
                    return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return False

    @staticmethod
    def _load_module(module: str, **kwargs) -> Optional[int]:
        """Load a PulseAudio module, return module ID or None."""
        args = [f"{k}={v}" for k, v in kwargs.items()]
        cmd = ["pactl", "load-module", module] + args
        print(f"[VirtualAudio] Running: {' '.join(cmd)}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                print(f"[VirtualAudio] Failed: {r.stderr.strip()}")
                return None
            mid = int(r.stdout.strip())
            print(f"[VirtualAudio] Loaded module {module} -> id {mid}")
            return mid
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as e:
            print(f"[VirtualAudio] Error: {e}")
            return None

    @staticmethod
    def _unload_module(module_id: int) -> bool:
        try:
            r = subprocess.run(
                ["pactl", "unload-module", str(module_id)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                print(f"[VirtualAudio] Unloaded module {module_id}")
                return True
            print(f"[VirtualAudio] Unload failed: {r.stderr.strip()}")
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"[VirtualAudio] Unload error: {e}")
        return False

    @staticmethod
    def _has_loopback(source: str, sink: str) -> bool:
        """Check if a loopback module with the given source/sink already exists."""
        try:
            r = subprocess.run(
                ["pactl", "list", "short", "modules"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                if "module-loopback" in line and source in line and sink in line:
                    return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enable(self, mic_source: Optional[str] = None,
               monitor_sink: Optional[str] = None) -> bool:
        """Create the virtual audio routing.

        *mic_source* – PA name of the real microphone to mix in (or None to
                       skip mic mixing).
        *monitor_sink* – PA name of the real output sink for local monitoring
                         (so the user can still hear the soundboard audio).

        Returns True if all essential modules were created successfully.
        """
        if self._active:
            print("[VirtualAudio] Already active")
            return True

        if not _pactl_available():
            print("[VirtualAudio] pactl not available — cannot create virtual audio")
            return False

        self._module_ids = []

        # 1. SoundboardSink (null sink – app sends audio here)
        if not self._has_sink(self.SOUNDBOARD_SINK):
            mid = self._load_module(
                "module-null-sink",
                sink_name=self.SOUNDBOARD_SINK,
                sink_properties=f"device.description={self.SOUNDBOARD_SINK}",
            )
            if mid is None:
                self._teardown()
                return False
            self._module_ids.append(mid)

        # 2. VirtualMix (null sink – combines soundboard + mic)
        if not self._has_sink(self.MIX_SINK):
            mid = self._load_module(
                "module-null-sink",
                sink_name=self.MIX_SINK,
                sink_properties=f"device.description={self.MIX_SINK}",
            )
            if mid is None:
                self._teardown()
                return False
            self._module_ids.append(mid)

        # 3. Loopback: SoundboardSink.monitor → VirtualMix
        sb_monitor = f"{self.SOUNDBOARD_SINK}.monitor"
        if not self._has_loopback(sb_monitor, self.MIX_SINK):
            mid = self._load_module(
                "module-loopback",
                source=sb_monitor,
                sink=self.MIX_SINK,
                latency_msec="1",
            )
            if mid is not None:
                self._module_ids.append(mid)

        # 4. Loopback: real mic → VirtualMix (so voice goes into the virtual mic)
        if mic_source:
            if not self._has_loopback(mic_source, self.MIX_SINK):
                mid = self._load_module(
                    "module-loopback",
                    source=mic_source,
                    sink=self.MIX_SINK,
                    latency_msec="1",
                )
                if mid is not None:
                    self._module_ids.append(mid)

        # 5. VirtualMic (remap-source from VirtualMix.monitor)
        if not self._has_source(self.VIRTUAL_MIC):
            mid = self._load_module(
                "module-remap-source",
                **{
                    "master": f"{self.MIX_SINK}.monitor",
                    "source_name": self.VIRTUAL_MIC,
                    "source_properties": f"device.description={self.VIRTUAL_MIC}",
                },
            )
            if mid is not None:
                self._module_ids.append(mid)

        # 6. Loopback: SoundboardSink.monitor → real output (local monitoring)
        if monitor_sink:
            if not self._has_loopback(sb_monitor, monitor_sink):
                mid = self._load_module(
                    "module-loopback",
                    source=sb_monitor,
                    sink=monitor_sink,
                    latency_msec="1",
                )
                if mid is not None:
                    self._module_ids.append(mid)

        self._active = True
        print(f"[VirtualAudio] Enabled — {len(self._module_ids)} modules loaded")
        print(f"[VirtualAudio] Virtual mic available as: {self.VIRTUAL_MIC}")
        return True

    def disable(self):
        """Remove all virtual audio modules created by enable()."""
        if not self._active:
            return
        self._teardown()
        self._active = False
        print("[VirtualAudio] Disabled — all modules unloaded")

    def _teardown(self):
        """Unload all tracked modules (reverse order)."""
        for mid in reversed(self._module_ids):
            self._unload_module(mid)
        self._module_ids.clear()
