"""Rate limit utilities."""

from redis.asyncio import Redis


async def seconds_until_reset(redis_client: Redis, key: str) -> int:
    """Remaining TTL of a rate limit counter, in seconds, for the Retry-After header.

    Never less than 1: a counter without TTL (-1) or already gone (-2) still means
    the client should wait before retrying, not retry immediately.
    """
    ttl = await redis_client.ttl(key)
    return max(ttl, 1)


async def increment_rate_limit(
    redis_client: Redis, keys: list[str], window_minutes: int
) -> dict[str, int]:
    """Increment the rate limit for the given keys."""
    all_attempts = {}

    for key in keys:
        attempts = await redis_client.incr(key)
        await redis_client.expire(key, 60 * window_minutes, nx=True)
        all_attempts[key] = attempts

    return all_attempts


async def reserve_attempt(
    redis_client: Redis,
    keys: list[str],
    max_attempts: int,
    window_minutes: int,
) -> int | None:
    """
    Reserve an attempt for the given keys.
    If the rate limit is exceeded, return the number of seconds to wait before retrying.
    """

    all_attempts = await increment_rate_limit(redis_client, keys, window_minutes)
    ttls: list[int] = []

    for key, attempts in all_attempts.items():
        if attempts > max_attempts:
            ttl = await seconds_until_reset(redis_client, key)
            ttls.append(ttl)

    if ttls:
        await release_attempt(redis_client, keys)
        return max(ttls)

    return None


async def release_attempt(redis_client: Redis, keys: list[str]) -> None:
    """Decrement the rate limit for the given keys."""

    for key in keys:
        attempt = await redis_client.decr(key)
        if attempt < 0:
            await redis_client.delete(key)
