"""Short-lived spawned CPU worker: bounded bytes, operations, CPU and wall time."""

import asyncio
import json
import multiprocessing
from dataclasses import asdict
from time import monotonic

from app.domain.analysis.artifact_diff import DiffResult, compare_json, compare_text


def _calculate(pipe, before, after, kind):
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 3))
        left, right = before.decode("utf-8"), after.decode("utf-8")
        if kind == "web":
            from app.application.services.artifact_service import sanitize_html_for_preview

            previews = [sanitize_html_for_preview(body).encode() for body in (left, right)]
            clipped = any(len(body) > 32768 for body in previews)
            result = asdict(
                DiffResult(
                    before != after, not clipped, "preview_limit" if clipped else "side_by_side"
                )
            )
            result.update(
                before_preview=previews[0][:32768].decode(errors="ignore"),
                after_preview=previews[1][:32768].decode(errors="ignore"),
            )
        else:
            function = compare_json if kind == "json" else compare_text
            result = asdict(function(left, right, output_limit=1046528, input_limit=4194304))
        if result["reason"] == "async_required":
            result["reason"] = "compute_limit"
            result["content_changed"] = before != after
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > 1048576:
            encoded = json.dumps(
                asdict(DiffResult(before != after, False, "output_limit"))
            ).encode()
        pipe.send_bytes(encoded)
    except (ValueError, UnicodeError, RecursionError, MemoryError):
        pipe.send_bytes(
            json.dumps(asdict(DiffResult(None, False, "invalid_or_complex_input"))).encode()
        )
    finally:
        pipe.close()


class IsolatedDiffCompute:
    async def compute(self, before: bytes, after: bytes, kind: str):
        if max(len(before), len(after)) > 2097152:
            return asdict(DiffResult(None, False, "input_limit"))
        if kind not in {"text", "json", "web"}:
            raise ValueError("invalid_diff_kind")
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_calculate, args=(send, before, after, kind), daemon=True)
        process.start()
        send.close()
        deadline = monotonic() + 5
        try:
            while monotonic() < deadline:
                if receive.poll():
                    try:
                        return json.loads(receive.recv_bytes(maxlength=1048576))
                    except (EOFError, OSError, ValueError):
                        break
                if not process.is_alive():
                    break
                await asyncio.sleep(0.01)
            return asdict(DiffResult(None, False, "compute_limit"))
        finally:
            receive.close()
            if process.is_alive():
                process.kill()
            await asyncio.to_thread(process.join, 1)
            process.close()
