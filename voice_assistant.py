# -*- coding: utf-8 -*-
"""Voice I/O with cancellable microphone capture and local speech synthesis."""
from __future__ import annotations

import array
import math
import threading
import time


def _pcm_rms(data: bytes, sample_width: int) -> float:
    """Calculate RMS energy for PCM samples without optional audioop dependency."""
    if sample_width != 2 or not data:
        return 0.0
    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if not samples:
        return 0.0
    total = sum(int(value) * int(value) for value in samples)
    return math.sqrt(total / len(samples))


def listen_once(
    language: str = "ru-RU",
    timeout: int = 7,
    phrase_time_limit: int = 12,
    stop_event: threading.Event | None = None,
) -> str:
    """Listen only while requested; stop_event closes the microphone promptly.

    Speech recognition is sent to Google's recognition endpoint by the
    SpeechRecognition package. The microphone stream itself is always closed
    before the network request begins.
    """
    try:
        import speech_recognition as sr  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Голосовой модуль не включён в эту сборку. Используй собранный EXE.") from exc

    cancel = stop_event or threading.Event()
    recognizer = sr.Recognizer()
    frames: list[bytes] = []
    started_at: float | None = None
    last_voice_at: float | None = None
    wait_started = time.monotonic()
    sample_rate = 16000
    sample_width = 2

    try:
        with sr.Microphone(sample_rate=sample_rate) as source:
            sample_rate = int(source.SAMPLE_RATE)
            sample_width = int(source.SAMPLE_WIDTH)
            if cancel.is_set():
                raise RuntimeError("Использование микрофона отключено.")
            recognizer.adjust_for_ambient_noise(source, duration=0.35)
            silence_after_speech = 1.0
            while not cancel.is_set():
                now = time.monotonic()
                if started_at is None and now - wait_started > max(1, timeout):
                    raise RuntimeError("Не услышал речь. Нажми кнопку и попробуй ещё раз.")
                if started_at is not None and now - started_at >= max(1, phrase_time_limit):
                    break
                try:
                    chunk = source.stream.read(source.CHUNK)
                except Exception as exc:
                    if cancel.is_set():
                        raise RuntimeError("Запись с микрофона остановлена.") from exc
                    raise RuntimeError(f"Не удалось прочитать микрофон: {exc}") from exc
                frames.append(chunk)
                energy = _pcm_rms(chunk, sample_width)
                threshold = max(100.0, float(recognizer.energy_threshold))
                if energy >= threshold:
                    if started_at is None:
                        started_at = now
                    last_voice_at = now
                elif started_at is not None and last_voice_at is not None:
                    if now - last_voice_at >= silence_after_speech and now - started_at >= 0.35:
                        break
                if sum(map(len, frames)) > sample_rate * sample_width * (phrase_time_limit + 2):
                    break
            if cancel.is_set():
                raise RuntimeError("Запись остановлена. Доступ к микрофону выключен.")
            if started_at is None:
                raise RuntimeError("Речь не обнаружена. Попробуй говорить чуть громче или ближе к микрофону.")
            audio = sr.AudioData(b"".join(frames), sample_rate, sample_width)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Не удалось получить звук с микрофона: {exc}") from exc

    if cancel.is_set():
        raise RuntimeError("Запись остановлена.")
    try:
        return recognizer.recognize_google(audio, language=language).strip()
    except sr.UnknownValueError as exc:
        raise RuntimeError("Не удалось разобрать речь. Попробуй произнести фразу ещё раз.") from exc
    except sr.RequestError as exc:
        raise RuntimeError(f"Сервис распознавания речи недоступен: {exc}") from exc


def speak_text(text: str, rate: int = 175) -> None:
    try:
        import pyttsx3  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Модуль озвучивания отсутствует. Используй собранный EXE.") from exc
    engine = pyttsx3.init()
    engine.setProperty("rate", max(120, min(230, int(rate))))
    engine.say(text[:5000])
    engine.runAndWait()
    engine.stop()
