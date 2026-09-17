from redis import Redis
from fastapi import HTTPException, status
from redis.exceptions import RedisError

from app.core.config import get_settings

redis_client = Redis.from_url(get_settings().redis_url, decode_responses=True)


def check_redis() -> bool:
    return bool(redis_client.ping())


def ensure_redis() -> None:
    try:
        redis_client.ping()
    except RedisError as exc:
        raise RuntimeError("Redis 连接不可用，服务无法启动") from exc


def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Fail closed when Redis is unavailable so public write endpoints stay protected."""
    try:
        with redis_client.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, window_seconds)
            attempts, _ = pipeline.execute()
        if attempts > limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="操作过于频繁，请稍后再试")
    except HTTPException:
        raise
    except RedisError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="限流服务暂不可用") from exc
