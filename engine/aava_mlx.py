"""Aava Persian TTS (KEYHAN-A/aava-tts-persian-3b, Apache-2.0) on Apple MLX.

Aava is Orpheus-3B (Llama) fine-tuned on ~2,500 h of Persian speech: the
language model writes SNAC audio codes, SNAC turns them into 24 kHz audio.

Two things make it usable for dubbing on a Mac:
- speed: 4-bit MLX weights, and many sentences generated together in one
  batch (decoding is memory-bound, so a batch of 6 costs little more than 1);
- a steady voice: Aava was trained on many speakers under one label, so each
  sentence comes out in a random voice. Putting one of its own earlier
  utterances in front of the prompt (a previous "turn" with that voice's
  audio codes) makes it continue in that voice; the pitch of every result is
  checked and a take in the wrong voice is regenerated.

    python aava_mlx.py convert <hf_dir> <mlx_dir> [bits]
    python aava_mlx.py say <mlx_dir> "متن فارسی" out.wav [voice]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

# Orpheus special tokens (from the Aava reference inference script)
SOH, EOH, SOA, SOS, EOS, EOA = 128259, 128260, 128261, 128257, 128258, 128262
AUDIO_OFFSET = 128266
CODEBOOK_OFFSETS = (0, 4096, 8192)
POS_TO_CB = (0, 1, 2, 2, 1, 2, 2)  # 7 tokens per frame -> SNAC codebooks
SAMPLE_RATE = 24000
LETTERS_PER_SECOND = 13.0          # typical Persian speaking rate


def median_f0(wav: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """Median pitch of the voiced frames (autocorrelation), 0 when unvoiced."""
    frame, hop = int(0.04 * sr), int(0.02 * sr)
    lo, hi = int(sr / 400), int(sr / 60)
    f0s = []
    for i in range(0, len(wav) - frame, hop):
        x = wav[i:i + frame]
        if float(np.sqrt((x ** 2).mean())) < 0.03:
            continue
        x = x - x.mean()
        spec = np.fft.rfft(x, n=2 * frame)
        ac = np.fft.irfft(spec * np.conj(spec))[:frame]
        if ac[0] <= 0:
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] / ac[0] > 0.35:
            f0s.append(sr / lag)
    return float(np.median(f0s)) if len(f0s) >= 5 else 0.0


def gender_of(f0: float) -> str | None:
    if 70 <= f0 <= 160:
        return "male"
    if 175 <= f0 <= 350:
        return "female"
    return None


class Aava:
    def __init__(self, mlx_dir: str, voices_file: str | None = None):
        from mlx_lm import load
        from snac import SNAC

        self.model, self.tokenizer = load(mlx_dir)
        self.snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval()
        # voice name -> {"text": ..., "codes": [...], "f0": ..., "gender": ...}
        self.voices = {}
        if voices_file and Path(voices_file).exists():
            self.voices = json.loads(Path(voices_file).read_text(encoding="utf-8"))

    # ------------------------------------------------------------ prompts
    def _turn(self, text: str, speaker="aava"):
        return [SOH] + self.tokenizer.encode(f"{speaker}: {text}", add_special_tokens=False) + [EOH, SOA, SOS]

    def prompt(self, text: str, voice: str | None = None):
        ids = []
        ref = self.voices.get(voice) if voice else None
        if ref:  # a finished earlier turn in this voice, then the new sentence
            ids += self._turn(ref["text"]) + list(ref["codes"]) + [EOS, EOA]
        return ids + self._turn(text)

    # ------------------------------------------------------------ generation
    def codes_batch(self, texts, voice=None, temperature=0.7, top_p=0.95,
                    repetition_penalty=1.1, max_tokens=2400):
        """Several sentences at once -> list of audio-token lists."""
        import mlx.core as mx  # noqa: F401
        from mlx_lm.generate import BatchGenerator
        from mlx_lm.sample_utils import make_logits_processors, make_sampler

        gen = BatchGenerator(self.model, max_tokens=max_tokens, stop_tokens=[[EOS], [EOA]],
                             sampler=make_sampler(temp=temperature, top_p=top_p),
                             completion_batch_size=max(8, len(texts)), prefill_batch_size=8)
        procs = [make_logits_processors(repetition_penalty=repetition_penalty, repetition_context_size=64)
                 for _ in texts]
        uids = gen.insert([self.prompt(t, voice) for t in texts], [max_tokens] * len(texts),
                          logits_processors=procs)
        out = {u: [] for u in uids}
        try:
            while responses := gen.next_generated():
                for r in responses:
                    if r.finish_reason != "stop" and r.token >= AUDIO_OFFSET:
                        out[r.uid].append(int(r.token))
        finally:
            gen.close()
        return [out[u] for u in uids]

    def decode(self, audio_tokens):
        import torch

        n = len(audio_tokens) // 7
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        layers = ([], [], [])
        for i in range(n):
            for j in range(7):
                cb = POS_TO_CB[j]
                layers[cb].append(audio_tokens[7 * i + j] - AUDIO_OFFSET - CODEBOOK_OFFSETS[cb])
        # SNAC codebooks hold 4096 entries; a stray token would crash the decoder
        codes = [torch.tensor([l], dtype=torch.long).clamp(0, 4095) for l in layers]
        with torch.no_grad():
            wav = self.snac.decode(codes).squeeze().numpy()
        peak = float(np.abs(wav).max()) if wav.size else 0.0
        return (wav / peak * 0.95).astype(np.float32) if peak > 0 else wav.astype(np.float32)

    def acceptable(self, text: str, wav: np.ndarray, voice: str | None) -> bool:
        """Right length for the text, and (for a named voice) the right register."""
        expected = max(0.8, sum(ch.isalpha() for ch in text) / LETTERS_PER_SECOND)
        if not 0.5 <= (len(wav) / SAMPLE_RATE) / expected <= 2.2:
            return False
        ref = self.voices.get(voice) if voice else None
        if ref and len(wav) > SAMPLE_RATE:  # very short takes have too little voiced audio to judge
            g = gender_of(median_f0(wav))
            if g is not None and g != ref["gender"]:
                return False
        return True

    def say_many(self, texts, voice=None, attempts=3):
        """Batch-synthesize sentences; regenerate the takes that fail the checks."""
        results = [None] * len(texts)
        pending = list(range(len(texts)))
        for attempt in range(attempts):
            if not pending:
                break
            takes = self.codes_batch([texts[i] for i in pending], voice)
            still = []
            for i, toks in zip(pending, takes):
                wav = self.decode(toks)
                if self.acceptable(texts[i], wav, voice) or attempt == attempts - 1:
                    if results[i] is None or len(wav):
                        results[i] = wav
                else:
                    results[i] = wav if results[i] is None else results[i]
                    still.append(i)
            pending = still
        return results

    def say(self, text: str, voice=None):
        return self.say_many([text], voice)[0]


def main():
    cmd = sys.argv[1]
    if cmd == "convert":
        from mlx_lm import convert

        bits = int(sys.argv[4]) if len(sys.argv) > 4 else 4
        convert(sys.argv[2], mlx_path=sys.argv[3], quantize=True, q_bits=bits, q_group_size=64)
    elif cmd == "say":
        import soundfile as sf

        mlx_dir = sys.argv[2]
        a = Aava(mlx_dir, str(Path(mlx_dir) / "voices.json"))
        t = time.time()
        wav = a.say(sys.argv[3], sys.argv[5] if len(sys.argv) > 5 else None)
        sf.write(sys.argv[4], wav, SAMPLE_RATE)
        print(f"{len(wav) / SAMPLE_RATE:.2f}s audio in {time.time() - t:.2f}s, f0 {median_f0(wav):.0f} Hz")


if __name__ == "__main__":
    main()
