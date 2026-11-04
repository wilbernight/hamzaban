"""One-time Aava setup (run by install.sh with HAMZABAN_AAVA=1, inside venv-aava)."""
import os
import sys

from huggingface_hub import snapshot_download
from mlx_lm import convert

app = sys.argv[1]
os.environ.setdefault("HF_HOME", os.path.join(app, "hf"))
snapshot_download("KEYHAN-A/aava-tts-persian-3b", local_dir=f"{app}/aava-model",
                  allow_patterns=["*.json", "*.safetensors", "*.jinja"])
convert(f"{app}/aava-model", mlx_path=f"{app}/aava-mlx-4", quantize=True, q_bits=4, q_group_size=64)

from snac import SNAC  # noqa: E402  (codec used by the worker, cached for offline use)

SNAC.from_pretrained("hubertsiuzdak/snac_24khz")
print("aava ready")
