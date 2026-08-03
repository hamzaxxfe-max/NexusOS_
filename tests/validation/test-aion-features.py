#!/usr/bin/env python3
# Aion feature unit tests — quick-resume, vram-scaler, cloud-sync,
# zero-lag-record. All subprocess/OS interactions are mocked so the tests
# run on any platform (CI included).
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(
        path.stem.replace("-", "_"), ROOT / path
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def qr():
    return _load(Path("features/quick-resume.py"))


@pytest.fixture(scope="module")
def vs():
    return _load(Path("features/vram-scaler.py"))


@pytest.fixture(scope="module")
def cs():
    return _load(Path("features/cloud-sync.py"))


@pytest.fixture(scope="module")
def zlr():
    return _load(Path("features/zero-lag-record.py"))


# ---------------------------------------------------------------------------
# quick-resume
# ---------------------------------------------------------------------------
class TestQuickResume:
    def test_validate_game_name_ok(self, qr):
        assert qr.validate_game_name("Cyberpunk 2077")
        assert qr.validate_game_name("a-b_c 1")

    def test_validate_game_name_rejects_traversal(self, qr):
        assert not qr.validate_game_name("")
        assert not qr.validate_game_name("../etc/passwd")
        assert not qr.validate_game_name("a;rm -rf")
        assert not qr.validate_game_name("/bin/sh")

    def test_check_criu_found(self, qr, monkeypatch):
        class R:
            returncode = 0
            stdout = "/usr/bin/criu"
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert qr.check_criu()

    def test_check_criu_missing(self, qr, monkeypatch):
        class R:
            returncode = 1
            stdout = ""
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert not qr.check_criu()

    def test_check_permissions_root(self, qr, monkeypatch):
        class FakeOS:
            @staticmethod
            def geteuid():
                return 0
        monkeypatch.setattr(qr, "os", FakeOS)
        assert qr.check_permissions()

    def test_check_permissions_no_cap(self, qr, monkeypatch):
        class FakeOS:
            @staticmethod
            def geteuid():
                return 1000
        monkeypatch.setattr(qr, "os", FakeOS)
        class R:
            returncode = 0
            stdout = "CapEff:\t0000000000000000\n"
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert not qr.check_permissions()

    def test_check_permissions_has_cap(self, qr, monkeypatch):
        class FakeOS:
            @staticmethod
            def geteuid():
                return 1000
        monkeypatch.setattr(qr, "os", FakeOS)
        cap = 1 << 19  # CAP_SYS_PTRACE
        class R:
            returncode = 0
            stdout = "CapEff:\t%016x\n" % cap
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert qr.check_permissions()

    def test_find_game_pid(self, qr, monkeypatch):
        class R:
            returncode = 0
            stdout = "42\n"
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())

        class FakeStatus:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def __iter__(self):
                return iter(["Name:\tgame\n", "Threads:\t8\n"])

        def fake_open(path, *a, **k):
            if str(path).endswith("/status"):
                return FakeStatus()
            return real_open(path, *a, **k)

        import builtins
        real_open = builtins.open
        monkeypatch.setattr(builtins, "open", fake_open)
        monkeypatch.setattr(qr.Path, "exists", lambda self: True)
        assert qr.find_game_pid("game") == 42

    def test_get_process_memory_mb(self, qr, monkeypatch):
        class FakeStatus:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def __iter__(self):
                return iter(["Name:\tgame\n", "VmRSS:\t204800 kB\n"])

        def fake_open(path, *a, **k):
            if str(path).endswith("/status"):
                return FakeStatus()
            return real_open(path, *a, **k)

        import builtins
        real_open = builtins.open
        monkeypatch.setattr(builtins, "open", fake_open)
        assert qr.get_process_memory_mb(123) == 200.0

    def test_get_process_memory_mb_missing(self, qr):
        assert qr.get_process_memory_mb(999999) == 0.0

    def test_freeze_game_success(self, qr, monkeypatch, tmp_path):
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(qr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(qr, "check_criu", lambda: True)
        monkeypatch.setattr(qr, "check_permissions", lambda: True)
        monkeypatch.setattr(qr, "find_game_pid", lambda name: 42)
        monkeypatch.setattr(qr, "get_process_memory_mb", lambda pid: 512.0)
        monkeypatch.setattr(qr.shutil, "disk_usage",
                            lambda p: type("D", (), {"free": 10 * 1024 ** 3})())
        class R:
            returncode = 0
            stderr = ""
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert qr.freeze_game("mygame")
        assert (tmp_path / "mygame" / "metadata.json").exists()

    def test_freeze_game_invalid_name(self, qr, monkeypatch, tmp_path):
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        assert not qr.freeze_game("../../evil")

    def test_freeze_game_criu_failure_cleans_up(self, qr, monkeypatch, tmp_path):
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(qr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(qr, "check_criu", lambda: True)
        monkeypatch.setattr(qr, "check_permissions", lambda: True)
        monkeypatch.setattr(qr, "find_game_pid", lambda name: 42)
        monkeypatch.setattr(qr, "get_process_memory_mb", lambda pid: 100.0)
        monkeypatch.setattr(qr.shutil, "disk_usage",
                            lambda p: type("D", (), {"free": 10 * 1024 ** 3})())
        class R:
            returncode = 1
            stderr = "dump failed"
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert not qr.freeze_game("mygame")
        assert not (tmp_path / "mygame").exists()

    def test_restore_game_success(self, qr, monkeypatch, tmp_path):
        snapshot = tmp_path / "mygame"
        snapshot.mkdir()
        (snapshot / "metadata.json").write_text(json.dumps(
            {"game_name": "mygame", "pid": 42}))
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(qr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(qr, "check_criu", lambda: True)
        monkeypatch.setattr(qr, "check_permissions", lambda: True)
        class R:
            returncode = 0
            stderr = ""
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert qr.restore_game("mygame")
        assert not snapshot.exists()

    def test_restore_game_no_snapshot(self, qr, monkeypatch, tmp_path):
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(qr, "check_criu", lambda: True)
        monkeypatch.setattr(qr, "check_permissions", lambda: True)
        assert not qr.restore_game("missing")

    def test_restore_game_criu_failure(self, qr, monkeypatch, tmp_path):
        snapshot = tmp_path / "mygame"
        snapshot.mkdir()
        (snapshot / "metadata.json").write_text(json.dumps({"game_name": "mygame"}))
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(qr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(qr, "check_criu", lambda: True)
        monkeypatch.setattr(qr, "check_permissions", lambda: True)
        class R:
            returncode = 1
            stderr = "restore failed"
        monkeypatch.setattr(qr.subprocess, "run", lambda *a, **k: R())
        assert not qr.restore_game("mygame")
        assert snapshot.exists()

    def test_delete_snapshot(self, qr, monkeypatch, tmp_path):
        snapshot = tmp_path / "mygame"
        snapshot.mkdir()
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(qr, "STATE_FILE", tmp_path / "state.json")
        assert qr.delete_snapshot("mygame")
        assert not snapshot.exists()

    def test_delete_snapshot_missing(self, qr, monkeypatch, tmp_path):
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        assert not qr.delete_snapshot("nope")

    def test_list_frozen(self, qr, monkeypatch, tmp_path):
        g = tmp_path / "game1"
        g.mkdir()
        (g / "metadata.json").write_text(json.dumps({"game_name": "game1"}))
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        snaps = qr.list_frozen()
        assert len(snaps) == 1
        assert snaps[0]["game_name"] == "game1"

    def test_update_state_evicts_oldest(self, qr, monkeypatch, tmp_path):
        old = tmp_path / "oldgame"
        new = tmp_path / "newgame"
        old.mkdir(); new.mkdir()
        monkeypatch.setattr(qr, "SNAPSHOT_DIR", tmp_path)
        monkeypatch.setattr(qr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(qr, "MAX_SNAPSHOTS", 1)
        qr._update_state("oldgame", {"frozen_at": "2024-01-01T00:00:00"})
        qr._update_state("newgame", {"frozen_at": "2024-02-01T00:00:00"})
        state = json.loads((tmp_path / "state.json").read_text())
        assert "oldgame" not in state
        assert "newgame" in state
        assert not old.exists()

    def test_remove_from_state(self, qr, monkeypatch, tmp_path):
        monkeypatch.setattr(qr, "STATE_FILE", tmp_path / "state.json")
        (tmp_path / "state.json").write_text(
            json.dumps({"a": 1, "b": 2}))
        qr._remove_from_state("a")
        state = json.loads((tmp_path / "state.json").read_text())
        assert state == {"b": 2}

    def test_guess_game_name(self, qr, monkeypatch):
        class FakePath:
            def __init__(self, p):
                self._p = p
            def read_text(self, **kw):
                if "cmdline" in str(self._p):
                    return "/usr/bin/steam\x00/home/user/.steam/steam/steamapps/common/Dota 2/dota.sh\x00"
                return "dota\n"
            @property
            def stem(self):
                import ntpath
                return ntpath.basename(str(self._p)).rsplit(".", 1)[0]
            def __str__(self):
                return str(self._p)
        monkeypatch.setattr(qr, "Path", FakePath)
        name = qr._guess_game_name(123)
        assert name == "dota"

    def test_validate_name_only(self, qr):
        assert qr.validate_name_only("Portal 2")
        assert not qr.validate_name_only("a/b")

    def test_suspend_games(self, qr, monkeypatch, tmp_path):
        proc = tmp_path / "42"
        proc.mkdir()
        (proc / "cmdline").write_bytes(b"steam\x00-dota.sh\x00")
        monkeypatch.setattr(qr.Path, "iterdir",
                            lambda self: [proc] if str(self) == "/proc" else [])
        monkeypatch.setattr(qr, "freeze_game", lambda name: True)
        monkeypatch.setattr(qr, "list_frozen", lambda: [])
        qr._suspend_games()


# ---------------------------------------------------------------------------
# vram-scaler
# ---------------------------------------------------------------------------
class TestVramScaler:
    def test_get_recommended_gtt(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "get_total_ram_gb", lambda: 8)
        assert vs.get_recommended_gtt() == 1024

    def test_get_recommended_gtt_large(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "get_total_ram_gb", lambda: 128)
        assert vs.get_recommended_gtt() == 8192

    def test_read_sysfs_ok(self, vs, monkeypatch, tmp_path):
        f = tmp_path / "value"
        f.write_text("1024\n")
        assert vs.read_sysfs(str(f)) == 1024

    def test_read_sysfs_missing(self, vs):
        assert vs.read_sysfs("/no/such/path") is None

    def test_vram_usage_percent(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "read_sysfs",
                            lambda p: 2048 if "total" in p else 512)
        assert abs(vs.get_vram_usage_percent() - 25.0) < 1e-6

    def test_vram_usage_percent_no_data(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "read_sysfs", lambda p: None)
        assert vs.get_vram_usage_percent() == 0.0

    def test_gtt_usage_percent(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "read_sysfs",
                            lambda p: 1024 if "size" in p else 512)
        assert abs(vs.get_gtt_usage_percent() - 50.0) < 1e-6

    def test_get_gtt_size_mb(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "read_sysfs", lambda p: 1024 * 1024 * 2048)
        assert vs.get_gtt_size_mb() == 2048

    def test_get_gtt_size_mb_missing(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "read_sysfs", lambda p: None)
        assert vs.get_gtt_size_mb() == 0

    def test_set_gtt_writes_sysfs(self, vs, monkeypatch, tmp_path):
        card0 = tmp_path / "card0"
        device = card0 / "device"
        device.mkdir(parents=True)
        gtt = device / "gtt_size"
        gtt.write_text("0")

        class FakePath(Path):
            _globs = [card0]

            def glob(self, pattern):
                return list(self._globs)

            def exists(self):
                return True

            def mkdir(self, *a, **k):
                return None

        monkeypatch.setattr(vs, "Path", FakePath)
        monkeypatch.setattr(vs, "AMD_GTT_PATH", str(gtt))
        assert vs.set_gtt_size_mb(2048)
        assert gtt.read_text() == str(2048 * 1024 * 1024)

    def test_set_gtt_permission_denied(self, vs, monkeypatch, tmp_path):
        card0 = tmp_path / "card0"
        device = card0 / "device"
        device.mkdir(parents=True)
        gtt = device / "gtt_size"
        gtt.write_text("0")

        class FakePath(Path):
            def glob(self, pattern):
                return [DenyWritePath(str(card0))]

            def exists(self):
                return True

            def mkdir(self, *a, **k):
                return None

        class DenyWritePath(FakePath):
            def __truediv__(self, other):
                return DenyWritePath(Path(self) / other)

            def write_text(self, data, *a, **k):
                raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(vs, "Path", FakePath)
        monkeypatch.setattr(vs, "AMD_GTT_PATH", str(gtt))
        assert not vs.set_gtt_size_mb(2048)

    def test_detect_gpu_amd(self, vs, monkeypatch):
        class R:
            returncode = 0
            stdout = "VGA compatible controller: Advanced Micro Devices [AMD]"
        monkeypatch.setattr(vs.subprocess, "run", lambda *a, **k: R())
        assert vs.detect_gpu_type() == "amd"

    def test_detect_gpu_unknown(self, vs, monkeypatch):
        class R:
            returncode = 0
            stdout = ""
        monkeypatch.setattr(vs.subprocess, "run", lambda *a, **k: R())
        assert vs.detect_gpu_type() == "unknown"

    def test_scale_vram_non_amd_exits(self, vs, monkeypatch):
        monkeypatch.setattr(vs, "detect_gpu_type", lambda: "nvidia")
        monkeypatch.setattr(vs, "get_total_ram_gb", lambda: 16)
        vs.scale_vram()


# ---------------------------------------------------------------------------
# cloud-sync
# ---------------------------------------------------------------------------
class TestCloudSync:
    def test_check_rclone_found(self, cs, monkeypatch):
        class R:
            returncode = 0
        monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R())
        assert cs.check_rclone()

    def test_check_rclone_missing(self, cs, monkeypatch):
        class R:
            returncode = 1
        monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R())
        assert not cs.check_rclone()

    def test_load_config_missing(self, cs, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "CONFIG_FILE", tmp_path / "nope.json")
        assert cs.load_config() == {}

    def test_load_save_config_roundtrip(self, cs, monkeypatch, tmp_path):
        cfg = tmp_path / "cfg.json"
        monkeypatch.setattr(cs, "CONFIG_FILE", cfg)
        cs.save_config({"remote_path": "aion-gdrive-crypt:", "sync_paths": []})
        assert cs.load_config()["remote_path"] == "aion-gdrive-crypt:"

    def test_get_file_hash(self, cs, tmp_path):
        f = tmp_path / "save.bin"
        f.write_bytes(b"hello")
        assert cs.get_file_hash(f) == cs.hashlib.sha256(b"hello").hexdigest()

    def test_sync_to_cloud_no_rclone(self, cs, monkeypatch):
        monkeypatch.setattr(cs, "check_rclone", lambda: False)
        assert not cs.sync_to_cloud({"remote_path": "x:"})

    def test_sync_to_cloud_no_remote(self, cs, monkeypatch):
        monkeypatch.setattr(cs, "check_rclone", lambda: True)
        assert not cs.sync_to_cloud({})

    def test_sync_to_cloud_success(self, cs, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "check_rclone", lambda: True)
        save = tmp_path / "games"
        save.mkdir()
        config = {
            "remote_path": "aion-gdrive-crypt:",
            "sync_paths": ["wine-prefixes"],
        }
        # Patch the SYNC_PATHS entry to point into tmp_path
        monkeypatch.setitem(cs.SYNC_PATHS["wine-prefixes"], "path", str(save))
        class R:
            returncode = 0
            stderr = ""
        monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R())
        assert cs.sync_to_cloud(config)

    def test_sync_to_cloud_failure(self, cs, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "check_rclone", lambda: True)
        save = tmp_path / "games"
        save.mkdir()
        config = {
            "remote_path": "aion-gdrive-crypt:",
            "sync_paths": ["wine-prefixes"],
        }
        monkeypatch.setitem(cs.SYNC_PATHS["wine-prefixes"], "path", str(save))
        class R:
            returncode = 1
            stderr = "boom"
        monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R())
        assert not cs.sync_to_cloud(config)

    def test_restore_from_cloud_success(self, cs, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "check_rclone", lambda: True)
        save = tmp_path / "games"
        save.mkdir()
        config = {
            "remote_path": "aion-gdrive-crypt:",
            "sync_paths": ["wine-prefixes"],
        }
        monkeypatch.setitem(cs.SYNC_PATHS["wine-prefixes"], "path", str(save))
        class R:
            returncode = 0
            stderr = ""
        monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R())
        assert cs.restore_from_cloud(config)

    def test_restore_from_cloud_failure_returns_false(self, cs, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "check_rclone", lambda: True)
        save = tmp_path / "games"
        save.mkdir()
        config = {
            "remote_path": "aion-gdrive-crypt:",
            "sync_paths": ["wine-prefixes"],
        }
        monkeypatch.setitem(cs.SYNC_PATHS["wine-prefixes"], "path", str(save))
        class R:
            returncode = 1
            stderr = "restore failed"
        monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R())
        # restore must NOT claim success when rclone fails
        assert not cs.restore_from_cloud(config)


# ---------------------------------------------------------------------------
# zero-lag-record
# ---------------------------------------------------------------------------
class TestZeroLagRecord:
    def test_get_encoder_args_nvenc_hevc(self, zlr):
        args = zlr.get_encoder_args("nvenc", zlr.PRESETS["quality"])
        assert "-c:v" in args
        assert "hevc_nvenc" in args
        assert "-zerolatency" in args

    def test_get_encoder_args_nvenc_h264(self, zlr):
        args = zlr.get_encoder_args("nvenc", zlr.PRESETS["performance"])
        assert "h264_nvenc" in args

    def test_get_encoder_args_vaapi(self, zlr):
        args = zlr.get_encoder_args("vaapi", zlr.PRESETS["quality"])
        assert "hevc_vaapi" in args
        assert "/dev/dri/renderD128" in args

    def test_get_encoder_args_qsv(self, zlr):
        args = zlr.get_encoder_args("qsv", zlr.PRESETS["quality"])
        assert "hevc_qsv" in args

    def test_get_encoder_args_amf(self, zlr):
        args = zlr.get_encoder_args("amf", zlr.PRESETS["quality"])
        assert "hevc_amf" in args

    def test_get_encoder_args_software(self, zlr):
        args = zlr.get_encoder_args("software", zlr.PRESETS["quality"])
        assert "libx265" in args

    def test_get_encoder_args_software_h264(self, zlr):
        args = zlr.get_encoder_args("software", zlr.PRESETS["performance"])
        assert "libx264" in args

    def test_detect_encoder_nvenc(self, zlr, monkeypatch):
        class R:
            returncode = 0
            stdout = "V....D h264_nvenc NVIDIA encoder\n"
        monkeypatch.setattr(zlr.subprocess, "run", lambda *a, **k: R())
        assert zlr.detect_encoder() == "nvenc"

    def test_detect_encoder_software(self, zlr, monkeypatch):
        class R:
            returncode = 0
            stdout = "nothing interesting\n"
        monkeypatch.setattr(zlr.subprocess, "run", lambda *a, **k: R())
        assert zlr.detect_encoder() == "software"

    def test_get_pipewire_source(self, zlr, monkeypatch):
        class R:
            returncode = 0
            stdout = "monitor.screen.0\tScreen 0\n"
        monkeypatch.setattr(zlr.subprocess, "run", lambda *a, **k: R())
        assert zlr.get_pipewire_source() == "monitor.screen.0"

    def test_get_pipewire_source_none(self, zlr, monkeypatch):
        class R:
            returncode = 1
            stdout = ""
        monkeypatch.setattr(zlr.subprocess, "run", lambda *a, **k: R())
        assert zlr.get_pipewire_source() is None

    def test_record_game_uses_pipewire(self, zlr, monkeypatch, tmp_path):
        monkeypatch.setattr(zlr, "detect_encoder", lambda: "nvenc")
        monkeypatch.setattr(zlr, "get_pipewire_source", lambda: "monitor.screen.0")
        captured = {}

        class R:
            returncode = 0
            stderr = ""
        def fake_run(cmd, *a, **k):
            captured["cmd"] = cmd
            return R()
        monkeypatch.setattr(zlr.subprocess, "run", fake_run)
        monkeypatch.setattr(zlr.Path, "home", lambda: tmp_path)
        out = str(tmp_path / "clip.mp4")
        assert zlr.record_game("portal", output=out)
        assert captured["cmd"][0] == "ffmpeg"
        assert "-f" in captured["cmd"]
        assert "pipewire" in captured["cmd"]
        assert captured["cmd"][-1] == out

    def test_record_game_ffmpeg_failure(self, zlr, monkeypatch, tmp_path):
        monkeypatch.setattr(zlr, "detect_encoder", lambda: "software")
        monkeypatch.setattr(zlr, "get_pipewire_source", lambda: None)
        class R:
            returncode = 1
            stderr = "ffmpeg crashed"
        monkeypatch.setattr(zlr.subprocess, "run", lambda *a, **k: R())
        monkeypatch.setattr(zlr.Path, "home", lambda: tmp_path)
        assert not zlr.record_game("portal", output=str(tmp_path / "clip.mp4"))

    def test_save_replay_no_segments(self, zlr, monkeypatch, tmp_path):
        monkeypatch.setattr(zlr, "REPLAY_DIR", tmp_path)
        assert not zlr.save_replay("game")

    def test_save_replay_single_segment(self, zlr, monkeypatch, tmp_path):
        seg = tmp_path / "replay_001.mkv"
        seg.write_bytes(b"data")
        monkeypatch.setattr(zlr, "REPLAY_DIR", tmp_path)
        monkeypatch.setattr(zlr.Path, "home", lambda: tmp_path)
        assert zlr.save_replay("game")

    def test_start_replay_buffer(self, zlr, monkeypatch, tmp_path):
        monkeypatch.setattr(zlr, "REPLAY_DIR", tmp_path)
        monkeypatch.setattr(zlr, "detect_encoder", lambda: "software")
        monkeypatch.setattr(zlr, "get_pipewire_source", lambda: ":0.0")
        class P:
            pid = 999
        monkeypatch.setattr(zlr.subprocess, "Popen", lambda *a, **k: P())
        proc = zlr.start_replay_buffer("game")
        assert proc.pid == 999
        assert (tmp_path / "replay.pid").read_text() == "999"
