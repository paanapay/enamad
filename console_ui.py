from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field


class C:
    """ANSI colors (no extra dependency)."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


_colors_enabled = False


def enable_colors(force: bool | None = None) -> bool:
    global _colors_enabled
    if force is not None:
        _colors_enabled = force and sys.stdout.isatty()
        return _colors_enabled

    if not sys.stdout.isatty():
        _colors_enabled = False
        return False

    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass

    _colors_enabled = True
    return True


def paint(text: str, *styles: str) -> str:
    if not _colors_enabled or not styles:
        return text
    return "".join(styles) + text + C.RESET


def fmt_duration(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def fmt_int(value: int) -> str:
    return f"{value:,}"


def progress_bar(ratio: float, width: int = 22) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(width * ratio)
    return f"[{'#' * filled}{'-' * (width - filled)}] {ratio * 100:5.1f}%"


@dataclass
class ScrapeStats:
    start_page: int = 1
    total_pages: int | None = None
    session_start: float = field(default_factory=time.time)
    pages_done: int = 0
    records_saved: int = 0
    chunks: int = 0
    chunk_pages_sum: int = 0
    captcha_solves: int = 0
    captcha_rounds: int = 0
    first_guess_wins: int = 0
    last_page: int = 0
    working_page: int = 0

    def note_captcha_round_failed(self) -> None:
        self.captcha_rounds += 1

    def note_captcha_solved(self, failed_tries: int) -> None:
        self.captcha_solves += 1
        if failed_tries == 0:
            self.first_guess_wins += 1

    def note_chunk(self, pages: list[int], records: int) -> None:
        if not pages:
            return
        self.chunks += 1
        self.chunk_pages_sum += len(pages)
        self.pages_done += len(pages)
        self.records_saved += records
        self.last_page = max(pages)

    @property
    def elapsed(self) -> float:
        return max(0.001, time.time() - self.session_start)

    @property
    def pages_per_hour(self) -> float:
        return self.pages_done * 3600 / self.elapsed

    @property
    def avg_chunk_size(self) -> float:
        if self.chunks == 0:
            return 0.0
        return self.chunk_pages_sum / self.chunks

    @property
    def progress_ratio(self) -> float:
        if not self.total_pages or self.last_page <= 0:
            return 0.0
        return min(1.0, self.last_page / self.total_pages)

    def eta_seconds(self) -> float:
        if not self.total_pages or self.pages_done <= 0:
            return float("inf")
        remaining_pages = max(0, self.total_pages - self.last_page)
        sec_per_page = self.elapsed / self.pages_done
        return remaining_pages * sec_per_page

    def first_guess_rate(self) -> float:
        if self.captcha_solves == 0:
            return 0.0
        return self.first_guess_wins * 100 / self.captcha_solves


class ScrapeConsole:
    """Live console: transient lines clear; sticky stats panel stays in place."""

    def __init__(self, enabled: bool = True, live: bool = True) -> None:
        self.enabled = enabled
        self.live = live and enabled and sys.stdout.isatty()
        self._transient_lines = 0
        self._sticky_lines = 0

    def _write(self, text: str = "") -> None:
        if not self.enabled:
            return
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()

    def _erase_lines(self, count: int) -> None:
        if count <= 0 or not self.live:
            return
        for _ in range(count):
            sys.stdout.write("\033[1A\033[2K")
        sys.stdout.flush()

    def permanent(self, text: str = "") -> None:
        self._write(text)

    def line(self, text: str = "") -> None:
        self.permanent(text)

    def transient(self, text: str = "") -> None:
        if not self.enabled:
            return
        if not self.live:
            return
        self._write(text)
        self._transient_lines += 1

    def clear_transient(self) -> None:
        self._erase_lines(self._transient_lines)
        self._transient_lines = 0

    def clear_sticky(self) -> None:
        self._erase_lines(self._sticky_lines)
        self._sticky_lines = 0

    def banner(self, title: str) -> None:
        if not self.enabled:
            return
        border = "=" * 52
        self.permanent(paint(f"+{border}+", C.CYAN, C.BOLD))
        self.permanent(paint(f"|  {title:<50}|", C.CYAN, C.BOLD))
        self.permanent(paint(f"+{border}+", C.CYAN, C.BOLD))
        if self.live:
            self.permanent(paint("  (live: verbose logs clear, stats panel stays)", C.DIM))

    def begin_chunk(self, page: int, total_pages: int | None, stats: ScrapeStats) -> None:
        if not self.enabled:
            return
        stats.working_page = page
        self.clear_transient()
        total = f" / {fmt_int(total_pages)}" if total_pages else ""
        self.transient(
            paint("> ", C.CYAN, C.BOLD)
            + paint(f"Page {fmt_int(page)}{total}", C.WHITE, C.BOLD)
            + paint("  ...", C.DIM)
        )

    def captcha_guesses(self, preview: str, learned: str = "") -> None:
        if not self.enabled:
            return
        text = (
            paint("  >> ", C.YELLOW)
            + paint("Guesses", C.YELLOW, C.BOLD)
            + paint(learned, C.DIM)
            + paint(f": {preview}", C.WHITE)
        )
        if self.live:
            self.transient(text)
        else:
            self.permanent(text)

    def captcha_try(self, index: int, code: str) -> None:
        if not self.enabled:
            return
        text = paint(f"  -> try {index}: ", C.DIM) + paint(code, C.WHITE)
        if self.live:
            self.transient(text)
        else:
            self.permanent(text)

    def captcha_fail(self, attempt: int, max_retries: int, error: str) -> None:
        if not self.enabled:
            return
        text = (
            paint(f"  [!] attempt {attempt}/{max_retries}: ", C.RED)
            + paint(error, C.RED, C.DIM)
        )
        if self.live:
            self.transient(text)
        else:
            self.permanent(text)

    def captcha_reuse(self, page: int) -> None:
        if not self.enabled:
            return
        text = paint(f"  ~ reuse captcha -> page {page}", C.MAGENTA, C.DIM)
        if self.live:
            self.transient(text)
        else:
            self.permanent(text)

    def end_chunk(
        self,
        pages: list[int],
        records_added: int,
        total_saved: int,
        stats: ScrapeStats,
        learner_summary: str = "",
    ) -> None:
        if not self.enabled:
            return

        self.clear_transient()

        summary = ""
        if pages:
            lo, hi = min(pages), max(pages)
            page_range = str(lo) if lo == hi else f"{lo}-{hi}"
            summary = (
                paint("  + ", C.GREEN, C.BOLD)
                + paint(f"pages {page_range}", C.GREEN)
                + paint(f"  | +{records_added} rows", C.WHITE)
                + paint(f"  | total {fmt_int(total_saved)}", C.DIM)
            )

        if self.live:
            self.clear_sticky()
            if summary:
                self.permanent(summary)
        elif summary:
            self.permanent(summary)

        self.refresh_sticky(stats, learner_summary)

    def _stats_row(self, label: str, value: str, w: int = 50) -> str:
        return (
            paint("| ", C.CYAN)
            + paint(f"{label:<12}", C.DIM)
            + paint(value, C.WHITE)
            + paint(" " * max(1, w - 14 - len(value)) + "|", C.CYAN)
        )

    def _stats_lines(self, stats: ScrapeStats, learner_summary: str = "") -> list[str]:
        page_label = (
            f"{fmt_int(stats.last_page)} / {fmt_int(stats.total_pages)}"
            if stats.total_pages
            else fmt_int(stats.last_page or stats.working_page)
        )
        eta = fmt_duration(stats.eta_seconds())
        speed = f"{stats.pages_per_hour / 60:.1f} pg/min"
        avg_chunk = f"{stats.avg_chunk_size:.1f}" if stats.chunks else "-"
        captcha_rate = f"{stats.first_guess_rate():.0f}%"
        elapsed = fmt_duration(stats.elapsed)
        w = 50

        lines = [
            paint("+" + "-" * w + "+", C.CYAN),
            paint("| ", C.CYAN) + paint("LIVE STATS", C.CYAN, C.BOLD) + paint(" " * 39 + "|", C.CYAN),
            paint("+" + "-" * w + "+", C.CYAN),
            self._stats_row("Page", page_label, w),
            self._stats_row("Progress", progress_bar(stats.progress_ratio), w),
            self._stats_row("Records", fmt_int(stats.records_saved), w),
            self._stats_row("Speed", speed, w),
            self._stats_row("Time", f"{elapsed} | ETA {eta}", w),
            self._stats_row("Chunk", f"{stats.chunks} x ~{avg_chunk} pg", w),
            self._stats_row("Captcha", f"{stats.captcha_solves} solve | 1st {captcha_rate}", w),
        ]
        if learner_summary:
            lines.append(self._stats_row("Learning", learner_summary[:36], w))
        lines.append(paint("+" + "-" * w + "+", C.CYAN))
        return lines

    def refresh_sticky(self, stats: ScrapeStats, learner_summary: str = "") -> None:
        if not self.enabled:
            return
        if not self.live:
            self.clear_sticky()
        for line in self._stats_lines(stats, learner_summary):
            self._write(line)
            if self.live:
                self._sticky_lines += 1

    def stats_panel(self, stats: ScrapeStats, learner_summary: str = "") -> None:
        self.refresh_sticky(stats, learner_summary)

    def done(self, total_saved: int, run_id: int | None) -> None:
        if not self.enabled:
            return
        self.clear_transient()
        self.clear_sticky()
        self.permanent(
            paint("\nDone! ", C.GREEN, C.BOLD)
            + paint(f"{fmt_int(total_saved)} records", C.GREEN)
            + paint(f"  (run_id={run_id})", C.DIM)
        )


WORKER_COLORS = (C.CYAN, C.GREEN, C.YELLOW, C.MAGENTA, C.BLUE)


class WorkerConsole:
    """Colored, lock-safe logging for parallel worker processes."""

    def __init__(
        self,
        worker_id: int,
        lock=None,
        *,
        quiet: bool = True,
        silent: bool = False,
        range_end: int | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.lock = lock
        self.quiet = quiet
        self.silent = silent
        self.range_end = range_end

    def badge(self) -> str:
        color = WORKER_COLORS[self.worker_id % len(WORKER_COLORS)]
        return paint(f"W{self.worker_id}", color, C.BOLD)

    def _emit(self, text: str) -> None:
        if self.lock is not None:
            with self.lock:
                self._write_unlocked(text)
        else:
            self._write_unlocked(text)

    def _write_unlocked(self, text: str) -> None:
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()

    def _line(self, body: str, *, force: bool = False) -> None:
        if self.silent and not force:
            return
        self._emit(f"  {paint('|', C.DIM)} {self.badge()} {paint('|', C.DIM)} {body}")

    def info(self, message: str) -> None:
        self._line(paint(message, C.WHITE))

    def dim(self, message: str) -> None:
        if self.quiet:
            return
        self._line(paint(message, C.DIM))

    def warn(self, message: str) -> None:
        self._line(paint(message, C.YELLOW), force=True)

    def error(self, message: str) -> None:
        self._line(paint(message, C.RED), force=True)

    def success(self, message: str) -> None:
        self._line(paint(message, C.GREEN))

    def page_label(self, page: int, range_lo: int) -> str:
        if self.range_end is not None:
            return f"page {fmt_int(page)} / {fmt_int(self.range_end)}"
        return f"page {fmt_int(page)}"

    def chunk_start(self, page: int, range_lo: int) -> None:
        self.info(f"{self.page_label(page, range_lo)}  {paint('...', C.DIM)}")

    def captcha_guesses(self, preview: str, learned: str = "") -> None:
        if self.quiet:
            return
        self.dim(f"captcha{learned}: {preview}")

    def captcha_try(self, index: int, code: str) -> None:
        if self.quiet:
            return
        self.dim(f"try {index}: {code}")

    def captcha_fail(self, attempt: int, max_retries: int, error: str) -> None:
        self.warn(f"captcha {attempt}/{max_retries}: {error}")

    def captcha_reuse(self, page: int) -> None:
        if self.quiet:
            return
        self.dim(f"reuse captcha -> {fmt_int(page)}")

    def chunk_done(self, pages: list[int], records_added: int, total_saved: int) -> None:
        if not pages:
            return
        lo, hi = min(pages), max(pages)
        page_range = str(lo) if lo == hi else f"{lo}-{hi}"
        self.success(
            paint(f"pages {page_range}", C.GREEN, C.BOLD)
            + paint(f"  +{records_added} rows", C.WHITE)
            + paint(f"  total {fmt_int(total_saved)}", C.DIM)
        )


class ParallelDashboard:
    """Live multi-worker status panel (parent process only)."""

    def __init__(
        self,
        worker_ranges: list[tuple[int, int, int]],
        shared,
        lock,
        *,
        global_start: int,
        global_end: int,
    ) -> None:
        # (worker_id, range_lo, range_hi)
        self.worker_ranges = worker_ranges
        self.shared = shared
        self.lock = lock
        self.global_start = global_start
        self.global_end = global_end
        self.started = time.time()
        self._panel_lines = 0
        self._live = sys.stdout.isatty()

    @staticmethod
    def print_plan(
        global_start: int,
        global_end: int,
        ranges: list[tuple[int, int]],
        *,
        worker_count: int | None = None,
    ) -> None:
        border = "=" * 58
        print(paint(f"+{border}+", C.CYAN, C.BOLD))
        title = f"Enamad Parallel Scraper  pages {fmt_int(global_start)}-{fmt_int(global_end)}"
        print(paint(f"|  {title:<56}|", C.CYAN, C.BOLD))
        print(paint(f"+{border}+", C.CYAN, C.BOLD))
        for index, (lo, hi) in enumerate(ranges):
            color = WORKER_COLORS[index % len(WORKER_COLORS)]
            count = hi - lo + 1
            line = (
                f"  {paint(f'W{index}', color, C.BOLD)}"
                f"  {fmt_int(lo)} -> {fmt_int(hi)}"
                f"  ({fmt_int(count)} pages)"
            )
            print(line)
        print(paint(f"+{border}+", C.CYAN, C.BOLD))
        count = worker_count if worker_count is not None else len(ranges)
        if sys.platform == "win32":
            print(
                paint(
                    f"  Windows spawn: {count} workers = {count} separate python.exe processes",
                    C.DIM,
                )
            )
            print(
                paint(
                    "  Task Manager -> Details: look for multiple python.exe with different PIDs",
                    C.DIM,
                )
            )
        else:
            print(paint(f"  {count} worker processes (fork/spawn)", C.DIM))
        print(paint("  Dashboard refreshes every ~1.5s — different page numbers = parallel OK", C.DIM))
        print()

    def _snapshot(self) -> list[dict]:
        rows: list[dict] = []
        with self.lock:
            for worker_id, range_lo, range_hi in self.worker_ranges:
                raw = self.shared.get(str(worker_id), {})
                rows.append(
                    {
                        "worker_id": worker_id,
                        "range_lo": range_lo,
                        "range_hi": range_hi,
                        "last_page": int(raw.get("last_page", range_lo - 1)),
                        "pages_done": int(raw.get("pages_done", 0)),
                        "records": int(raw.get("records", 0)),
                        "status": str(raw.get("status", "starting")),
                        "activity": str(raw.get("activity", "")),
                        "pid": int(raw.get("pid", 0)),
                    }
                )
        return rows

    def _active_process_count(self, rows: list[dict]) -> int:
        pids = {row["pid"] for row in rows if row.get("pid", 0) > 0}
        return len(pids)

    @staticmethod
    def _eta_seconds(pages_done: int, total_pages: int, elapsed: float) -> float:
        if pages_done <= 0 or total_pages <= 0:
            return float("inf")
        remaining = max(0, total_pages - pages_done)
        return remaining * (elapsed / pages_done)

    @staticmethod
    def _eta_label(eta_sec: float) -> str:
        if eta_sec == float("inf"):
            return ""
        finish_clock = time.strftime("%H:%M", time.localtime(time.time() + eta_sec))
        return f"ETA {fmt_duration(eta_sec)} @{finish_clock}"

    def render(self) -> None:
        rows = self._snapshot()
        elapsed = max(0.001, time.time() - self.started)
        total_pages_done = sum(row["pages_done"] for row in rows)
        total_records = sum(row["records"] for row in rows)
        speed = total_pages_done * 60 / elapsed
        active_procs = self._active_process_count(rows)
        global_span = max(1, self.global_end - self.global_start + 1)
        global_done = total_pages_done
        global_ratio = min(1.0, global_done / global_span)
        eta_sec = self._eta_seconds(global_done, global_span, elapsed)
        eta_text = self._eta_label(eta_sec)
        eta_part = f"  {eta_text}" if eta_text else ""

        lines: list[str] = []
        w = 56
        lines.append(paint("+" + "-" * w + "+", C.CYAN))
        proc_label = f"{active_procs}/{len(rows)} proc"
        lines.append(
            paint("| ", C.CYAN)
            + paint("WORKERS", C.CYAN, C.BOLD)
            + paint(
                f"  {proc_label}  {fmt_duration(elapsed)}  {speed:.0f} pg/min"
                f"{eta_part}  {fmt_int(total_records)} rows",
                C.DIM,
            )
            + paint(" |", C.CYAN)
        )
        lines.append(paint("+" + "-" * w + "+", C.CYAN))

        for row in rows:
            wid = row["worker_id"]
            color = WORKER_COLORS[wid % len(WORKER_COLORS)]
            span = row["range_hi"] - row["range_lo"] + 1
            done = row["pages_done"]
            ratio = done / span if span > 0 else 0.0
            bar = progress_bar(ratio, width=14)
            page_text = fmt_int(row["last_page"]) if row["last_page"] >= row["range_lo"] else "-"
            status = row["activity"] or row["status"]
            if len(status) > 10:
                status = status[:9] + "…"
            pid_text = str(row["pid"]) if row.get("pid", 0) > 0 else "…"
            line = (
                paint("| ", C.CYAN)
                + paint(f"W{wid}", color, C.BOLD)
                + paint(f" p{pid_text[-5:]}", C.DIM)
                + " "
                + bar
                + paint(f" pg{page_text}", C.WHITE)
                + paint(f" {fmt_int(row['records'])}r", C.DIM)
                + paint(f" {status:<10}", C.DIM)
                + paint("|", C.CYAN)
            )
            lines.append(line)

        lines.append(paint("+" + "-" * w + "+", C.CYAN))
        eta_suffix = (
            paint(f"  {eta_text}", C.YELLOW, C.DIM) if eta_text else ""
        )
        lines.append(
            paint("| ", C.CYAN)
            + paint("Overall", C.DIM)
            + " "
            + progress_bar(global_ratio, width=22)
            + paint(
                f" {fmt_int(global_done)}/{fmt_int(global_span)} pg",
                C.WHITE,
            )
            + eta_suffix
            + paint(" |", C.CYAN)
        )
        lines.append(paint("+" + "-" * w + "+", C.CYAN))

        if self._live and self._panel_lines > 0:
            sys.stdout.write(f"\033[{self._panel_lines}A")
        for line in lines:
            if self._live:
                sys.stdout.write("\033[2K")
            print(line, flush=True)
        self._panel_lines = len(lines)

    def finish(self, results: list[dict]) -> None:
        if self._live and self._panel_lines > 0:
            sys.stdout.write(f"\033[{self._panel_lines}A")
            for _ in range(self._panel_lines):
                sys.stdout.write("\033[2K\n")
            self._panel_lines = 0

        print()
        print(paint("Parallel summary", C.GREEN, C.BOLD))
        for result in sorted(results, key=lambda item: int(item.get("worker_id") or 0)):
            wid = int(result.get("worker_id") or 0)
            color = WORKER_COLORS[wid % len(WORKER_COLORS)]
            status = result.get("status", "?")
            pages = int(result.get("pages_fetched") or 0)
            records = int(result.get("records_saved") or 0)
            style = C.GREEN if status == "completed" else C.RED
            print(
                f"  {paint(f'W{wid}', color, C.BOLD)}  "
                + paint(f"{status:<10}", style)
                + f"  pid {result.get('pid', '?')}  "
                + f"{fmt_int(pages)} pages  {fmt_int(records)} records"
            )
            if status != "completed":
                print(paint(f"         {result.get('error', '')}", C.RED, C.DIM))
