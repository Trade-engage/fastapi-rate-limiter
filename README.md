## fastapi-rate-limiter

FastAPI-compatible Redis-backed sliding-window rate limiter.

### Installation

- From PyPI (when published):
  
  ```bash
  pip install fastapi-rate-limiter
  ```

- From source (Git):
  
  ```bash
  pip install git+https://github.com/<your_org>/fastapi-rate-limiter@main
  ```

- From a local checkout:
  
  ```bash
  pip install .
  ```

### Quickstart (FastAPI via lifespan)

```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Depends
from fastapi_rate_limiter import RateLimiter, RateLimit


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    rate_limiter = RateLimiter(redis_url="redis://localhost:6379/0")
    await rate_limiter.init(namespace_prefix="rate-limiter", fastapi_app=app)
    try:
        yield
    finally:
        await rate_limiter.close()


app = FastAPI(lifespan=lifespan)



@app.get("/ping", dependencies=[Depends(RateLimit(limit=5, window=60))])
async def ping():
    return {"ok": True}
```

The `RateLimit` dependency sets standard headers on successful requests:
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset` (unix timestamp)

### Custom key function

Provide an async function that returns a key string for rate limiting. The default key combines client IP and route path.

```python
from fastapi import Request

async def key_from_user(request: Request) -> str:
    user_id = request.headers.get("x-user-id", "anonymous")
    return f"user:{user_id}:{request.url.path}"

limit_dependency = RateLimit(limit=100, window=60, key_function=key_from_user)
```

### Low-level helper

You can call the low-level helper directly if you already manage a Redis client.

```python
import asyncio
import redis.asyncio as redis
from fastapi_rate_limiter import is_limited

async def main():
    client = redis.from_url("redis://localhost:6379/0")
    limited, limit, remaining, reset = await is_limited(
        redis_client=client,
        key="user:123:/ping",
        limit=5,
        window=60,
        namespace_prefix="rate-limiter",
    )
    print(limited, limit, remaining, reset)

asyncio.run(main())
```

### License

MIT License

Copyright (c) TradeEngage

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


