"""
Audio engine for playback, recording, and device management.
Supports WAV, OGG, and MP3 (via pydub/ffmpeg).
"""

import os
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


class VirtualAudioDevice:
    """Manages a virtual audio sink via PipeWire/PulseAudio for feeding audio to other apps.

    Creates a null-sink module whose monitor source appears as a microphone
    input in Discord, games, and other applications. Works on Arch Linux
    with either PipeWire (pipewire-pulse) or PulseAudio.
    """

    SINK_NAME = "LaunchForge_Virtual_Mic"
    SINK_DESCRIPTION = "LaunchForge Virtual Mic"

    def __init__(self):
        self._module_id: Optional[int] = None
        self._sink_device_index: Optional[int] = None
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def sink_device_index(self) -> Optional[int]:
        """sounddevice index of the virtual sink (output side)."""
        return self._sink_device_index

    @staticmethod
    def is_available() -> bool:
        """Check if pactl is available on the system."""
        import shutil
        return shutil.which("pactl") is not None

    def create(self) -> bool:
        """Load the null-sink module and return True on success."""
        if self._enabled:
            return True
        if not self.is_available():
            print("[VirtualAudio] pactl not found – install pipewire-pulse or pulseaudio")
            return False

        import subprocess
        try:
            result = subprocess.run(
                [
                    "pactl", "load-module", "module-null-sink",
                    f"sink_name={self.SINK_NAME}",
                    f"sink_properties=device.description={self.SINK_DESCRIPTION}",
                    "rate=44100", "channels=2",
                ],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                print(f"[VirtualAudio] pactl error: {result.stderr.strip()}")
                return False

            self._module_id = int(result.stdout.strip())
            self._enabled = True
            self._resolve_device_index()
            print(f"[VirtualAudio] Created sink (module {self._module_id}, "
                  f"device index {self._sink_device_index})")
            return True
        except Exception as e:
            print(f"[VirtualAudio] Failed to create sink: {e}")
            return False

    def destroy(self) -> bool:
        """Unload the null-sink module."""
        if not self._enabled or self._module_id is None:
            self._enabled = False
            return True

        import subprocess
        try:
            subprocess.run(
                ["pactl", "unload-module", str(self._module_id)],
                capture_output=True, text=True, timeout=5,
            )
            print(f"[VirtualAudio] Removed sink (module {self._module_id})")
        except Exception as e:
            print(f"[VirtualAudio] Failed to remove sink: {e}")

        self._module_id = None
        self._sink_device_index = None
        self._enabled = False
        return True

    def _resolve_device_index(self):
        """Find the sounddevice index matching our virtual sink."""
        if not SD_AVAILABLE:
            return
        # Give PipeWire/PA a moment to register the new device
        import time
        time.sleep(0.3)
        # Re-query sounddevice so it sees the new sink
        sd._terminate()
        sd._initialize()
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if self.SINK_NAME in d["name"] and d["max_output_channels"] > 0:
                self._sink_device_index = i
                return
        print("[VirtualAudio] Warning: sink created but not found in sounddevice list")


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
        # Virtual audio device support
        self.virtual_device = VirtualAudioDevice()
        self._virtual_stream = None
        self._last_mix = None

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
        self._stop_virtual_stream()
        self.start()
        if self.virtual_device.enabled:
            self._start_virtual_stream()

    def stop(self):
        """Stop the audio engine."""
        self.stop_all()
        self._stop_virtual_stream()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._record_stream:
            self._record_stream.stop()
            self._record_stream.close()
            self._record_stream = None
        print("[Audio] Engine stopped")

    # --- Virtual Audio Device ---

    def enable_virtual_device(self) -> bool:
        """Create the virtual sink and start mirroring audio to it."""
        if not self.virtual_device.create():
            return False
        self._start_virtual_stream()
        return True

    def disable_virtual_device(self):
        """Stop mirroring and destroy the virtual sink."""
        self._stop_virtual_stream()
        self.virtual_device.destroy()

    def _start_virtual_stream(self):
        """Open a secondary output stream targeting the virtual sink."""
        if not SD_AVAILABLE or self._virtual_stream is not None:
            return
        dev_idx = self.virtual_device.sink_device_index
        if dev_idx is None:
            print("[VirtualAudio] No device index – cannot open stream")
            return
        try:
            self._virtual_stream = sd.OutputStream(
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                channels=2,
                dtype="float32",
                device=dev_idx,
                callback=self._virtual_audio_callback,
            )
            self._virtual_stream.start()
            print("[VirtualAudio] Mirror stream started")
        except Exception as e:
            print(f"[VirtualAudio] Failed to start mirror stream: {e}")
            self._virtual_stream = None

    def _stop_virtual_stream(self):
        """Close the virtual sink output stream."""
        if self._virtual_stream:
            try:
                self._virtual_stream.stop()
                self._virtual_stream.close()
            except Exception:
                pass
            self._virtual_stream = None

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
        # Share the mixed buffer for the virtual sink mirror stream
        self._last_mix = output.copy()

    def _virtual_audio_callback(self, outdata, frames, time_info, status):
        """Mirror the same mix to the virtual sink so other apps can capture it."""
        buf = self._last_mix
        if buf is not None and len(buf) >= frames:
            outdata[:] = buf[:frames]
        else:
            outdata[:] = 0

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
