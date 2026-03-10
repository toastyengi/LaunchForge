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
