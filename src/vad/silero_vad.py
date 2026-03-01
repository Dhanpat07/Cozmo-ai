"""
Voice Activity Detection using Silero VAD
Lightweight, fast, runs on CPU
"""
import logging
import numpy as np
from typing import Optional
import torch

logger = logging.getLogger(__name__)


class SileroVAD:
    """
    Silero VAD - low-latency voice activity detection
    Processes 20ms frames, returns speech probability
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._model = None
        self._loaded = False
        self._load_model()

    def _load_model(self):
        try:
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False
            )
            self._model = model
            self._loaded = True
            logger.info("Silero VAD loaded successfully")
        except Exception as e:
            logger.warning("Silero VAD load failed (%s), using energy-based fallback", e)
            self._loaded = False

    def is_speech(self, audio_frame: np.ndarray) -> float:
        """
        Returns speech probability [0.0, 1.0] for a 20ms audio frame.
        audio_frame: float32 numpy array, values in [-1, 1]
        """
        if self._loaded and self._model is not None:
            try:
                tensor = torch.FloatTensor(audio_frame)
                with torch.no_grad():
                    prob = self._model(tensor, self.sample_rate).item()
                return prob
            except Exception:
                pass

        # Fallback: energy-based VAD
        return self._energy_vad(audio_frame)

    def _energy_vad(self, audio_frame: np.ndarray) -> float:
        """Simple energy-based speech detection as fallback"""
        rms = np.sqrt(np.mean(audio_frame ** 2))
        # Typical speech RMS is 0.01-0.1, noise is <0.005
        if rms > 0.02:
            return 0.9
        elif rms > 0.01:
            return 0.5
        else:
            return 0.1

    def reset(self):
        """Reset VAD state between utterances"""
        if self._loaded and self._model is not None:
            try:
                self._model.reset_states()
            except Exception:
                pass
