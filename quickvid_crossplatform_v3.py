#!/usr/bin/env python3
"""
QuickVid - simple cross-platform screen recorder.

Designed to preserve the original Linux QuickVid behavior while also
providing a path for macOS and Windows.

Linux:
  - Uses slop for the familiar drag-to-select rectangle.
  - Uses zenity for timer/audio/filename dialogs.
  - Uses pactl for PulseAudio/PipeWire audio selection.
  - Uses ffmpeg x11grab for the screen.
  - No Python packages are required.

macOS / Windows:
  - Uses ffmpeg.
  - Rectangle can be supplied with --region X,Y,W,H.
  - Audio device names are platform-dependent; use --list-devices
    and adjust the audio options for the particular computer.

Invocation:
  quickvid_crossplatform.py             Toggle: start/stop
  quickvid_crossplatform.py --start     Start
  quickvid_crossplatform.py --stop      Stop
  quickvid_crossplatform.py --status    Status
"""

import os
import sys
import signal
import shutil
import subprocess
import time
import platform
from pathlib import Path

APP_NAME = "QuickVid"
HOME = Path.home()
CONFIG_DIR = HOME / ".quickvid"
PID_FILE = CONFIG_DIR / "quickvid.pid"
FFMPEG_PID_FILE = CONFIG_DIR / "ffmpeg.pid"
LOG_FILE = Path("/tmp/quickvid.log")
VIDEO_DIR = HOME / "Videos"

SYSTEM = platform.system()
IS_LINUX = SYSTEM == "Linux"
IS_MAC = SYSTEM == "Darwin"
IS_WINDOWS = SYSTEM == "Windows"


# ----------------------------------------------------------------------
# General helpers
# ----------------------------------------------------------------------

def log(message):
    text = f"[QuickVid] {message}"
    print(text, flush=True)
    try:
        with LOG_FILE.open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except Exception:
        pass


def command_exists(name):
    return shutil.which(name) is not None


def run(cmd, check=False, capture=True):
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=check,
        )
    except Exception as e:
        log(f"Command failed: {e}")
        return None


def notify(message, urgency="normal"):
    if IS_LINUX and command_exists("notify-send"):
        subprocess.run(
            ["notify-send", APP_NAME, message, "-u", urgency],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    elif IS_MAC:
        # Avoid AppleScript failures affecting recording.
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{APP_NAME}"'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    else:
        print(f"{APP_NAME}: {message}")


def ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def read_pid(path):
    try:
        pid = int(path.read_text().strip())
        return pid
    except Exception:
        return None


def pid_alive(pid):
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def process_command(pid):
    if not pid:
        return ""
    if IS_LINUX:
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(
                b"\x00", b" "
            ).decode(errors="replace")
        except Exception:
            return ""
    try:
        r = run(["ps", "-p", str(pid), "-o", "command="])
        return (r.stdout or "").strip() if r else ""
    except Exception:
        return ""


def is_our_process(pid):
    cmd = process_command(pid).lower()
    return "quickvid.py" in cmd or "quickvid_crossplatform.py" in cmd


# ----------------------------------------------------------------------
# PID / recording state
# ----------------------------------------------------------------------

def recording_pid():
    pid = read_pid(PID_FILE)
    if pid and pid_alive(pid) and is_our_process(pid):
        return pid
    if pid:
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass
    return None


def status():
    pid = recording_pid()
    if pid:
        ffpid = read_pid(FFMPEG_PID_FILE)
        if ffpid and pid_alive(ffpid):
            print(f"QuickVid is recording. Controller PID: {pid}, FFmpeg PID: {ffpid}")
        else:
            print(f"QuickVid controller is running. PID: {pid}")
        return True
    print("QuickVid is not recording.")
    return False


# ----------------------------------------------------------------------
# Linux audio
# ----------------------------------------------------------------------

def pactl(*args):
    if not command_exists("pactl"):
        return ""
    r = run(["pactl", *args])
    return (r.stdout or "").strip() if r else ""


def pactl_default_sink():
    out = pactl("get-default-sink")
    if out:
        return out.splitlines()[0].strip()
    out = pactl("info")
    for line in out.splitlines():
        if line.lower().startswith("default sink:"):
            return line.split(":", 1)[1].strip()
    return ""


def pactl_default_source():
    out = pactl("get-default-source")
    if out:
        return out.splitlines()[0].strip()
    out = pactl("info")
    for line in out.splitlines():
        if line.lower().startswith("default source:"):
            return line.split(":", 1)[1].strip()
    return ""


def find_bx29():
    sinks = pactl("list", "short", "sinks")
    sources = pactl("list", "short", "sources")

    for text in (sinks, sources):
        for line in text.splitlines():
            low = line.lower()
            if "bx29" in low:
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1]

    return ""


def linux_audio_devices():
    sink = pactl_default_sink()
    source = pactl_default_source()
    bx29 = find_bx29()

    monitor = ""
    if sink:
        monitor = sink + ".monitor"

        # Confirm the monitor exists.
        sources = pactl("list", "short", "sources")
        if monitor not in sources:
            # Some PipeWire installations expose a slightly different name.
            monitor = ""
            for line in sources.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith(sink + ".monitor"):
                    monitor = parts[1]
                    break

    return monitor, source, bx29


def choose_linux_audio():
    """
    Return one of:
      none, system, mic, both, bx29
    """
    if not command_exists("zenity"):
        return "none"

    choices = [
        "No audio",
        "System audio only",
        "Microphone only",
        "Both system + microphone",
        "BX29 headphones",
    ]

    r = subprocess.run(
        [
            "zenity", "--list",
            "--title", f"{APP_NAME} - Audio Options",
            "--text", "What audio do you want to record?",
            "--column", "Option",
            *choices,
            "--width", "350",
            "--height", "280",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    selected = (r.stdout or "").strip()

    mapping = {
        "No audio": "none",
        "System audio only": "system",
        "Microphone only": "mic",
        "Both system + microphone": "both",
        "BX29 headphones": "bx29",
    }
    return mapping.get(selected, "none")


# ----------------------------------------------------------------------
# Linux dialogs
# ----------------------------------------------------------------------

def linux_timer():
    """
    Return seconds, or 0 for no limit.
    """
    if not command_exists("zenity"):
        return 0

    r = subprocess.run(
        [
            "zenity", "--forms",
            "--title", f"{APP_NAME} - Recording Time",
            "--text", "Optional recording time. Leave blank for no limit.",
            "--add-entry", "Minutes",
            "--width", "400",
            "--height", "180",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    if r.returncode != 0:
        return None

    value = (r.stdout or "").strip().replace(",", ".")
    if not value:
        return 0

    try:
        minutes = float(value)
        if minutes <= 0:
            return 0
        return max(1, int(minutes * 60))
    except ValueError:
        notify("Invalid recording time.", "critical")
        return None


def linux_filename():
    default_name = time.strftime("QuickVid_%Y-%m-%d_%H-%M-%S.mp4")

    if not command_exists("zenity"):
        return str(VIDEO_DIR / default_name)

    r = subprocess.run(
        [
            "zenity", "--file-selection",
            "--save",
            "--confirm-overwrite",
            "--filename", str(VIDEO_DIR / default_name),
            "--title", f"{APP_NAME} - Save Recording",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    if r.returncode != 0:
        return None

    filename = (r.stdout or "").strip()
    if not filename:
        return None

    if not filename.lower().endswith(".mp4"):
        filename += ".mp4"

    return filename


# ----------------------------------------------------------------------
# Linux region selection
# ----------------------------------------------------------------------

def select_linux_region():
    if not command_exists("slop"):
        log("slop is not installed.")
        log("Install it with: sudo apt install slop")
        return None

    # Exactly matches the original QuickVid approach.
    r = run(["slop", "-f", "%w %h %x %y"])
    if not r or r.returncode != 0:
        return None

    text = (r.stdout or "").strip()
    parts = text.split()

    if len(parts) != 4:
        log(f"Unexpected slop output: {text!r}")
        return None

    try:
        w, h, x, y = map(int, parts)
    except ValueError:
        return None

    if w <= 0 or h <= 0:
        return None

    return x, y, w, h


# ----------------------------------------------------------------------
# macOS / Windows region and dialogs
# ----------------------------------------------------------------------

def parse_region(value):
    try:
        parts = [int(x.strip()) for x in value.split(",")]
        if len(parts) != 4:
            raise ValueError
        x, y, w, h = parts
        if w <= 0 or h <= 0:
            raise ValueError
        return x, y, w, h
    except Exception:
        raise ValueError("Region must be X,Y,W,H")


def prompt_region():
    print()
    print("Enter the recording rectangle as:")
    print("  X,Y,WIDTH,HEIGHT")
    print("Example: 100,100,1200,800")
    print()
    try:
        value = input("Region: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not value:
        return None

    try:
        return parse_region(value)
    except ValueError as e:
        print(f"QuickVid: {e}")
        return None


def prompt_filename():
    default = VIDEO_DIR / time.strftime("QuickVid_%Y-%m-%d_%H-%M-%S.mp4")
    try:
        value = input(f"Output file [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return value or str(default)


# ----------------------------------------------------------------------
# Platform checks
# ----------------------------------------------------------------------

def check_ffmpeg():
    if not command_exists("ffmpeg"):
        log("ffmpeg was not found in PATH.")
        if IS_LINUX:
            log("Install it with: sudo apt install ffmpeg")
        elif IS_MAC:
            log("Install ffmpeg with Homebrew or another trusted package manager.")
        elif IS_WINDOWS:
            log("Install ffmpeg and make sure ffmpeg.exe is in PATH.")
        return False
    return True


def check_linux_requirements():
    missing = []
    for name in ("slop", "zenity", "pactl"):
        if not command_exists(name):
            missing.append(name)

    if missing:
        log("Missing Linux program(s): " + ", ".join(missing))
        log("For the original QuickVid behavior, install:")
        log("  sudo apt install slop zenity pulseaudio-utils")
        # notify-send is optional.
        return False
    return True


# ----------------------------------------------------------------------
# FFmpeg command construction
# ----------------------------------------------------------------------

def build_linux_ffmpeg(region, audio_mode, output, duration):
    x, y, w, h = region

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", "x11grab",
        "-framerate", "30",
        "-video_size", f"{w}x{h}",
        "-i", f":0.0+{x},{y}",
    ]

    if audio_mode == "system":
        monitor, _, _ = linux_audio_devices()
        if not monitor:
            log("Could not find the default system-audio monitor.")
            return None
        cmd += ["-f", "pulse", "-i", monitor]
        cmd += [
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c:a", "aac",
        ]

    elif audio_mode == "mic":
        _, mic, _ = linux_audio_devices()
        if not mic:
            log("Could not find the default microphone.")
            return None
        cmd += ["-f", "pulse", "-i", mic]
        cmd += [
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c:a", "aac",
        ]

    elif audio_mode == "both":
        monitor, mic, _ = linux_audio_devices()
        if not monitor:
            log("Could not find the system-audio monitor.")
            return None
        if not mic:
            log("Could not find the default microphone.")
            return None
        cmd += ["-f", "pulse", "-i", monitor]
        cmd += ["-f", "pulse", "-i", mic]
        cmd += [
            "-filter_complex",
            "[1:a:0][2:a:0]amix=inputs=2:duration=longest:dropout_transition=2[aout]",
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:a", "aac",
            "-shortest",
        ]

    elif audio_mode == "bx29":
        _, _, bx29 = linux_audio_devices()
        if not bx29:
            log("Could not find a PulseAudio/PipeWire device containing 'BX29'.")
            return None
        cmd += ["-f", "pulse", "-i", bx29]
        cmd += [
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c:a", "aac",
        ]

    if duration and duration > 0:
        cmd += ["-t", str(duration)]

    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
    ]

    cmd += [str(output)]
    return cmd


def build_mac_ffmpeg(region, output, duration):
    x, y, w, h = region

    # AVFoundation screen capture commonly uses device 1 for the main screen.
    # This is intentionally conservative; device numbering can vary.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", "avfoundation",
        "-framerate", "30",
        "-video_size", f"{w}x{h}",
        "-i", "1:none",
        "-vf", f"crop={w}:{h}:{x}:{y}",
    ]

    if duration and duration > 0:
        cmd += ["-t", str(duration)]

    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    return cmd


def build_windows_ffmpeg(region, output, duration):
    x, y, w, h = region

    # gdigrab captures the Windows desktop. The crop is applied after capture.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", "gdigrab",
        "-framerate", "30",
        "-i", "desktop",
        "-vf", f"crop={w}:{h}:{x}:{y}",
    ]

    if duration and duration > 0:
        cmd += ["-t", str(duration)]

    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    return cmd


# ----------------------------------------------------------------------
# Start / stop
# ----------------------------------------------------------------------

def write_pid(path, pid):
    path.write_text(str(pid))


def start_ffmpeg(cmd):
    log("Starting FFmpeg:")
    log(" ".join(str(x) for x in cmd))

    try:
        if IS_WINDOWS:
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        else:
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
    except Exception as e:
        log(f"Unable to start ffmpeg: {e}")
        return None

    write_pid(FFMPEG_PID_FILE, p.pid)
    return p


def stop_process(pid):
    if not pid or not pid_alive(pid):
        return

    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Kill the process group first when possible. This also cleans
            # up ffmpeg's child processes without touching unrelated ffmpeg.
            try:
                os.killpg(pid, signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)

            deadline = time.time() + 2
            while time.time() < deadline and pid_alive(pid):
                time.sleep(0.1)

            if pid_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except Exception:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
    except Exception as e:
        log(f"Error stopping process {pid}: {e}")


def stop_recording():
    controller_pid = recording_pid()

    ffpid = read_pid(FFMPEG_PID_FILE)

    # Stop FFmpeg first.
    if ffpid and pid_alive(ffpid):
        log(f"Stopping FFmpeg PID {ffpid}")
        stop_process(ffpid)

    try:
        FFMPEG_PID_FILE.unlink()
    except FileNotFoundError:
        pass

    # If this process is called as a second QuickVid invocation, don't
    # kill ourselves. The controller PID in the PID file belongs to the
    # first invocation.
    if controller_pid and controller_pid != os.getpid() and pid_alive(controller_pid):
        log(f"Stopping QuickVid controller PID {controller_pid}")
        stop_process(controller_pid)

    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass

    notify("Recording stopped")
    return True


def cleanup():
    # Only the controller process should remove its own PID file.
    pid = read_pid(PID_FILE)
    if pid == os.getpid():
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


def controller_signal(signum, frame):
    log("QuickVid received termination signal.")
    cleanup()
    sys.exit(0)


# ----------------------------------------------------------------------
# Start workflow
# ----------------------------------------------------------------------

def start_recording(region_arg=None):
    ensure_dirs()

    existing = recording_pid()
    if existing:
        print(f"[QuickVid] A recording is already running.")
        print(f"[QuickVid] PID: {existing}")
        return False

    # Remove stale state.
    for p in (PID_FILE, FFMPEG_PID_FILE):
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    if not check_ffmpeg():
        return False

    if IS_LINUX and not check_linux_requirements():
        return False

    # --------------------------------------------------------------
    # Choose rectangle
    # --------------------------------------------------------------
    if region_arg:
        try:
            region = parse_region(region_arg)
        except ValueError as e:
            log(str(e))
            return False
    elif IS_LINUX:
        log("Select the recording area with the mouse.")
        region = select_linux_region()
        if not region:
            log("Screen selection cancelled.")
            return False
    else:
        region = prompt_region()
        if not region:
            log("Screen selection cancelled.")
            return False

    x, y, w, h = region
    log(f"Recording region: x={x}, y={y}, width={w}, height={h}")

    # --------------------------------------------------------------
    # Time
    # --------------------------------------------------------------
    if IS_LINUX:
        duration = linux_timer()
        if duration is None:
            return False
    else:
        duration = 0
        print("No recording time limit. Stop QuickVid with the same command.")

    # --------------------------------------------------------------
    # Audio / output
    # --------------------------------------------------------------
    if IS_LINUX:
        audio_mode = choose_linux_audio()
        output = linux_filename()
        if not output:
            log("Save dialog cancelled.")
            return False

        cmd = build_linux_ffmpeg(region, audio_mode, output, duration)

        if cmd is None:
            notify("Could not configure the selected audio.", "critical")
            return False

    elif IS_MAC:
        output = prompt_filename()
        if not output:
            return False
        cmd = build_mac_ffmpeg(region, output, duration)

    else:
        output = prompt_filename()
        if not output:
            return False
        cmd = build_windows_ffmpeg(region, output, duration)

    # --------------------------------------------------------------
    # Start controller
    # --------------------------------------------------------------
    write_pid(PID_FILE, os.getpid())
    signal.signal(signal.SIGTERM, controller_signal)
    signal.signal(signal.SIGINT, controller_signal)
    if hasattr(signal, "SIGQUIT"):
        signal.signal(signal.SIGQUIT, controller_signal)

    p = start_ffmpeg(cmd)
    if not p:
        cleanup()
        return False

    # Give FFmpeg a moment to fail immediately due to an input/device error.
    time.sleep(1)

    if p.poll() is not None:
        stderr = ""
        try:
            stderr = p.stderr.read() if p.stderr else ""
        except Exception:
            pass

        log("FFmpeg exited immediately.")
        if stderr:
            log(stderr.strip())

        cleanup()
        try:
            FFMPEG_PID_FILE.unlink()
        except FileNotFoundError:
            pass

        notify("Recording could not be started.", "critical")
        return False

    notify(f"Recording started: {Path(output).name}")
    log(f"Recording started: {output}")

    # --------------------------------------------------------------
    # Controller loop
    # --------------------------------------------------------------
    try:
        while True:
            rc = p.poll()

            if rc is not None:
                if rc == 0:
                    log("FFmpeg finished normally.")
                    notify(f"Recording saved: {Path(output).name}")
                else:
                    stderr = ""
                    try:
                        stderr = p.stderr.read() if p.stderr else ""
                    except Exception:
                        pass
                    log(f"FFmpeg exited with code {rc}.")
                    if stderr:
                        log(stderr.strip())
                    notify("Recording ended with an FFmpeg error.", "critical")
                break

            time.sleep(0.5)

    except KeyboardInterrupt:
        log("Stopping from keyboard.")
        stop_process(p.pid)

    finally:
        try:
            FFMPEG_PID_FILE.unlink()
        except FileNotFoundError:
            pass
        cleanup()

    return True


# ----------------------------------------------------------------------
# Device listing
# ----------------------------------------------------------------------

def list_devices():
    print(f"{APP_NAME} device information")
    print(f"Platform: {SYSTEM}")
    print()

    if not command_exists("ffmpeg"):
        print("ffmpeg is not installed or not in PATH.")
        return

    if IS_MAC:
        print("macOS AVFoundation devices:")
        print("Run:")
        print("  ffmpeg -f avfoundation -list_devices true -i \"\"")
        print()
        print("The screen device number may differ from the default used by QuickVid.")

    elif IS_WINDOWS:
        print("Windows DirectShow devices:")
        print("Run:")
        print("  ffmpeg -list_devices true -f dshow -i dummy")
        print()
        print("QuickVid currently uses gdigrab for the screen and does not force")
        print("a particular microphone device.")

    elif IS_LINUX:
        print("Linux PulseAudio/PipeWire sinks:")
        print(pactl("list", "short", "sinks"))
        print()
        print("Linux PulseAudio/PipeWire sources:")
        print(pactl("list", "short", "sources"))

    else:
        print("Unsupported operating system.")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def usage():
    print(
        """QuickVid

Usage:
  quickvid_crossplatform.py                 Toggle start/stop
  quickvid_crossplatform.py --start         Start recording
  quickvid_crossplatform.py --stop          Stop recording
  quickvid_crossplatform.py --status        Show status
  quickvid_crossplatform.py --list-devices  Show audio/screen devices

Optional:
  --region X,Y,W,H                          Use a specified rectangle

Examples:
  quickvid_crossplatform.py
  quickvid_crossplatform.py --start --region 100,100,1200,800
  quickvid_crossplatform.py --stop
"""
    )


def main():
    ensure_dirs()

    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        usage()
        return 0

    if "--list-devices" in args:
        list_devices()
        return 0

    if "--status" in args:
        return 0 if status() else 1

    region_arg = None
    if "--region" in args:
        i = args.index("--region")
        if i + 1 >= len(args):
            print("QuickVid: --region requires X,Y,W,H")
            return 2
        region_arg = args[i + 1]

    if "--stop" in args:
        stop_recording()
        return 0

    if "--start" in args:
        return 0 if start_recording(region_arg) else 1

    # IMPORTANT:
    # No arguments deliberately toggles QuickVid, matching the original
    # Linux QuickVid.sh behavior.
    if not args:
        if recording_pid():
            stop_recording()
            return 0
        return 0 if start_recording(region_arg) else 1

    usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
