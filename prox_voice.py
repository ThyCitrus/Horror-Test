"""Low-bandwidth push-to-talk voice transport and playback."""

import array
import math
import queue
import random
import socket
import threading
import time
import uuid

SAMPLE_RATE = 8000
FRAME_SAMPLES = 160
FRAME_BYTES = FRAME_SAMPLES
MAX_QUEUED_FRAMES = 8


def encode_mulaw(pcm):
    """Encode native-endian signed 16-bit mono PCM as G.711 mu-law."""
    samples = array.array("h")
    samples.frombytes(pcm)
    if samples.itemsize != 2:
        raise RuntimeError("This platform does not support 16-bit PCM samples.")
    encoded = bytearray(len(samples))
    for index, sample in enumerate(samples):
        sign = 0x80 if sample < 0 else 0
        magnitude = min(32635, abs(sample)) + 132
        exponent = 7
        mask = 0x4000
        while exponent and not magnitude & mask:
            exponent -= 1
            mask >>= 1
        mantissa = (magnitude >> (exponent + 3)) & 0x0F
        encoded[index] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(encoded)


def decode_mulaw(data):
    """Decode G.711 mu-law bytes to native-endian signed 16-bit mono PCM."""
    samples = array.array("h")
    for value in data:
        value = ~value & 0xFF
        sign = value & 0x80
        exponent = (value >> 4) & 0x07
        mantissa = value & 0x0F
        magnitude = ((mantissa << 3) + 132) << exponent
        sample = magnitude - 132
        samples.append(-sample if sign else sample)
    return samples.tobytes()


class ProximityVoiceChat:
    def __init__(self, host, port, client_id, token):
        try:
            import sounddevice
        except ImportError as exc:
            raise RuntimeError(
                "Voice chat needs the optional sounddevice package. "
                "Install it with: pip install -r requirements-voice.txt"
            ) from exc

        try:
            self._client_uuid = uuid.UUID(client_id).bytes
        except (ValueError, AttributeError) as exc:
            raise ValueError("Voice chat received an invalid client identity.") from exc
        try:
            self._token = bytes.fromhex(token)
        except (TypeError, ValueError) as exc:
            raise ValueError("Voice chat received an invalid session token.") from exc
        if len(self._token) != 16:
            raise ValueError("Voice chat session token has an invalid length.")

        self._sounddevice = sounddevice
        self._lock = threading.Lock()
        self._frames = {}
        self._speaker_levels = {}
        self._filter_state = {}
        self._noise_rng = {}
        self._feedback_sounds = {}
        self._inbox = queue.Queue()
        self._running = True
        self._transmitting = False
        self._muted = False
        self._connected = False
        self._closed = False
        self._failure = None
        self._connection_started = time.monotonic()
        self._timeout_reported = False
        self._last_handshake = 0.0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._input = None
        self._output = None
        try:
            self._sock.connect((host, port))
            self._input = sounddevice.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=FRAME_SAMPLES,
                channels=1,
                dtype="int16",
                callback=self._capture,
            )
            self._output = sounddevice.RawOutputStream(
                samplerate=SAMPLE_RATE,
                blocksize=FRAME_SAMPLES,
                channels=2,
                dtype="int16",
                callback=self._playback,
            )
            self._output.start()
            threading.Thread(target=self._receive_loop, daemon=True).start()
            self._send_handshake()
        except Exception:
            self.close()
            raise

    @property
    def transmitting(self):
        return self._transmitting

    @property
    def muted(self):
        return self._muted

    @property
    def connected(self):
        return self._connected

    @property
    def failure(self):
        return self._failure

    def set_muted(self, muted):
        self._muted = bool(muted)

    def _play_feedback(self, enabled):
        try:
            import pygame
        except ImportError:
            return
        mixer_format = pygame.mixer.get_init()
        if not mixer_format or mixer_format[1] != -16:
            return
        sample_rate, _, channels = mixer_format
        sound = self._feedback_sounds.get(enabled)
        if sound is None:
            sample_count = int(sample_rate * 0.11)
            samples = array.array("h")
            start_pitch, end_pitch = (520, 1450) if enabled else (1450, 310)
            rng = random.Random(enabled)
            for index in range(sample_count):
                progress = index / max(1, sample_count - 1)
                pitch = start_pitch + (end_pitch - start_pitch) * progress
                envelope = min(1.0, index / 180, (sample_count - index) / 800)
                tone = math.sin(math.tau * pitch * index / sample_rate)
                noise = rng.uniform(-1.0, 1.0) * (0.12 if not enabled else 0.03)
                sample = int(32767 * 0.32 * envelope * (tone * 0.78 + noise))
                samples.extend([sample] * channels)
            sound = pygame.mixer.Sound(buffer=samples.tobytes())
            sound.set_volume(0.42)
            self._feedback_sounds[enabled] = sound
        sound.play()

    def start_talking(self):
        if not self._running or self._transmitting:
            return
        try:
            self._input.start()
        except Exception as exc:
            raise RuntimeError(f"Could not start microphone capture: {exc}") from exc
        self._transmitting = True
        self._play_feedback(True)

    def stop_talking(self):
        if self._transmitting:
            self._transmitting = False
            try:
                self._input.stop()
            except Exception as exc:
                raise RuntimeError(f"Could not stop microphone capture: {exc}") from exc
            self._play_feedback(False)

    def set_speaker_levels(self, levels):
        with self._lock:
            self._speaker_levels = dict(levels)

    def _send_handshake(self):
        try:
            self._sock.send(b"H" + self._client_uuid + self._token)
        except OSError:
            self._failure = "Could not reach the voice relay."
            self._inbox.put(("error", self._failure))
            self._running = False

    def _capture(self, indata, frames, time_info, status):
        if not self._running or not self._transmitting:
            return
        payload = encode_mulaw(indata)
        if not payload:
            return
        try:
            self._sock.send(b"V" + self._client_uuid + payload)
        except OSError:
            pass

    def _receive_loop(self):
        while self._running:
            try:
                packet = self._sock.recv(512)
            except OSError:
                if self._running:
                    self._failure = "Voice connection was interrupted."
                    self._inbox.put(("error", self._failure))
                return
            if len(packet) == 17 and packet[:1] == b"A":
                if packet[1:] == self._client_uuid:
                    self._connected = True
                continue
            if len(packet) < 18 or packet[:1] != b"V":
                continue
            try:
                speaker_id = str(uuid.UUID(bytes=packet[1:17]))
            except ValueError:
                continue
            pcm = decode_mulaw(packet[17:])
            with self._lock:
                frames = self._frames.setdefault(
                    speaker_id, queue.Queue(maxsize=MAX_QUEUED_FRAMES)
                )
            if frames.full():
                try:
                    frames.get_nowait()
                except queue.Empty:
                    pass
            try:
                frames.put_nowait(pcm)
            except queue.Full:
                pass

    def _playback(self, outdata, frames, time_info, status):
        output = array.array("h", [0]) * (frames * 2)
        with self._lock:
            levels = dict(self._speaker_levels)
            audio_frames = {
                speaker_id: audio_queue
                for speaker_id, audio_queue in self._frames.items()
            }
        if self._muted:
            outdata[:] = output.tobytes()
            return
        for speaker_id, audio_queue in audio_frames.items():
            level = levels.get(speaker_id)
            if not level:
                continue
            try:
                pcm = audio_queue.get_nowait()
            except queue.Empty:
                continue
            samples = array.array("h")
            samples.frombytes(pcm)
            volume, pan, muffled = level
            left_gain = volume * (1.0 - max(0.0, pan))
            right_gain = volume * (1.0 + min(0.0, pan))
            filter_state = self._filter_state.get(speaker_id, (0.0, 0.0, 0.0))
            lowpass, previous_lowpass, highpass = filter_state
            rng = self._noise_rng.setdefault(
                speaker_id, random.Random(hash(speaker_id))
            )
            drop_frame = rng.random() < 0.04
            static_burst = rng.random() < 0.18
            sample_count = min(frames, len(samples))
            for index in range(sample_count):
                if drop_frame:
                    sample = 0
                elif static_burst and rng.random() < 0.22:
                    sample = int(rng.uniform(-1.0, 1.0) * 8500)
                else:
                    sample = samples[index]
                    lowpass += 0.72 * (sample - lowpass)
                    highpass = 0.94 * (highpass + lowpass - previous_lowpass)
                    previous_lowpass = lowpass
                    sample = highpass
                    sample = max(-22000, min(22000, int(sample * 1.35)))
                    sample = int(sample / 128) * 128
                    if static_burst:
                        sample += int(rng.uniform(-1.0, 1.0) * 3000)
                if muffled:
                    sample = int(sample * 0.68 + lowpass * 0.12)
                left_index = index * 2
                output[left_index] += int(sample * left_gain)
                output[left_index + 1] += int(sample * right_gain)
            self._filter_state[speaker_id] = (
                lowpass,
                previous_lowpass,
                highpass,
            )
        for index, sample in enumerate(output):
            output[index] = max(-32768, min(32767, sample))
        outdata[:] = output.tobytes()

    def update(self):
        now = time.monotonic()
        if self._running and now - self._last_handshake >= 1.0:
            self._send_handshake()
            self._last_handshake = now
        messages = []
        if (
            not self._connected
            and not self._timeout_reported
            and now - self._connection_started >= 6.0
        ):
            self._timeout_reported = True
            self._failure = (
                "Voice relay is not responding. Check UDP port forwarding and firewall access."
            )
            messages.append(
                (
                    "error",
                    self._failure,
                )
            )
        while True:
            try:
                messages.append(self._inbox.get_nowait())
            except queue.Empty:
                return messages

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._running = False
        if getattr(self, "_input", None):
            try:
                self._input.stop()
            except Exception:
                pass
            try:
                self._input.close()
            except Exception:
                pass
        if getattr(self, "_output", None):
            try:
                self._output.stop()
            except Exception:
                pass
            try:
                self._output.close()
            except Exception:
                pass
        if getattr(self, "_sock", None):
            try:
                self._sock.close()
            except OSError:
                pass
