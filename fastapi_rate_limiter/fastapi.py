"""FastAPI-compatible Redis-backed rate limiter utilities.

Provides a `RateLimiter` to initialize and close a shared Redis client and
pool, attaching the limiter instance to `FastAPI.state` for downstream
dependencies and middleware to consume.
"""

from typing import Callable, Literal

from .limiter import is_limited

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request, Response


class RateLimiter:
    """Manage Redis connections for request rate limiting.

    This class owns the Redis connection pool and client and exposes lifecycle
    hooks to integrate with FastAPI application startup and shutdown events.
    """

    def __init__(self, redis_url: str) -> None:
        """Create a new `RateLimiter`.

        Args:
            redis_url: Redis connection URL, e.g. ``redis://localhost:6379/0``.
        """
        self.redis_url: str = redis_url
        self.redis_connection_pool: redis.ConnectionPool
        self.redis_client: redis.Redis
        self.namespace_prefix: str = "rate-limiter"

    async def init(
        self, fastapi_app: FastAPI, namespace_prefix: str = "rate-limiter"
    ) -> None:
        """Initialize Redis resources and attach limiter to the FastAPI app.

        Establishes the connection pool and client, validates connectivity with
        ``PING``, and assigns the limiter instance to ``fastapi_app.state.limiter``.

        Args:
            fastapi_app: The FastAPI application instance to bind this limiter to.

        Returns:
            The connected Redis client instance associated with this limiter.
        """
        self.redis_connection_pool = redis.ConnectionPool.from_url(self.redis_url)
        self.redis_client = redis.Redis.from_pool(self.redis_connection_pool)
        self.namespace_prefix = namespace_prefix
        await self.redis_client.ping()
        fastapi_app.state.limiter = self
        fastapi_app.state.limiter_namespace_prefix = namespace_prefix

    async def close(self) -> None:
        """Close the Redis client and connection pool.

        Safe to call during application shutdown; no-op if resources are absent.
        """
        if self.redis_client:
            await self.redis_client.aclose()
        if self.redis_connection_pool:
            await self.redis_connection_pool.aclose()


class RateLimit:
    def __init__(
        self,
        key: str | None,
        limit: int,
        window: int,
        fail_mode: Literal["open", "closed", "raise"] = "open",
        key_function: Callable[[Request], str] | None = None,
    ) -> None:
        self.key_function: Callable[[Request], str] | None = key_function
        self.limit: int = limit
        self.window: int = window
        self.fail_mode: Literal["open", "closed", "raise"] = fail_mode
        self.key: str | None = key

        if not self.key and not self.key_function:
            raise ValueError("Either key or key_function must be provided")

        if self.key and self.key_function:
            raise ValueError("Only one of key or key_function must be provided")

    async def __call__(self, request: Request, response: Response) -> None:
        key = self.key_function(request) if self.key_function else self.key
        limited, limit, remaining, reset_time = await is_limited(
            redis_client=self.redis_client,
            key=key,
            limit=self.limit,
            window=self.window,
            namespace_prefix=self.namespace_prefix,
            fail_mode=self.fail_mode,
        )
        if limited:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(reset_time),
                },
            )

        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        return None
