"""Server-side baselines for FPS anti-cheat. See the repository README."""

from .models import GameProfile
from .pipeline import run_score

__all__ = ["GameProfile", "run_score"]
__version__ = "0.2.0"
