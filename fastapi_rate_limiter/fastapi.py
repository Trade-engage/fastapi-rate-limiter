"""FastAPI-compatible Redis-backed rate limiter utilities.

Provides a `RateLimiter` to initialize and close a shared Redis client and
pool, attaching the limiter instance to `FastAPI.state` for downstream
dependencies and middleware to consume.
"""

from typing import Awaitable, Callable, Literal

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


async def _generate_key_based_on_ip(request: Request) -> str:
    """Generate a rate-limit key based on the client's IP address.

    Prefers the ``X-Forwarded-For`` header when present, using the first IP in
    the list (the original client in typical proxy chains). Falls back to
    ``request.client.host`` when the header is absent. If neither is available
    (e.g., certain test scenarios), returns the string "unknown".

    Args:
        request: The current FastAPI request.

    Returns:
        A best-effort client IP string to use as a rate-limiting key.
    """
    xff = request.headers.get("x-forwarded-for")
    ip: str = "unknown"
    if xff:
        ip = xff.split(",")[0].strip()
    if request.client:
        ip = request.client.host
    return f"ip:{ip}:{request.scope['route'].path}"


class RateLimit:
    """Per-route dependency enforcing a sliding-window rate limit.

    Resolves a rate-limit key using an async ``key_function`` (defaults to
    the client's IP via ``X-Forwarded-For`` or ``request.client.host``),
    checks the current usage in Redis, and either raises an HTTP 429 or sets
    standard rate-limit headers on the response.
    """
    def __init__(
        self,
        limit: int,
        window: int,
        fail_mode: Literal["open", "closed", "raise"] = "open",
        key_function: Callable[[Request], Awaitable[str]] = _generate_key_based_on_ip,
    ) -> None:
        """Create a new rate-limiting dependency.

        Args:
            limit: Maximum number of allowed requests within the window.
            window: Window size in seconds for the sliding window.
            fail_mode: Behavior when Redis fails.
                - "open" (default): allow requests on Redis errors
                - "closed": treat Redis errors as limited
                - "raise": propagate the Redis exception
            key_function: Async function that computes the rate-limit key from
                the ``Request``. Defaults to a function that derives the client
                IP (preferring ``X-Forwarded-For``).
        """
        self.key_function: Callable[[Request], Awaitable[str]] = key_function
        self.limit: int = limit
        self.window: int = window
        self.fail_mode: Literal["open", "closed", "raise"] = fail_mode


    async def __call__(self, request: Request, response: Response) -> None:
        """Apply rate limiting for the current request.

        Resolves the rate-limit key, queries Redis to determine usage, and
        either raises an HTTP 429 or sets rate-limit headers on the response.

        Args:
            request: The incoming FastAPI request.
            response: The outgoing FastAPI response to annotate with headers.

        Raises:
            ValueError: If the application ``RateLimiter`` is not initialized
                on ``request.state.limiter``.
            HTTPException: With status 429 when the request is limited.
            Exception: If ``fail_mode='raise'`` and the Redis operation fails,
                the underlying exception is propagated.

        Returns:
            None. Response headers set on success:
            - ``X-RateLimit-Limit``
            - ``X-RateLimit-Remaining``
            - ``X-RateLimit-Reset`` (unix timestamp)
        """
        key = await self.key_function(request)
        
        limiter: RateLimiter = request.state.limiter
        
        if not limiter:
            raise ValueError("Limiter not found in request state, you must initialize the limiter first")
        
        limited, limit, remaining, reset_time = await is_limited(
            redis_client=limiter.redis_client,
            key=key,
            limit=self.limit,
            window=self.window,
            namespace_prefix=limiter.namespace_prefix,
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
