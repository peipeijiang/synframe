"""Batch similarity from difference hashes of sampled frames (ffmpeg only)."""
import subprocess
from itertools import combinations


def _dhash(path, t, size=8):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=%d:%d,format=gray" % (size + 1, size), "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    if len(raw) < (size + 1) * size:
        raise ValueError("could not sample frame at %.2f in %s" % (t, path))
    bits = 0
    for y in range(size):
        row = raw[y * (size + 1):(y + 1) * (size + 1)]
        for x in range(size):
            bits = (bits << 1) | (row[x] > row[x + 1])
    return bits


def fingerprint(path, duration, opening_sec=3.0, step=0.5, body_samples=12):
    opening = [_dhash(path, t / 1000.0) for t in range(0, int(opening_sec * 1000), int(step * 1000))]
    whole = [_dhash(path, duration * (i + 0.5) / body_samples) for i in range(body_samples)]
    return {"opening": opening, "whole": whole}


def _distance(a, b):
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b)) / float(min(len(a), len(b)))


def compare(prints, opening_threshold=12.0):
    """Mean Hamming distance (0-64) between every pair; low opening distance means a repeated hook."""
    pairs = []
    for (na, a), (nb, b) in combinations(sorted(prints.items()), 2):
        o, w = _distance(a["opening"], b["opening"]), _distance(a["whole"], b["whole"])
        pairs.append({"a": na, "b": nb, "opening_distance": round(o, 2), "whole_distance": round(w, 2),
                      "repeated_opening": o < opening_threshold})
    return {"opening_threshold": opening_threshold, "threshold_status": "uncalibrated",
            "pairs": pairs, "flagged": [p for p in pairs if p["repeated_opening"]]}
