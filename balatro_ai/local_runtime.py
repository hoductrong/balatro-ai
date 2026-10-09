"""Machine-local model/game lifecycle and cooperative run controls."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from .client import BalatroBotClient
from .local_coach import LocalCoach
from .probe import run_probes
from .runner import DECKS, STAKES, Limits, load_resume, run_game, write_json
from .watch import RunWatch

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "runs" / "local"
LMS = Path.home() / ".lmstudio/bin/lms"
GAME = Path("/Applications/Balatro.app/Contents/Game/Balatro.app/Contents/MacOS/love")
LOVELY = Path("/Applications/Balatro.app/Contents/liblovely.dylib")
MODS = Path.home() / "Library/Application Support/Balatro/Mods"
IDENTIFIER = "balatro-llm"


def command(*args, timeout=180):
    reply = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=timeout)
    if reply.returncode:
        raise RuntimeError((reply.stdout + reply.stderr)[-2500:])
    return reply.stdout


class RunControl:
    def __init__(self):
        self.condition = threading.Condition()
        self.paused = False
        self.stopped = False
        self.generation = 0

    def set(self, action):
        with self.condition:
            if action in {"pause", "stop"}:
                self.generation += 1
            if action == "stop":
                self.stopped = True
            self.paused = action == "pause"
            self.condition.notify_all()

    def checkpoint(self):
        started = time.monotonic()
        with self.condition:
            while self.paused and not self.stopped:
                self.condition.wait(0.2)
            if self.stopped:
                raise KeyboardInterrupt
        return time.monotonic() - started


def install_bridge():
    source = ROOT / "macos/BalatroLLMLocal"
    destination = MODS / "BalatroLLMLocal"
    destination.mkdir(exist_ok=True)
    for src in source.iterdir():
        target = destination / src.name
        if target.exists() and target.read_bytes() != src.read_bytes():
            backup = DATA / "backups" / str(time.time_ns())
            backup.mkdir(parents=True)
            shutil.copy2(target, backup / target.name)
        shutil.copy2(src, target)


class LocalRuntime:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.thread = None
        self.control = RunControl()
        self.status = "idle"
        self.message = "Chọn model rồi bấm Nạp model."
        self.models = []
        self.loaded = None
        self.run = None
        self.watch = None
        self.game = None
        self.results = []
        self.probes = []
        self.settings = {
            "model": "qwen3.6-35b-a3b",
            "deck": "RED",
            "stake": "WHITE",
            "seed": "",
            "count": 1,
        }
        state = DATA / "ui-state.json"
        if state.exists():
            saved = json.loads(state.read_text())
            self.settings.update(saved.get("settings", {}))
            self.run = Path(saved["run"]) if saved.get("run") else None
            if self.run and (self.run / "trajectory.jsonl").exists():
                self.watch = RunWatch(self.run)
                self.message = (
                    "Đã khôi phục lịch sử. Chọn model; dùng Khôi phục nếu ván còn dang dở."
                )
            self.results = saved.get("results", [])
            self.probes = saved.get("probes", [])

    def save(self):
        write_json(
            DATA / "ui-state.json",
            dict(
                settings=self.settings,
                run=str(self.run) if self.run else None,
                results=self.results,
                probes=self.probes,
            ),
        )

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def snapshot(self):
        with self.lock:
            return dict(
                status=self.status,
                message=self.message,
                busy=self.busy,
                models=self.models,
                loaded=self.loaded,
                settings=self.settings,
                results=self.results[-30:],
                probes=self.probes[-10:],
                run=str(self.run) if self.run else None,
                paused=self.control.paused,
                call_seconds=60,
                backend="Bionic / LM Studio",
            )

    def refresh(self):
        self.models = [
            m
            for m in json.loads(command(LMS, "ls", "--json", timeout=30))
            if m.get("type") == "llm"
        ]
        return self.models

    def job(self, status, work):
        with self.lock:
            if self.busy:
                raise ValueError(
                    "Một tác vụ đang chạy. Dừng trước khi đổi model hoặc chạy tác vụ khác."
                )
            self.control = RunControl()
            self.status = status

            def execute():
                try:
                    work()
                except KeyboardInterrupt:
                    self.status, self.message = "stopped", "Đã dừng; kết quả AI đến muộn bị bỏ."
                except Exception as exc:
                    self.status, self.message = "error", str(exc)
                finally:
                    with self.lock:
                        self.save()

            self.thread = threading.Thread(target=execute, daemon=True)
            self.thread.start()

    def load(self, key):
        def work():
            self.message = "Đang kiểm tra model đã cài…"
            self.refresh()
            if key not in {m["modelKey"] for m in self.models}:
                raise ValueError("Model chưa có trên máy; bản này không tự tải model.")
            command(LMS, "server", "start", timeout=60)
            loaded = json.loads(command(LMS, "ps", "--json", timeout=30))
            own = next((m for m in loaded if m.get("identifier") == IDENTIFIER), None)
            if own and own.get("modelKey") != key:
                command(LMS, "unload", IDENTIFIER, timeout=60)
                own = None
            if not own:
                self.loaded = None
                self.message = "Đang nạp model vào Bionic / LM Studio…"
                command(
                    LMS,
                    "load",
                    key,
                    "--identifier",
                    IDENTIFIER,
                    "--context-length",
                    "16384",
                    "--parallel",
                    "1",
                    "-y",
                    timeout=240,
                )
            self.loaded = key
            self.settings["model"] = key
            self.coach().preflight()
            self.status, self.message = "ready", "Model đã nạp. Có thể benchmark hoặc chạy ván."

        self.job("loading", work)

    def coach(self):
        if not self.loaded:
            raise ValueError("Nạp model trước.")
        return LocalCoach(IDENTIFIER, model_key=self.loaded)

    def ensure_game(self):
        connection = DATA / "game.json"
        if connection.exists():
            port = json.loads(connection.read_text())["port"]
            candidate = BalatroBotClient(port=port)
            try:
                health = candidate.health()
                if health.get("local_bridge_version") == 1 and health.get("bot_profile") == 2:
                    self.game = candidate
                    return candidate
            except RuntimeError:
                pass
        running = subprocess.run(
            ["pgrep", "-f", "^" + re.escape(str(GAME)) + "$"], capture_output=True, text=True
        ).stdout.strip()
        if running:
            raise RuntimeError("Balatro đang mở ngoài bản fork. Đóng game rồi bấm Chạy lại.")
        install_bridge()
        backup = DATA / "backups" / ("profile-" + str(time.time_ns()))
        backup.mkdir(parents=True)
        game_data = MODS.parent
        for name in ("settings.jkr", "2"):
            path = game_data / name
            if path.is_dir():
                shutil.copytree(path, backup / name)
            elif path.exists():
                shutil.copy2(path, backup / name)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env.pop("DYLD_INSERT_LIBRARIES", None)
        env.update(
            BALATRO_AI="1",
            BALATRO_LLM_LOCAL="1",
            BALATROBOT_FAST="0",
            BALATROBOT_HEADLESS="0",
            BALATROBOT_RENDER_ON_API="0",
            BALATROBOT_FPS_CAP="60",
            BALATROBOT_RENDER_FPS="30",
            BALATROBOT_GAMESPEED="4",
            BALATROBOT_ANIMATION_FPS="60",
            BALATROBOT_HOST="127.0.0.1",
            BALATROBOT_PORT=str(port),
        )
        with (DATA / "game.log").open("ab") as log:
            process = subprocess.Popen(
                ["/usr/bin/arch", "-arm64", "-e", f"DYLD_INSERT_LIBRARIES={LOVELY}", str(GAME)],
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        write_json(connection, dict(port=port, pid=process.pid))
        client = BalatroBotClient(port=port)
        for _ in range(100):
            self.control.checkpoint()
            if process.poll() is not None:
                raise RuntimeError("Balatro đã thoát. Xem runs/local/game.log.")
            try:
                health = client.health()
                if health.get("local_bridge_version") == 1 and health.get("bot_profile") == 2:
                    self.game = client
                    return client
            except RuntimeError:
                pass
            time.sleep(0.3)
        raise RuntimeError("Game API chưa sẵn sàng. Xem runs/local/game.log.")

    def start(self, options, resume=False):
        settings = dict(self.settings, **options)
        if settings["deck"] not in DECKS or settings["stake"] not in STAKES:
            raise ValueError("Deck/stake không hợp lệ")
        seed = settings.get("seed", "").strip()
        if seed and re.fullmatch(r"[A-Za-z0-9]{1,8}", seed) is None:
            raise ValueError("Seed cần 1–8 chữ/số")
        count = int(settings.get("count", 1))
        if not 1 <= count <= 20:
            raise ValueError("Số ván cần từ 1 đến 20")
        self.coach().preflight()
        if settings.get("model") != self.loaded:
            raise ValueError("Nạp model đã chọn trước khi chạy")
        previous = self.run if resume else None
        if resume and (previous is None or not (previous / "trajectory.jsonl").exists()):
            raise ValueError("Chưa có trajectory để khôi phục")

        def work():
            self.settings = settings
            self.message = "Đang kết nối Balatro, profile bot 2…"
            game = self.ensure_game()
            for index in range(1 if resume else count):
                self.control.checkpoint()
                continuation = load_resume(game, previous) if previous else None
                if continuation:
                    current_seed = json.loads((previous / "manifest.json").read_text()).get("seed")
                else:
                    raw = game.gamestate()
                    if raw.get("state") != "MENU":
                        # Explicit Start is a new bot run; Recover preserves the old one.
                        write_json(
                            DATA / f"reset-{time.time_ns()}.json",
                            dict(
                                previous_run=str(self.run) if self.run else None,
                                previous_state=raw.get("state"),
                                profile=raw.get("bot_profile"),
                            ),
                        )
                        game.rpc("menu")
                    current_seed = (
                        seed if index == 0 else (seed[:6] + f"{index:02d}") if seed else None
                    )
                self.run = DATA / f"game-{time.time_ns()}"
                self.watch = RunWatch(self.run)
                self.save()
                self.message = (
                    f"Đang chơi ván {index + 1}/{count} · {settings['deck']} / {settings['stake']}"
                )
                result = run_game(
                    game,
                    self.coach(),
                    self.run,
                    limits=Limits(450, 750, 7200, 60),
                    seed=current_seed,
                    deck=settings["deck"],
                    stake=settings["stake"],
                    continuation=continuation,
                    control=self.control,
                )
                result = dict(result, model=self.loaded, run=str(self.run))
                self.results.append(result)
                self.save()
                if result["status"] not in {"won", "lost"}:
                    self.status, self.message = result["status"], result["reason"]
                    return
            self.status = "finished"
            self.message = f"Hoàn thành: {result['status']} · Ante {result['ante_reached']}"

        self.job("running", work)

    def benchmark(self):
        coach = self.coach()

        def work():
            self.message = "Đang kiểm tra 5 tình huống chiến thuật gốc; không điều khiển game."
            output = DATA / f"probes-{time.time_ns()}"
            result = run_probes(
                ROOT / "tests/fixtures/decision_probes.json",
                output,
                max_calls=5,
                call_seconds=60,
                coach=coach,
            )
            self.probes.append(
                dict(
                    model=self.loaded,
                    passed=result["passed"],
                    failed=result["failed"],
                    results=result["results"],
                    run=str(output),
                )
            )
            self.status, self.message = (
                "ready",
                f"Probe: {result['passed']}/5 đạt. Đây chưa phải tỷ lệ thắng.",
            )

        self.job("benchmarking", work)

    def action(self, name):
        with self.lock:
            if not self.busy or self.status not in {"running", "paused", "stopping"}:
                raise ValueError("Không có ván đang chạy")
            self.control.set(name)
            self.status = {"pause": "paused", "resume": "running", "stop": "stopping"}[name]
            self.message = {
                "pause": "Đã chặn hành động tiếp theo; kết quả AI đang chờ sẽ bị bỏ.",
                "resume": "Đã tiếp tục ván.",
                "stop": "Đang dừng tại ranh giới an toàn; không gửi hành động mới.",
            }[name]
