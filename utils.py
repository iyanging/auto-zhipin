from collections.abc import AsyncIterable
import re


def remove_json_fences(raw: str):
    return re.sub(r"`{3}(json)?\n?", "", raw)


async def async_batched[T](
    iterable: AsyncIterable[T], n: int
) -> AsyncIterable[tuple[T, ...]]:
    chunk: list[T] = []
    async for e in iterable:
        chunk.append(e)

        if len(chunk) == n:
            yield tuple(chunk)
            chunk = []

    if chunk:
        yield tuple(chunk)
