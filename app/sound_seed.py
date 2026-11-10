"""Framework-independent seed validation shared by sound APIs and isolated workers."""
import secrets


def seed_of(params: dict) -> int:
    seed = params.get("seed")
    if seed is None:
        seed = secrets.randbits(32)
    if type(seed) is not int or not 0 <= seed <= 2 ** 32 - 1:
        raise ValueError("seed is a number: an unsigned 32-bit whole number")
    return seed
