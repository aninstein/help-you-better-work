"""Bounded subprocess output and process-group cleanup, including cancellation."""

import asyncio
import os
import signal
from pathlib import Path


async def run_process(
    argv: list[str],
    cwd: Path,
    timeout: int,
    limit: int,
    env: dict[str, str] | None = None,
) -> dict:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    async def drain(reader: asyncio.StreamReader | None) -> str:
        result = bytearray()
        if reader:
            while data := await reader.read(8192):
                if len(result) < limit * 4:
                    result.extend(data[: limit * 4 - len(result)])
        value = result.decode("utf-8", errors="replace")
        return value[:limit] + ("\n[output truncated]" if len(value) > limit else "")

    async def collect() -> tuple[str, str]:
        stdout, stderr, _ = await asyncio.gather(
            drain(process.stdout), drain(process.stderr), process.wait()
        )
        return stdout, stderr

    task = asyncio.create_task(collect())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(task), timeout)
        return {
            "ok": process.returncode == 0,
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    finally:
        # A shell can exit before its children. Always clean up its process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
