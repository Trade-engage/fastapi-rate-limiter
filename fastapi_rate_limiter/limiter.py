"""Generic async sliding-window rate limiter backed by Redis.

This module provides a minimal helper to determine whether an action
associated with a given `key` should be rate limited, using a Redis ZSET
to implement a sliding time window. It is framework-agnostic and can be
used from web servers (e.g., FastAPI) or any async context.

How it works (per call):
- Remove timestamps older than the configured `window` from the sorted set
  for the computed Redis key.
- Add the current timestamp as both the member and the score.
- Count the remaining members and compare against `limit`.

Failure behavior is controlled via the `fail_mode` argument of
`is_limited` and can be:
- "open" (default): do not limit when Redis operations fail
- "closed": treat failures as limited
- "raise": propagate the Redis exception
"""

from difflib import restore
from telnetlib import OLD_ENVIRON
import time
from typing import Literal

import redis.asyncio as redis


async def is_limited(
    redis_client: redis.Redis,
    key: str,
    limit: int,
    window: int,
    namespace_prefix: str,
    fail_mode: Literal["open", "closed", "raise"] = "open",
) -> tuple[bool, int, int, int]:
    """Check whether the rate limit has been exceeded for a given key.

    This implements a sliding-window rate limit using a Redis ZSET. Each call
    records the current timestamp and prunes entries older than `window`
    seconds. The number of remaining entries is compared to `limit`.

    Args:
        redis_client: An instance of ``redis.asyncio.Redis``.
        key: Identifier to rate-limit (e.g., user ID, IP address, route name).
        limit: Maximum number of allowed events within the window.
        window: Window size in seconds for the sliding window.
        namespace_prefix: Prefix used to build the Redis key for namespacing.
        fail_mode: One of "open", "closed", or "raise". With "open"
            (default), Redis errors result in allowing the request (not
            limited). With "closed", Redis errors result in treating the
            request as limited. With "raise", Redis errors result in raising
            the exception.
    Returns:
        A 4-tuple: (is_limited, limit, remaining, reset_time)
        - is_limited (bool): True if current count exceeds ``limit``; else False
        - limit (int): The configured limit value
        - remaining (int): How many requests remain in the current window
        - reset_time (int): Unix timestamp when the window resets for this key

    Notes:
        - On Redis failures, behavior depends on ``fail_mode``: "open" allows,
          "closed" limits, and "raise" propagates the exception.
        - Entries are trimmed on each call and the key's TTL is set to the
          window length.
    """
    new_key = f"{namespace_prefix}:{key}"
    now = time.time()
    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(new_key, 0, now - window)
    pipe.zadd(new_key, {now: now})
    pipe.zcard(new_key)
    pipe.expire(new_key, window)
    pipe.zrange(new_key, 0, 0, withscores=True)
    reset_time = int(now + window)
    try:
        _, _, count, _, oldest_timestamp = await pipe.execute()
        if oldest_timestamp:
            oldest = int(oldest_timestamp[0][1])
            reset_time = int(oldest + window)
    except Exception as e:
        if fail_mode == "raise":
            raise

        if fail_mode == "closed":
            return True, limit, 0, int(now + window)

        if fail_mode == "open":
            return False, limit, limit, reset_time

    if count > limit:
        return True, limit, limit - count, reset_time
    return False, limit, limit - count, reset_time
