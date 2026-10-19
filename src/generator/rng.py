"""Named, independent random streams derived from default_rng(42)."""

import zlib

import numpy as np

SEED = 42


def child_rng(name: str) -> np.random.Generator:
    """Return the generator for one component, keyed by its name.

    Each stream is spawned from default_rng(42)'s seed sequence with a
    name-based spawn key, so adding or reordering components never changes
    the draws of existing ones.
    """
    root = np.random.default_rng(SEED).bit_generator.seed_seq
    seq = np.random.SeedSequence(root.entropy, spawn_key=(zlib.crc32(name.encode()),))
    return np.random.default_rng(seq)
