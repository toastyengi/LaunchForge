"""
Audio engine for playback, recording, and device management.
Supports WAV, OGG, and MP3 (via pydub/ffmpeg).
"""

import os
import subprocess
import threading
import time
import json
from typing import Optional, Dict, List, Callable

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


class AudioDevice:
    """Manages audio device selection and enumeration."""

    @staticmethod
    def list_input_devices() -> List[Dict]:
        if not SD_AVAILABLE:
            return []
        devices = sd.query_devices()
        return [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"], "samplerate": d["default_samplerate"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]

    @staticmethod
    def list_output_devices() -> List[Dict]:
        if not SD_AVAILABLE:
            return []
        devices = sd.query_devices()
        return [
            {"index": i, "name": d["name"], "channels": d["max_output_channels"], "samplerate": d["default_samplerate"]}
            for i, d in enumerate(devices)
            if d["max_output_channels"] > 0
        ]

    @staticmethod
    def set_input_device(index: int):
        if SD_AVAILABLE:
            sd.default.device[0] = index

    @staticmethod
    def set_output_device(index: int):
        if SD_AVAILABLE:
            sd.default.device[1] = index

    @staticmethod
    def get_default_devices():
        if SD_AVAILABLE:
            return sd.default.device
        return [None, None]


class VirtualMicSink:
    """Manages a PipeWire/PulseAudio virtual microphone sink.

    Creates a virtual audio sink whose monitor acts as an input (microphone)
    device. When enabled, the AudioEngine writes its mixed output into this
    sink so that applications like Discord or games can capture LaunchForge
    audio as if it were coming from a microphone.

    Requires ``pactl`` (PulseAudio / PipeWire-Pulse) to be available.
    """

    DEFAULT_SINK_NAME = "LaunchForge_VirtualMic"
    DEFAULT_DESCRIPTION = "LaunchForge Virtual Microphone"

    def __init__(self, sink_name: str = DEFAULT_SINK_NAME,
                 description: str = DEFAULT_DESCRIPTION,
                 samplerate: int = 44100, channels: int = 2):
        self.sink_name = sink_name
        self.description = description
        self.samplerate = samplerate
        self.channels = channels
        self._module_id: Optional[int] = None
        self._stream = None  # sounddevice OutputStream writing to the sink
        self._lock = threading.Lock()
        self._active = False
        self._monitor_source_name = f"{sink_name}.monitor"

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    @staticmethod
    def is_supported() -> bool:
        """Return True if pactl is available on this system."""
        try:
            result = subprocess.run(
                ["pactl", "--version"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    # ------------------------------------------------------------------
    # Sink lifecycle
    # ------------------------------------------------------------------

    def create(self) -> bool:
        """Load a null-sink PulseAudio module and return True on success."""
        if self._module_id is not None:
            return True  # already loaded

        try:
            result = subprocess.run(
                [
                    "pactl", "load-module", "module-null-sink",
                    f"sink_name={self.sink_name}",
                    f"sink_properties=device.description=\"{self.description}\"",
                    f"rate={self.samplerate}",
                    f"channels={self.channels}",
                    "format=float32le",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                self._module_id = int(result.stdout.strip())
                print(f"[VirtualMic] Created sink (module {self._module_id})")
                return True
            else:
                print(f"[VirtualMic] pactl error: {result.stderr.strip()}")
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as e:
            print(f"[VirtualMic] Failed to create sink: {e}")
            return False

    def destroy(self) -> bool:
        """Unload the null-sink module. Returns True on success."""
        self.stop_stream()
        if self._module_id is None:
            return True

        try:
            result = subprocess.run(
                ["pactl", "unload-module", str(self._module_id)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                print(f"[VirtualMic] Destroyed sink (module {self._module_id})")
                self._module_id = None
                return True
            else:
                print(f"[VirtualMic] Unload error: {result.stderr.strip()}")
                return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"[VirtualMic] Failed to destroy sink: {e}")
            return False

    # ------------------------------------------------------------------
    # Audio stream that feeds the virtual sink
    # ------------------------------------------------------------------

    def start_stream(self, audio_callback):
        """Open an OutputStream targeting the virtual sink.

        ``audio_callback`` must have the same signature as a sounddevice
        OutputStream callback: ``(outdata, frames, time_info, status)``.
        The caller is responsible for filling *outdata* with the mixed audio.
        """
        if not SD_AVAILABLE:
            print("[VirtualMic] sounddevice not available")
            return
        if self._module_id is None:
            print("[VirtualMic] Sink not created — call create() first")
            return

        # Find the device index for our virtual sink
        sink_index = self._find_sink_device_index()
        if sink_index is None:
            print("[VirtualMic] Could not find virtual sink device in sounddevice")
            return

        with self._lock:
            if self._stream is not None:
                return  # already running

            try:
                self._stream = sd.OutputStream(
                    samplerate=self.samplerate,
                    blocksize=512,
                    channels=self.channels,
                    dtype="float32",
                    device=sink_index,
                    callback=audio_callback,
                )
                self._stream.start()
                self._active = True
                print(f"[VirtualMic] Stream started (device {sink_index})")
            except Exception as e:
                print(f"[VirtualMic] Stream start error: {e}")
                self._stream = None

    def stop_stream(self):
        """Stop the output stream to the virtual sink."""
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
                self._active = False
                print("[VirtualMic] Stream stopped")

    @property
    def active(self) -> bool:
        return self._active

    @property
    def monitor_source(self) -> str:
        """PulseAudio monitor source name for other apps to use as mic."""
        return self._monitor_source_name

    # ------------------------------------------------------------------
    # List available virtual sinks already loaded in the system
    # ------------------------------------------------------------------

    @staticmethod
    def list_virtual_sinks() -> List[Dict]:
        """Return a list of currently loaded null-sink modules."""
        sinks: List[Dict] = []
        try:
            result = subprocess.run(
                ["pactl", "list", "sinks", "short"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        sinks.append({
                            "index": parts[0],
                            "name": parts[1],
                            "driver": parts[2] if len(parts) > 2 else "",
                        })
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return sinks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_sink_device_index(self) -> Optional[int]:
        """Find the sounddevice output index matching our virtual sink."""
        if not SD_AVAILABLE:
            return None
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["max_output_channels"] > 0 and self.sink_name in d["name"]:
                return i
        return None

    def get_monitor_device_index(self) -> Optional[int]:
        """Find the sounddevice *input* index for our monitor source.

        Other parts of the app (or the user) can select this as their
        microphone input device.
        """
        if not SD_AVAILABLE:
            return None
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and self.sink_name in d["name"]:
                return i
        return None


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

        # Virtual microphone sink — shares mixed audio with a virtual device
        self._virtual_mic: Optional[VirtualMicSink] = None
        self._virtual_mic_enabled = False
        # Ring buffer exchanged between the main callback and the sink callback
        self._vmic_buffer: Optional[np.ndarray] = None
        self._vmic_write_pos = 0
        self._vmic_read_pos = 0
        self._vmic_buf_lock = threading.Lock()

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

        try:
            self._stream = sd.OutputStream(
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                channels=2,
                dtype="float32",
                callback=self._audio_callback,
            )
            self._stream.start()
            print("[Audio] Engine started")
        except Exception as e:
            print(f"[Audio] Failed to start: {e}")

    def restart(self):
        """Restart the audio output stream (e.g., after device change)."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.start()

    def stop(self):
        """Stop the audio engine."""
        self.disable_virtual_mic()
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

    # --- Virtual Microphone Sink ---

    def enable_virtual_mic(self) -> bool:
        """Create a virtual mic sink and start routing mixed audio to it.

        Returns True if the virtual mic was successfully enabled.
        """
        if self._virtual_mic_enabled:
            return True

        if not VirtualMicSink.is_supported():
            print("[Audio] Virtual mic not supported (pactl not found)")
            return False

        vmic = VirtualMicSink(
            samplerate=self.samplerate,
            channels=2,
        )
        if not vmic.create():
            return False

        # Allocate a ring buffer (~1 second of audio)
        buf_frames = self.samplerate
        self._vmic_buffer = np.zeros((buf_frames, 2), dtype=np.float32)
        self._vmic_write_pos = 0
        self._vmic_read_pos = 0

        vmic.start_stream(self._vmic_playback_callback)
        self._virtual_mic = vmic
        self._virtual_mic_enabled = True
        print("[Audio] Virtual microphone enabled")
        return True

    def disable_virtual_mic(self):
        """Tear down the virtual mic sink."""
        if not self._virtual_mic_enabled:
            return
        self._virtual_mic_enabled = False
        if self._virtual_mic is not None:
            self._virtual_mic.stop_stream()
            self._virtual_mic.destroy()
            self._virtual_mic = None
        self._vmic_buffer = None
        print("[Audio] Virtual microphone disabled")

    @property
    def virtual_mic_active(self) -> bool:
        return self._virtual_mic_enabled

    @property
    def virtual_mic_monitor_source(self) -> Optional[str]:
        """Name of the PulseAudio monitor source for the virtual mic."""
        if self._virtual_mic is not None:
            return self._virtual_mic.monitor_source
        return None

    def _vmic_playback_callback(self, outdata, frames, time_info, status):
        """Callback for the OutputStream that feeds the virtual sink."""
        if not self._virtual_mic_enabled or self._vmic_buffer is None:
            outdata[:] = np.zeros((frames, 2), dtype=np.float32)
            return

        with self._vmic_buf_lock:
            buf_len = len(self._vmic_buffer)
            for i in range(frames):
                outdata[i] = self._vmic_buffer[self._vmic_read_pos]
                self._vmic_read_pos = (self._vmic_read_pos + 1) % buf_len

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

        # Feed virtual mic ring buffer if active
        if self._virtual_mic_enabled and self._vmic_buffer is not None:
            with self._vmic_buf_lock:
                buf_len = len(self._vmic_buffer)
                for i in range(frames):
                    self._vmic_buffer[self._vmic_write_pos] = output[i]
                    self._vmic_write_pos = (self._vmic_write_pos + 1) % buf_len

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

    def start_recording(self, input_device: Optional[int] = None, channels: int = 1):
        """Start recording from microphone."""
        if self._recording:
            return
        if not SD_AVAILABLE:
            print("[Audio] sounddevice not available for recording")
            return

        self._record_buffer = []
        self._recording = True

        kwargs = {
            "samplerate": self.samplerate,
            "blocksize": self.blocksize,
            "channels": channels,
            "dtype": "float32",
            "callback": self._record_callback,
        }
        if input_device is not None:
            kwargs["device"] = input_device

        try:
            self._record_stream = sd.InputStream(**kwargs)
            self._record_stream.start()
            print("[Audio] Recording started")
        except Exception as e:
            print(f"[Audio] Record start error: {e}")
            self._recording = False

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
