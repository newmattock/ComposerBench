"""RIFF/WAVE decoding to normalized, frame-by-channel float64 samples."""
from pathlib import Path
import struct
import numpy as np


def write_float_wav(path: Path | str, samples: np.ndarray, sample_rate: int) -> Path:
    """Write finite frame-by-channel IEEE float audio without clipping or overwriting."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 2 or samples.shape[1] not in (1, 2) or not samples.size:
        raise ValueError('nonempty mono/stereo frame-by-channel audio required')
    if not np.isfinite(samples).all() or sample_rate < 1:
        raise ValueError('finite samples and positive sample rate required')
    channels = samples.shape[1]
    data = samples.astype('<f4').tobytes()
    fmt = struct.pack('<HHIIHH', 3, channels, sample_rate,
                      sample_rate * channels * 4, channels * 4, 32)
    payload = b'fmt ' + struct.pack('<I', len(fmt)) + fmt
    payload += b'data' + struct.pack('<I', len(data)) + data
    with Path(path).open('xb') as output:
        output.write(b'RIFF' + struct.pack('<I', len(payload) + 4) + b'WAVE' + payload)
    return Path(path)


def read_wav(path: Path | str) -> tuple[np.ndarray, int]:
    path = Path(path)
    if not path.exists():
        from composerbench.errors import EntityNotFound
        raise EntityNotFound('audio-file', str(path))
    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('expected little-endian RIFF/WAVE')
    fmt = None
    chunks = []
    pos = 12
    while pos + 8 <= len(raw):
        kind, size = struct.unpack_from('<4sI', raw, pos)
        pos += 8
        if pos + size > len(raw):
            raise ValueError('truncated WAV chunk')
        chunk = raw[pos:pos+size]
        if kind == b'fmt ': fmt = chunk
        if kind == b'data': chunks.append(chunk)
        pos += size + (size & 1)
    if fmt is None or len(fmt) < 16 or not chunks:
        raise ValueError('WAV requires fmt and data chunks')
    tag, channels, sr, _, align, bits = struct.unpack_from('<HHIIHH', fmt)
    if tag == 0xfffe:
        if len(fmt) < 40 or fmt[26:40] != bytes.fromhex('000000001000800000aa00389b71'):
            raise ValueError('unsupported extensible WAV format')
        tag = struct.unpack_from('<H', fmt, 24)[0]
    if channels < 1 or sr < 1 or align != channels * (bits // 8):
        raise ValueError('invalid WAV format metadata')
    data = b''.join(chunks)
    if len(data) % align:
        raise ValueError('incomplete WAV frame')
    if tag == 1 and bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        n = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        samples = ((n ^ 0x800000) - 0x800000).astype(np.float64) / 8388608
    elif tag == 1 and bits in (16, 32):
        samples = np.frombuffer(data, dtype=f'<i{bits//8}').astype(np.float64) / 2**(bits-1)
    elif tag == 1 and bits == 8:
        samples = (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128) / 128
    elif tag == 3 and bits in (32, 64):
        samples = np.frombuffer(data, dtype=f'<f{bits//8}').astype(np.float64)
    else:
        raise ValueError(f'unsupported WAV format tag={tag}, bits={bits}')
    if not np.isfinite(samples).all():
        raise ValueError('audio contains NaN or infinity')
    return samples.reshape(-1, channels), sr
