#!/usr/bin/env python3
"""
Benchmark Ollama inference: local vs cluster GPU.
Runs meter recognition test on both endpoints and compares performance.
"""
import base64
import json
import sys
import time
import subprocess
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import cycle, islice
from pathlib import Path
from typing import Dict, List, Tuple
import urllib.request
import urllib.error
from datetime import datetime
import statistics
import re
import tempfile
import shutil

MODEL = "gemma4:e4b"
DEFAULT_OPTIONS = {
    "temperature": 0,
    "num_ctx": 8192,
}

PROFILES = {
    "fast": {
        "temperature": 0,
        "num_ctx": 4096,
        "num_predict": 96,
        "top_k": 20,
        "top_p": 0.9,
    },
    "balanced": {
        "temperature": 0,
        "num_ctx": 8192,
        "num_predict": 160,
        "top_k": 30,
        "top_p": 0.92,
    },
    "quality": {
        "temperature": 0,
        "num_ctx": 12288,
        "num_predict": 256,
        "top_k": 40,
        "top_p": 0.95,
    },
}
PROMPT = """\
Analyze the utility meter in this photo.

Return ONLY valid JSON: {"reading": "VALUE", "serial": "VALUE"}

READING:
- The main consumption counter on the digital display.
- Read ALL digit positions from left to right, including leading zeros.
- Include the decimal point if present.
- Do NOT skip any drums or windows, even if dim or partially rotated.
- Ignore handwritten numbers, stickers, or annotations.
- No spaces, no units, no thousands separators.

SERIAL:
- The serial number or device ID printed on the meter label.
- Look for labels like Nr., S/N, Zähler-Nr., Eigentum, or similar.
- Include ALL leading zeros exactly as printed.
- Only alphanumeric characters, no spaces or punctuation.

If a field is unreadable, use null.\
"""

class BenchmarkResult:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.durations: List[float] = []
        self.success_count = 0
        self.error_count = 0
        self.responses: List[Dict] = []
        self.start_time = None
        self.end_time = None

    def add_result(self, duration: float, success: bool, response: Dict = None):
        self.durations.append(duration)
        if success:
            self.success_count += 1
            if response:
                self.responses.append(response)
        else:
            self.error_count += 1

    def get_stats(self) -> Dict:
        if not self.durations:
            return {}
        return {
            "count": len(self.durations),
            "success": self.success_count,
            "errors": self.error_count,
            "min": min(self.durations),
            "max": max(self.durations),
            "mean": statistics.mean(self.durations),
            "median": statistics.median(self.durations),
            "stdev": statistics.stdev(self.durations) if len(self.durations) > 1 else 0,
            "total_time": sum(self.durations),
        }


def check_ollama_available(url: str) -> bool:
    """Check if Ollama is available at the given URL."""
    try:
        req = urllib.request.Request(url.replace("/api/generate", ""), method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def run_inference(url: str, img_path: Path, model: str, prompt: str, options: Dict) -> Tuple[float, bool, Dict]:
    """Run single inference and return (duration_in_seconds, success, response_data)."""
    b64 = base64.b64encode(img_path.read_bytes()).decode()

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "format": "json",
        "think": False,
        "options": options,
    }).encode()

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}
    )
    
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
            duration = time.time() - start
            return duration, True, result
    except Exception as e:
        duration = time.time() - start
        return duration, False, {"error": str(e)}


def get_gpu_stats(cluster_host: str = "172.16.50.98") -> Dict:
    """Get GPU stats from cluster via SSH."""
    try:
        cmd = [
            "ssh", "-o", "GSSAPIAuthentication=no", "-o", "ProxyCommand=none",
            "-o", "StrictHostKeyChecking=no",
            f"root@{cluster_host}",
            "nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total --format=csv,nounits,noheader"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            return {
                "gpu_util": float(parts[0]),
                "mem_util": float(parts[1]),
                "mem_used_mb": float(parts[2]),
                "mem_total_mb": float(parts[3]),
            }
    except Exception as e:
        print(f"Warning: Could not get GPU stats: {e}", file=sys.stderr)
    return {}


def monitor_gpu_during_inference(cluster_host: str, duration_sec: float):
    """Monitor GPU during inference (background, simple polling)."""
    stats = []
    start = time.time()
    poll_interval = 0.5
    
    while time.time() - start < duration_sec + 1:
        gpu_stat = get_gpu_stats(cluster_host)
        if gpu_stat:
            stats.append(gpu_stat)
        time.sleep(poll_interval)
    
    return stats


def _print_single_result(result: BenchmarkResult, stats: Dict):
    """Print results for a single endpoint."""
    print(f"\n  Endpoint: {result.endpoint}")
    print(f"  Success: {stats['success']}/{stats['count']}")
    print(f"\n  {'METRIC':<25} {'VALUE':<20}")
    print("  " + "-" * 45)
    print(f"  {'MIN Duration (s)':<25} {stats['min']:>18.2f}s")
    print(f"  {'MAX Duration (s)':<25} {stats['max']:>18.2f}s")
    print(f"  {'MEAN Duration (s)':<25} {stats['mean']:>18.2f}s")
    print(f"  {'MEDIAN Duration (s)':<25} {stats['median']:>18.2f}s")
    if stats['stdev'] > 0:
        print(f"  {'STDEV Duration (s)':<25} {stats['stdev']:>18.2f}s")
    print(f"  {'Total Time (s)':<25} {stats['total_time']:>18.2f}s")
    
    if result.responses:
        print(f"\n  Image Processing Results:")
        for i, resp in enumerate(result.responses, 1):
            if resp.get('response'):
                try:
                    parsed = json.loads(resp.get('response', '').strip())
                    reading = parsed.get('reading', 'N/A')
                    serial = parsed.get('serial', 'N/A')
                    print(f"    [{i}] reading={reading}, serial={serial}")
                except:
                    print(f"    [{i}] (parsing failed)")


def _build_image_list(images: List[Path], count: int) -> List[Path]:
    """Create a deterministic image list of desired length by cycling input images."""
    return list(islice(cycle(images), count))


def _print_phase_stats(title: str, phase_result: BenchmarkResult):
    stats = phase_result.get_stats()
    print(f"\n{'='*70}")
    print(title)
    print(f"{'='*70}")
    if not stats:
        print("❌ No data")
        return
    _print_single_result(phase_result, stats)


def run_parallel_phase(endpoint: str, images: List[Path], workers: int, model: str, prompt: str, options: Dict) -> BenchmarkResult:
    """Run a parallel inference phase with a fixed number of workers."""
    result = BenchmarkResult(endpoint)
    result.start_time = datetime.now()

    print(f"\nStarting parallel phase: {len(images)} requests with {workers} workers")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_inference, endpoint, img, model, prompt, options): img for img in images
        }
        completed = 0
        for future in as_completed(futures):
            completed += 1
            img_path = futures[future]
            duration, success, response = future.result()
            result.add_result(duration, success, response)
            status = "✓" if success else "✗"
            print(f"  [{completed}/{len(images)}] {img_path.name}: {status} {duration:.2f}s")

    result.end_time = datetime.now()
    return result


def _extract_quality_metrics(responses: List[Dict]) -> Dict:
    """Quality proxy for OCR extraction tasks without labeled ground truth."""
    total = len(responses)
    if total == 0:
        return {
            "json_parse_rate": 0.0,
            "reading_present_rate": 0.0,
            "serial_present_rate": 0.0,
            "reading_format_rate": 0.0,
            "serial_format_rate": 0.0,
            "quality_score": 0.0,
        }

    parsed_ok = 0
    reading_present = 0
    serial_present = 0
    reading_format_ok = 0
    serial_format_ok = 0

    # OCR expectations for this task: reading numeric with optional dot, serial alnum only.
    reading_pattern = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
    serial_pattern = re.compile(r"^[A-Za-z0-9]+$")

    for resp in responses:
        raw = resp.get("response", "").strip()
        try:
            payload = json.loads(raw)
            parsed_ok += 1
        except Exception:
            continue

        reading = payload.get("reading")
        serial = payload.get("serial")

        if reading is not None and str(reading).strip() != "":
            reading_present += 1
            if reading_pattern.match(str(reading)):
                reading_format_ok += 1

        if serial is not None and str(serial).strip() != "":
            serial_present += 1
            if serial_pattern.match(str(serial)):
                serial_format_ok += 1

    m = {
        "json_parse_rate": parsed_ok / total,
        "reading_present_rate": reading_present / total,
        "serial_present_rate": serial_present / total,
        "reading_format_rate": reading_format_ok / total,
        "serial_format_rate": serial_format_ok / total,
    }
    # Weighted for OCR extraction reliability.
    m["quality_score"] = (
        0.35 * m["json_parse_rate"]
        + 0.30 * m["reading_present_rate"]
        + 0.10 * m["serial_present_rate"]
        + 0.20 * m["reading_format_rate"]
        + 0.05 * m["serial_format_rate"]
    )
    return m


def _build_sweep_configs(base_profiles: Dict[str, Dict]) -> List[Dict]:
    """Build a curated sweep set for performance vs OCR quality."""
    return [
        {"name": "fast_base", "options": dict(base_profiles["fast"])},
        {"name": "fast_ctx8k", "options": {**base_profiles["fast"], "num_ctx": 8192}},
        {"name": "balanced_base", "options": dict(base_profiles["balanced"])},
        {"name": "balanced_predict120", "options": {**base_profiles["balanced"], "num_predict": 120}},
        {"name": "balanced_predict192", "options": {**base_profiles["balanced"], "num_predict": 192}},
        {"name": "quality_base", "options": dict(base_profiles["quality"])},
        {"name": "quality_predict320", "options": {**base_profiles["quality"], "num_predict": 320}},
        {"name": "pdf_ocr_focus", "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 320, "top_k": 40, "top_p": 0.95}},
    ]


def run_parameter_sweep(endpoint: str, images: List[Path], model: str, prompt: str, sweep_count: int):
    """Run multi-configuration benchmark sweep and recommend best config."""
    if not check_ollama_available(endpoint):
        print(f"❌ Ollama not available at {endpoint}")
        return

    sweep_images = _build_image_list(images, sweep_count)
    configs = _build_sweep_configs(PROFILES)

    print(f"\n{'#'*78}")
    print("PARAMETER SWEEP")
    print(f"Endpoint: {endpoint}")
    print(f"Model: {model}")
    print(f"Images per config: {len(sweep_images)}")
    print(f"Configurations: {len(configs)}")
    print(f"{'#'*78}")

    rows = []
    for idx, cfg in enumerate(configs, 1):
        print(f"\n[{idx}/{len(configs)}] Testing config: {cfg['name']}")
        print(f"Options: {json.dumps(cfg['options'], ensure_ascii=False)}")

        result = benchmark_endpoint(
            endpoint=endpoint,
            images=sweep_images,
            model=model,
            prompt=prompt,
            options=cfg["options"],
        )
        stats = result.get_stats()
        quality = _extract_quality_metrics(result.responses)

        if not stats:
            rows.append({
                "name": cfg["name"],
                "options": cfg["options"],
                "success_rate": 0.0,
                "mean_s": float("inf"),
                "stdev_s": float("inf"),
                "quality_score": 0.0,
                "composite": float("inf"),
            })
            continue

        success_rate = stats["success"] / stats["count"] if stats["count"] else 0.0
        mean_s = stats["mean"]
        stdev_s = stats["stdev"]
        quality_score = quality["quality_score"]

        # Lower is better. Penalize failures and low quality heavily.
        composite = (
            mean_s
            + 2.0 * stdev_s
            + (1.0 - success_rate) * 50.0
            + (1.0 - quality_score) * 15.0
        )

        rows.append({
            "name": cfg["name"],
            "options": cfg["options"],
            "success_rate": success_rate,
            "mean_s": mean_s,
            "stdev_s": stdev_s,
            "quality_score": quality_score,
            "json_parse_rate": quality["json_parse_rate"],
            "reading_present_rate": quality["reading_present_rate"],
            "reading_format_rate": quality["reading_format_rate"],
            "composite": composite,
        })

    rows_sorted = sorted(rows, key=lambda r: r["composite"])
    fastest = min(rows, key=lambda r: r["mean_s"])
    best_quality = max(rows, key=lambda r: r["quality_score"])
    best_overall = rows_sorted[0]

    print(f"\n{'='*92}")
    print("SWEEP SUMMARY")
    print(f"{'='*92}")
    print(f"{'CONFIG':<24} {'MEAN(s)':>9} {'STDEV':>9} {'SUCCESS':>9} {'QUALITY':>9} {'SCORE':>9}")
    print("-" * 92)
    for r in rows_sorted:
        success_pct = 100.0 * r["success_rate"]
        print(
            f"{r['name']:<24} {r['mean_s']:>9.2f} {r['stdev_s']:>9.2f} {success_pct:>8.1f}% "
            f"{r['quality_score']:>9.3f} {r['composite']:>9.2f}"
        )

    print(f"\nBest overall: {best_overall['name']}")
    print(f"  options={json.dumps(best_overall['options'], ensure_ascii=False)}")
    print(f"Best speed:   {fastest['name']} ({fastest['mean_s']:.2f}s mean)")
    print(f"Best quality: {best_quality['name']} (score {best_quality['quality_score']:.3f})")

    pdf_candidate = next((r for r in rows_sorted if r["name"] == "pdf_ocr_focus"), None)
    if pdf_candidate:
        print("\nPDF OCR recommendation:")
        print(
            "  Use pdf_ocr_focus for longer context pages, then step down to balanced_predict192 "
            "if latency is too high."
        )


def _collect_pdf_files(pdf_dir: Path) -> List[Path]:
    files = sorted(pdf_dir.glob("*.pdf"))
    return [f for f in files if f.is_file()]


def _render_pdf_pages(pdf_files: List[Path], dpi: int, max_pages_per_pdf: int) -> List[Path]:
    """Render PDF pages to JPG files using pdftoppm (poppler)."""
    if shutil.which("pdftoppm") is None:
        print("❌ pdftoppm not found. Install poppler-tools (or poppler-utils).")
        return []

    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf_bench_"))
    rendered: List[Path] = []

    for pdf in pdf_files:
        out_prefix = tmp_dir / pdf.stem
        cmd = [
            "pdftoppm",
            "-jpeg",
            "-r",
            str(dpi),
            str(pdf),
            str(out_prefix),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"⚠️ Could not render {pdf.name}: {result.stderr.strip()}")
            continue

        # pdftoppm writes files like <prefix>-1.jpg, <prefix>-2.jpg
        pages = sorted(tmp_dir.glob(f"{pdf.stem}-*.jpg"))
        if max_pages_per_pdf > 0:
            pages = pages[:max_pages_per_pdf]

        rendered.extend(pages)

    return rendered


def run_pdf_sweep(
    endpoint: str,
    pdf_dir: Path,
    model: str,
    prompt: str,
    sweep_count: int,
    dpi: int,
    max_pages_per_pdf: int,
):
    """Render PDFs to images and run parameter sweep on rendered pages."""
    pdf_files = _collect_pdf_files(pdf_dir)
    if not pdf_files:
        print(f"❌ No PDF files found in {pdf_dir}")
        return

    print(f"Found {len(pdf_files)} PDF file(s) in {pdf_dir}")
    rendered_pages = _render_pdf_pages(pdf_files, dpi=dpi, max_pages_per_pdf=max_pages_per_pdf)
    if not rendered_pages:
        print("❌ No rendered pages available for benchmark")
        return

    print(f"Rendered {len(rendered_pages)} page image(s) at {dpi} DPI")
    run_parameter_sweep(
        endpoint=endpoint,
        images=rendered_pages,
        model=model,
        prompt=prompt,
        sweep_count=sweep_count,
    )


def run_heat_cool_parallel_test(
    endpoint: str,
    images: List[Path],
    warmup_count: int,
    cooldown_sec: int,
    parallel_count: int,
    model: str,
    prompt: str,
    options: Dict,
):
    """Run warmup -> cooldown -> parallel load sequence against one endpoint."""
    print(f"\n{'#'*78}")
    print("HEAT/COOLDOWN/PARALLEL TEST")
    print(f"Endpoint: {endpoint}")
    print(f"Warmup requests: {warmup_count}")
    print(f"Cooldown seconds: {cooldown_sec}")
    print(f"Parallel requests: {parallel_count}")
    print(f"{'#'*78}")

    if not check_ollama_available(endpoint):
        print(f"❌ Ollama not available at {endpoint}")
        return

    warmup_images = _build_image_list(images, warmup_count)
    parallel_images = _build_image_list(images, parallel_count)

    warmup_result = benchmark_endpoint(endpoint, warmup_images, model=model, prompt=prompt, options=options)
    _print_phase_stats("WARMUP RESULTS", warmup_result)

    print(f"\nCooling down for {cooldown_sec}s...")
    time.sleep(cooldown_sec)

    parallel_result = run_parallel_phase(
        endpoint,
        parallel_images,
        workers=parallel_count,
        model=model,
        prompt=prompt,
        options=options,
    )
    _print_phase_stats("PARALLEL RESULTS", parallel_result)

    warmup_stats = warmup_result.get_stats()
    parallel_stats = parallel_result.get_stats()
    if warmup_stats and parallel_stats:
        print(f"\n{'='*70}")
        print("PHASE COMPARISON")
        print(f"{'='*70}")
        print(f"Warmup mean (serial):   {warmup_stats['mean']:.2f}s")
        print(f"Parallel mean/request:  {parallel_stats['mean']:.2f}s")
        print(f"Parallel total (10 req): {parallel_stats['total_time']:.2f}s (sum of request durations)")

        wall_warmup = (warmup_result.end_time - warmup_result.start_time).total_seconds()
        wall_parallel = (parallel_result.end_time - parallel_result.start_time).total_seconds()
        print(f"Warmup wall-clock:      {wall_warmup:.2f}s")
        print(f"Parallel wall-clock:    {wall_parallel:.2f}s")
        if wall_parallel > 0:
            throughput = parallel_stats['count'] / wall_parallel
            print(f"Parallel throughput:    {throughput:.2f} req/s")



def benchmark_endpoint(
    endpoint: str,
    images: List[Path],
    cluster_host: str = None,
    model: str = MODEL,
    prompt: str = PROMPT,
    options: Dict = None,
) -> BenchmarkResult:
    """Run benchmark against an endpoint."""
    result = BenchmarkResult(endpoint)
    
    print(f"\n{'='*70}")
    print(f"Benchmarking: {endpoint}")
    print(f"{'='*70}")
    
    if not check_ollama_available(endpoint):
        print(f"❌ Ollama not available at {endpoint}")
        return result
    
    print(f"✓ Ollama available, starting {len(images)} inference(s)...")
    result.start_time = datetime.now()
    
    if options is None:
        options = DEFAULT_OPTIONS

    for i, img_path in enumerate(images, 1):
        print(f"\n  [{i}/{len(images)}] Processing {img_path.name}...", end=" ", flush=True)
        
        duration, success, response = run_inference(endpoint, img_path, model, prompt, options)
        result.add_result(duration, success, response)
        
        if success:
            # Extract reading/serial if available
            try:
                raw = response.get('response', '').strip()
                parsed = json.loads(raw)
                reading = parsed.get('reading', 'N/A')
                serial = parsed.get('serial', 'N/A')
                print(f"✓ {duration:.1f}s (reading={reading}, serial={serial})")
            except:
                print(f"✓ {duration:.1f}s")
        else:
            print(f"✗ {duration:.1f}s (error: {response.get('error', 'unknown')})")
    
    result.end_time = datetime.now()
    return result


def print_comparison(local_result: BenchmarkResult, cluster_result: BenchmarkResult):
    """Print detailed comparison report."""
    print(f"\n{'='*70}")
    print(f"BENCHMARK RESULTS")
    print(f"{'='*70}")
    
    local_stats = local_result.get_stats()
    cluster_stats = cluster_result.get_stats()
    
    if not local_stats and not cluster_stats:
        print("❌ No data collected from any endpoint")
        return
    
    if not local_stats:
        print("⚠️  No local Ollama available (install at localhost:11434)")
        print(f"\nCluster Results:")
        _print_single_result(cluster_result, cluster_stats)
        return
    
    if not cluster_stats:
        print("⚠️  No cluster Ollama available")
        print(f"\nLocal Results:")
        _print_single_result(local_result, local_stats)
        return
    
    print(f"\n{'METRIC':<25} {'LOCAL':<20} {'CLUSTER':<20} {'SPEEDUP':<15}")
    print("-" * 80)
    
    # Success rate
    local_success_rate = (local_stats['success'] / local_stats['count'] * 100) if local_stats['count'] > 0 else 0
    cluster_success_rate = (cluster_stats['success'] / cluster_stats['count'] * 100) if cluster_stats['count'] > 0 else 0
    print(f"{'Success Rate':<25} {local_success_rate:>18.1f}% {cluster_success_rate:>18.1f}%")
    
    # Min/Max/Mean
    print(f"\n{'MIN Duration (s)':<25} {local_stats['min']:>18.2f}s {cluster_stats['min']:>18.2f}s", end="")
    if cluster_stats['min'] > 0:
        speedup = local_stats['min'] / cluster_stats['min']
        print(f" {speedup:>13.2f}x")
    else:
        print()
    
    print(f"{'MAX Duration (s)':<25} {local_stats['max']:>18.2f}s {cluster_stats['max']:>18.2f}s", end="")
    if cluster_stats['max'] > 0:
        speedup = local_stats['max'] / cluster_stats['max']
        print(f" {speedup:>13.2f}x")
    else:
        print()
    
    print(f"{'MEAN Duration (s)':<25} {local_stats['mean']:>18.2f}s {cluster_stats['mean']:>18.2f}s", end="")
    if cluster_stats['mean'] > 0:
        speedup = local_stats['mean'] / cluster_stats['mean']
        print(f" {speedup:>13.2f}x")
    else:
        print()
    
    print(f"{'MEDIAN Duration (s)':<25} {local_stats['median']:>18.2f}s {cluster_stats['median']:>18.2f}s", end="")
    if cluster_stats['median'] > 0:
        speedup = local_stats['median'] / cluster_stats['median']
        print(f" {speedup:>13.2f}x")
    else:
        print()
    
    if local_stats['stdev'] > 0 or cluster_stats['stdev'] > 0:
        print(f"{'STDEV Duration (s)':<25} {local_stats['stdev']:>18.2f}s {cluster_stats['stdev']:>18.2f}s")
    
    print(f"{'Total Time (s)':<25} {local_stats['total_time']:>18.2f}s {cluster_stats['total_time']:>18.2f}s", end="")
    if cluster_stats['total_time'] > 0:
        speedup = local_stats['total_time'] / cluster_stats['total_time']
        print(f" {speedup:>13.2f}x")
    else:
        print()
    
    print("\n" + "=" * 80)
    print(f"SUMMARY:")
    print(f"  Local:   {local_stats['success']} successful, {local_stats['errors']} failed")
    print(f"  Cluster: {cluster_stats['success']} successful, {cluster_stats['errors']} failed")
    
    if cluster_stats['mean'] > 0:
        speedup_factor = local_stats['mean'] / cluster_stats['mean']
        print(f"\n  ⚡ Cluster is {speedup_factor:.2f}x {'FASTER' if speedup_factor > 1 else 'SLOWER'} on average")
    
    print(f"\n  Local time:   {local_result.start_time} to {local_result.end_time}")
    print(f"  Cluster time: {cluster_result.start_time} to {cluster_result.end_time}")


def main():
    parser = argparse.ArgumentParser(description="Ollama benchmark tool")
    parser.add_argument("--mode", choices=["compare", "heat-parallel", "sweep", "pdf-sweep"], default="compare")
    parser.add_argument("--endpoint", default="http://172.16.50.98:11434/api/generate")
    parser.add_argument("--warmup-count", type=int, default=20)
    parser.add_argument("--cooldown-sec", type=int, default=15)
    parser.add_argument("--parallel-count", type=int, default=10)
    parser.add_argument("--sweep-count", type=int, default=6)
    parser.add_argument("--profile", choices=["fast", "balanced", "quality"], default="balanced")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--num-ctx", type=int, default=None)
    parser.add_argument("--num-predict", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--pdf-dir", default="/home/dmkif/zaehl-o-mat/Beispielzähler")
    parser.add_argument("--pdf-dpi", type=int, default=220)
    parser.add_argument("--pdf-max-pages", type=int, default=0)
    args = parser.parse_args()

    options = dict(PROFILES[args.profile])
    if args.num_ctx is not None:
        options["num_ctx"] = args.num_ctx
    if args.num_predict is not None:
        options["num_predict"] = args.num_predict
    if args.temperature is not None:
        options["temperature"] = args.temperature
    if args.top_k is not None:
        options["top_k"] = args.top_k
    if args.top_p is not None:
        options["top_p"] = args.top_p

    # Find test images
    img_dir = Path("/home/dmkif/zaehl-o-mat/Beispielzähler")
    images = sorted(img_dir.glob("*.jpg"))
    
    if not images:
        print(f"❌ No images found in {img_dir}")
        sys.exit(1)
    
    print(f"Found {len(images)} test images")
    print(f"Using model: {args.model}")
    print(f"Using profile: {args.profile}")
    print(f"Using options: {json.dumps(options, ensure_ascii=False)}")

    if args.mode == "heat-parallel":
        run_heat_cool_parallel_test(
            endpoint=args.endpoint,
            images=images,
            warmup_count=args.warmup_count,
            cooldown_sec=args.cooldown_sec,
            parallel_count=args.parallel_count,
            model=args.model,
            prompt=PROMPT,
            options=options,
        )
        print("\n✓ Heat/Cool/Parallel test complete")
        return

    if args.mode == "sweep":
        run_parameter_sweep(
            endpoint=args.endpoint,
            images=images,
            model=args.model,
            prompt=PROMPT,
            sweep_count=args.sweep_count,
        )
        print("\n✓ Parameter sweep complete")
        return

    if args.mode == "pdf-sweep":
        run_pdf_sweep(
            endpoint=args.endpoint,
            pdf_dir=Path(args.pdf_dir),
            model=args.model,
            prompt=PROMPT,
            sweep_count=args.sweep_count,
            dpi=args.pdf_dpi,
            max_pages_per_pdf=args.pdf_max_pages,
        )
        print("\n✓ PDF parameter sweep complete")
        return
    
    # Run benchmarks
    local_result = benchmark_endpoint(
        "http://localhost:11434/api/generate",
        images,
        model=args.model,
        prompt=PROMPT,
        options=options,
    )
    
    cluster_result = benchmark_endpoint(
        "http://172.16.50.98:11434/api/generate",
        images,
        cluster_host="172.16.50.98",
        model=args.model,
        prompt=PROMPT,
        options=options,
    )
    
    # Print comparison
    print_comparison(local_result, cluster_result)
    
    print("\n✓ Benchmark complete")


if __name__ == "__main__":
    main()
