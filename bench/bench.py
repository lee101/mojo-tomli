from __future__ import annotations

import platform
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import mojo_tomli  # noqa: E402
import tomli  # noqa: E402


def time_best(function, *, repeat: int = 5) -> float:
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        function()
        samples.append(time.perf_counter() - start)
    return min(samples)


def machine() -> str:
    cpu = ""
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    if not cpu:
        cpu = platform.processor()
    return f"{cpu or 'unknown CPU'}; Python {platform.python_version()}; {platform.system()} {platform.machine()}"


def row(name: str, document: str, repeat: int = 5) -> tuple[str, float, float]:
    ours = lambda: mojo_tomli.loads(document)
    upstream = lambda: tomli.loads(document)
    ours()
    upstream()
    mojo_time = time_best(ours, repeat=repeat)
    upstream_time = time_best(upstream, repeat=repeat)
    return name, mojo_time, upstream_time


def main() -> None:
    tiny = 'name = "demo"\nenabled = true\nretries = 3\nratio = 0.25\n'
    table_lines = ["[service]"]
    for index in range(5_000):
        table_lines.append(f'key_{index} = "value-{index}"')
    tables = "\n".join(table_lines)
    integers = "values = [" + ",".join(str(i - 125_000) for i in range(250_000)) + "]"
    floats = "values = [" + ",".join(f"{i}.125e-3" for i in range(150_000)) + "]"
    commented = "values = [\n" + "\n".join(
        f"{i}, # item {i}" for i in range(75_000)
    ) + "\n]"

    cases = [
        row("tiny config (4 scalars)", tiny, repeat=20),
        row("table (5,000 string keys)", tables),
        row("integer array (250,000)", integers),
        row("float array (150,000)", floats),
        row("commented integer array (75,000)", commented),
    ]
    print(f"Machine: {machine()}")
    print()
    print("| case | mojo-tomli | tomli 2.4.1 | ratio | result |")
    print("| --- | ---: | ---: | ---: | --- |")
    for name, mojo_time, upstream_time in cases:
        ratio = upstream_time / mojo_time
        result = "faster" if ratio >= 1 else "slower"
        print(
            f"| {name} | {mojo_time * 1e3:.3f} ms | "
            f"{upstream_time * 1e3:.3f} ms | {ratio:.2f}x | {result} |"
        )


if __name__ == "__main__":
    main()
