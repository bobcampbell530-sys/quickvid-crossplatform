# QuickVid Cross-Platform Screen Recorder

A simple cross-platform screen recorder built with Python and ffmpeg. Preserves the original Linux QuickVid behavior while adding macOS and Windows support.


## Platform Dependencies

### Linux
```
  - ffmpeg
  - slop          (sudo apt install slop)
  - zenity        (sudo apt install zenity)
  - pactl         (sudo apt install pulseaudio-utils)
```

### macOS
```
  - ffmpeg        (brew install ffmpeg)
```

### Windows
```
  - ffmpeg
```

## Platform-Specific Notes

### Linux
- Uses **slop** for drag-to-select region (matches original QuickVid)
- Uses **zenity** for audio, timer, and filename dialogs
- Uses **pactl** (PulseAudio/PipeWire) for audio device selection
- Audio modes: no audio, system audio, microphone, both, or BX29 headphones

### macOS
- Uses **AVFoundation** for screen capture
- Rectangle must be supplied via `--region X,Y,W,H`
- macOS AVFoundation device numbering can vary. List available devices with:
  ```bash
  ffmpeg -f avfoundation -list_devices true -i ""
  ```
- Adjust the video device index in `build_mac_ffmpeg()` if the default (`1`) doesn't match your screen

### Windows
- Uses **gdigrab** for screen capture
- Rectangle must be supplied via `--region X,Y,W,H`
- Microphone device selection is platform-dependent

## License

MIT
