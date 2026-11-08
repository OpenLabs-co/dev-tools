// ttsd — background helper for Claude Code voice (speak.py).
//  • Mic watcher: when any app starts recording (e.g. holding Space for Claude Code
//    dictation) speech stops immediately and the queue waits; it resumes when the mic closes.
//  • Double-tap a modifier on its own (needs Input Monitoring permission):
//      Control ×2  shh — stop and clear the queue
//      Option  ×2  skip to the next reply
//      Shift   ×2  replay the last reply
//  • Global hotkeys (no permission needed):
//      ⌃⌥T  voice on/off   (also ⌃⌥S stop, ⌃⌥N skip, ⌃⌥R replay)
// Build: swiftc -O ttsd.swift -o ttsd   (install.sh --hotkeys does this and loads ~/Library/LaunchAgents/com.openlabs.kokoro-voice.plist)

import AppKit
import Carbon
import CoreAudio

let home = FileManager.default.homeDirectoryForCurrentUser.path
let python = "\(home)/.claude/tts/venv/bin/python"
let script = "\(home)/.claude/tts/speak.py"

func speak(_ mode: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: python)
    p.arguments = [script, mode]
    try? p.run()
    NSLog("ttsd: \(mode)")
}

// MARK: - Mic watcher

var inputDevice = AudioObjectID(0)
var micOn = false

func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

func defaultInput() -> AudioObjectID {
    var dev = AudioObjectID(0)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    var a = address(kAudioHardwarePropertyDefaultInputDevice)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &dev)
    return dev
}

func isRunning(_ dev: AudioObjectID) -> Bool {
    var running: UInt32 = 0
    var size = UInt32(4)
    var a = address(kAudioDevicePropertyDeviceIsRunningSomewhere)
    AudioObjectGetPropertyData(dev, &a, 0, nil, &size, &running)
    return running != 0
}

func checkMic() {
    let now = isRunning(inputDevice)
    guard now != micOn else { return }
    micOn = now
    speak(now ? "mic-on" : "mic-off")
}

let runningListener: AudioObjectPropertyListenerBlock = { _, _ in
    DispatchQueue.main.async { checkMic() }
}

func watch(_ dev: AudioObjectID) {
    var a = address(kAudioDevicePropertyDeviceIsRunningSomewhere)
    if inputDevice != 0 {
        AudioObjectRemovePropertyListenerBlock(inputDevice, &a, DispatchQueue.main, runningListener)
    }
    inputDevice = dev
    AudioObjectAddPropertyListenerBlock(dev, &a, DispatchQueue.main, runningListener)
    checkMic()
}

// MARK: - Hotkeys

let hotkeys: [(key: Int, mode: String)] = [
    (kVK_ANSI_S, "shh"), (kVK_ANSI_N, "skip"), (kVK_ANSI_R, "replay"), (kVK_ANSI_T, "toggle"),
]

func registerHotkeys() {
    var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
    InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
        var id = EventHotKeyID()
        GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                          nil, MemoryLayout<EventHotKeyID>.size, nil, &id)
        speak(hotkeys[Int(id.id)].mode)
        return noErr
    }, 1, &spec, nil, nil)
    for (i, hk) in hotkeys.enumerated() {
        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(UInt32(hk.key), UInt32(controlKey | optionKey),
                                         EventHotKeyID(signature: OSType(0x7474_7364), id: UInt32(i)),
                                         GetApplicationEventTarget(), 0, &ref)
        if status != noErr { NSLog("ttsd: hotkey for \(hk.mode) unavailable (\(status))") }
    }
}

// MARK: - Double-tap modifiers

let modifierMask: CGEventFlags = [.maskControl, .maskAlternate, .maskShift, .maskCommand]
let doubleTaps: [(flag: CGEventFlags, mode: String)] = [
    (.maskControl, "shh"), (.maskAlternate, "skip"), (.maskShift, "replay"),
]
var downFlag: CGEventFlags = []   // modifier currently held alone, if any
var downAt = 0.0
var lastTapFlag: CGEventFlags = []
var lastTapAt = 0.0
var tap: CFMachPort?
var loggedWaiting = false

func handle(_ type: CGEventType, _ event: CGEvent) {
    let now = ProcessInfo.processInfo.systemUptime
    guard type == .flagsChanged else {
        // Any real key press means the modifier was part of a shortcut, not a tap.
        downFlag = []; lastTapFlag = []
        return
    }
    let flags = event.flags.intersection(modifierMask)
    if let match = doubleTaps.first(where: { flags == $0.flag }) {
        downFlag = match.flag; downAt = now           // pressed on its own
        return
    }
    if flags.isEmpty, !downFlag.isEmpty, now - downAt < 0.3 {   // released quickly = a tap
        if downFlag == lastTapFlag, now - lastTapAt < 0.45 {
            if let match = doubleTaps.first(where: { $0.flag == downFlag }) { speak(match.mode) }
            lastTapFlag = []
        } else {
            lastTapFlag = downFlag; lastTapAt = now
        }
    } else if !flags.isEmpty {
        lastTapFlag = []                               // two modifiers together: a chord
    }
    downFlag = []
}

func startTap() {
    let mask = (1 << CGEventType.flagsChanged.rawValue) | (1 << CGEventType.keyDown.rawValue)
    tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                            eventsOfInterest: CGEventMask(mask), callback: { _, type, event, _ in
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
        } else {
            handle(type, event)
        }
        return Unmanaged.passUnretained(event)
    }, userInfo: nil)
    guard let tap else {
        // No Input Monitoring permission yet: ask once, then keep retrying quietly.
        if !loggedWaiting { NSLog("ttsd: double-tap needs Input Monitoring permission — waiting") }
        loggedWaiting = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { startTap() }
        return
    }
    CFRunLoopAddSource(CFRunLoopGetMain(), CFMachPortCreateRunLoopSource(nil, tap, 0), .commonModes)
    NSLog("ttsd: double-tap modifiers active")
}

// MARK: - Main

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
speak("mic-off")  // clear any flag left by a previous run
watch(defaultInput())
var defaultAddr = address(kAudioHardwarePropertyDefaultInputDevice)
AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &defaultAddr, DispatchQueue.main) { _, _ in
    watch(defaultInput())
}
registerHotkeys()
if !CGPreflightListenEventAccess() { CGRequestListenEventAccess() }
startTap()
NSLog("ttsd: running")
app.run()
