// Prototype local-file execution owner. Build on an ARM64 Mac with the host SDK:
// xcrun swiftc -swift-version 5 -target arm64-apple-macosx13.0 \
//   -framework AVFoundation -framework Foundation -framework CoreMedia \
//   apps/desktop/src/native/AutPlayAudioHelper.swift -o AutPlayAudioHelper
// stdin/stdout contain only bounded protocol frames; no launch arguments or logs.
import AVFoundation
import CoreMedia
import Darwin
import Foundation

private let protocolVersion: Int64 = 1
private let protocolName: String = "autplay.local-audio"
private let maximumFrameBytes: Int = 65_536
private let maximumQueuedFrames: Int = 32
private let maximumPositionMs: Int64 = 604_800_000

private enum WireFailure: Error { case invalid }

private enum WireValue {
    case string(String)
    case integer(Int64)
    case boolean(Bool)

    var string: String? { if case let .string(value) = self { return value }; return nil }
    var integer: Int64? { if case let .integer(value) = self { return value }; return nil }
    var boolean: Bool? { if case let .boolean(value) = self { return value }; return nil }
}

// The inbound protocol is a flat object of scalars. Reject duplicate keys, numeric
// coercions, arrays, nested objects, nulls and unknown fields before dispatch.
private struct FlatJSON {
    private let bytes: [UInt8]
    private var offset: Int = 0

    init(_ data: Data) throws {
        guard String(data: data, encoding: .utf8) != nil else { throw WireFailure.invalid }
        bytes = Array(data)
    }

    mutating func parse() throws -> [String: WireValue] {
        whitespace()
        try take(123)
        whitespace()
        var result: [String: WireValue] = [:]
        if consume(125) { whitespace(); try requireEnd(); return result }
        while true {
            let key = try string()
            guard result[key] == nil else { throw WireFailure.invalid }
            whitespace()
            try take(58)
            whitespace()
            result[key] = try value()
            whitespace()
            if consume(125) { break }
            try take(44)
            whitespace()
        }
        whitespace()
        try requireEnd()
        return result
    }

    private mutating func value() throws -> WireValue {
        guard offset < bytes.count else { throw WireFailure.invalid }
        if bytes[offset] == 34 { return .string(try string()) }
        if match([116, 114, 117, 101]) { return .boolean(true) }
        if match([102, 97, 108, 115, 101]) { return .boolean(false) }
        let start = offset
        _ = consume(45)
        guard offset < bytes.count, (48...57).contains(bytes[offset]) else { throw WireFailure.invalid }
        if bytes[offset] == 48 {
            offset += 1
        } else {
            while offset < bytes.count, (48...57).contains(bytes[offset]) { offset += 1 }
        }
        guard let token = String(bytes: bytes[start..<offset], encoding: .utf8),
              token != "-0", let number = Int64(token) else { throw WireFailure.invalid }
        return .integer(number)
    }

    private mutating func string() throws -> String {
        let start = offset
        try take(34)
        var escaped = false
        while offset < bytes.count {
            let byte = bytes[offset]
            offset += 1
            if byte < 32 { throw WireFailure.invalid }
            if escaped { escaped = false; continue }
            if byte == 92 { escaped = true; continue }
            if byte == 34 {
                return try JSONDecoder().decode(String.self, from: Data(bytes[start..<offset]))
            }
        }
        throw WireFailure.invalid
    }

    private mutating func whitespace() {
        while offset < bytes.count, [9, 10, 13, 32].contains(bytes[offset]) { offset += 1 }
    }

    private mutating func consume(_ byte: UInt8) -> Bool {
        guard offset < bytes.count, bytes[offset] == byte else { return false }
        offset += 1
        return true
    }

    private mutating func take(_ byte: UInt8) throws {
        guard consume(byte) else { throw WireFailure.invalid }
    }

    private mutating func match(_ value: [UInt8]) -> Bool {
        guard bytes.count - offset >= value.count,
              Array(bytes[offset..<(offset + value.count)]) == value else { return false }
        offset += value.count
        return true
    }

    private func requireEnd() throws { guard offset == bytes.count else { throw WireFailure.invalid } }
}

private struct Command {
    let id: Int64
    let op: String
    let generation: Int64
    let managedPath: String?
    let positionMs: Int64?
    let autoplay: Bool?

    init(_ frame: [String: WireValue]) throws {
        guard frame["v"]?.integer == protocolVersion, frame["type"]?.string == "command",
              let id = frame["id"]?.integer, id > 0,
              let generation = frame["generation"]?.integer, generation >= 0,
              let op = frame["op"]?.string,
              ["open", "play", "pause", "seek", "stop", "close"].contains(op) else {
            throw WireFailure.invalid
        }
        var expected: Set<String> = ["v", "type", "id", "op", "generation"]
        if op == "open" { expected.formUnion(["managedPath", "positionMs", "autoplay"]) }
        if op == "seek" { expected.insert("positionMs") }
        guard Set(frame.keys) == expected else { throw WireFailure.invalid }
        if op == "open" {
            guard frame["managedPath"]?.string != nil, frame["autoplay"]?.boolean != nil else {
                throw WireFailure.invalid
            }
        }
        if op == "open" || op == "seek" {
            guard let position = frame["positionMs"]?.integer, position >= 0,
                  position <= maximumPositionMs else { throw WireFailure.invalid }
        }
        self.id = id
        self.op = op
        self.generation = generation
        managedPath = frame["managedPath"]?.string
        positionMs = frame["positionMs"]?.integer
        autoplay = frame["autoplay"]?.boolean
    }
}

private func onMain(_ action: @escaping () -> Void) {
    if Thread.isMainThread { action() } else { DispatchQueue.main.async(execute: action) }
}

private final class FramedPipe {
    private let writer: DispatchQueue = DispatchQueue(label: "app.autplay.local-audio.stdout")
    private let reader: DispatchQueue = DispatchQueue(label: "app.autplay.local-audio.stdin")
    private let lock: NSLock = NSLock()
    private let inboundSlots: DispatchSemaphore = DispatchSemaphore(value: maximumQueuedFrames)
    private var outboundCount: Int = 0
    private var finishing: Bool = false
    private var outputBroken: Bool = false
    var onFrame: (([String: WireValue]) -> Void)?
    var onEOF: (() -> Void)?
    var onFailure: (() -> Void)?

    init() {
        signal(SIGPIPE, SIG_IGN)
        let flags = fcntl(STDOUT_FILENO, F_GETFL)
        guard flags >= 0, fcntl(STDOUT_FILENO, F_SETFL, flags | O_NONBLOCK) >= 0 else { exit(2) }
    }

    func start() {
        reader.async { [weak self] in self?.readFrames() }
    }

    func send(_ frame: [String: Any]) {
        guard let payload = try? JSONSerialization.data(withJSONObject: frame, options: [.sortedKeys]),
              !payload.isEmpty, payload.count <= maximumFrameBytes else { fail(); return }
        var length = UInt32(payload.count).bigEndian
        var data = withUnsafeBytes(of: &length) { Data($0) }
        data.append(payload)
        lock.lock()
        if finishing { lock.unlock(); return }
        guard outboundCount < maximumQueuedFrames else { lock.unlock(); fail(); return }
        outboundCount += 1
        lock.unlock()
        writer.async { [weak self] in
            guard let self = self else { return }
            self.lock.lock()
            let alreadyBroken = self.outputBroken
            self.lock.unlock()
            let succeeded = alreadyBroken ? false : self.write(data)
            self.lock.lock()
            self.outboundCount -= 1
            if !succeeded { self.outputBroken = true }
            self.lock.unlock()
            if !succeeded && !alreadyBroken { self.fail() }
        }
    }

    // A serial sentinel exits only after all previously queued frames have been
    // written, each with a bounded deadline. The JVM also has a process deadline.
    func finish(_ code: Int32) {
        lock.lock()
        guard !finishing else { lock.unlock(); return }
        finishing = true
        lock.unlock()
        writer.async { [self] in
            lock.lock()
            let finalCode: Int32 = outputBroken ? 2 : code
            lock.unlock()
            exit(finalCode)
        }
    }

    private func fail() { onMain { [weak self] in self?.onFailure?() } }

    private func readFrames() {
        while true {
            do {
                guard let header = try readExactly(4, allowEOF: true) else {
                    onMain { [weak self] in self?.onEOF?() }
                    return
                }
                let size = header.reduce(0) { ($0 << 8) | Int($1) }
                guard size > 0, size <= maximumFrameBytes,
                      let payload = try readExactly(size, allowEOF: false) else { throw WireFailure.invalid }
                var parser = try FlatJSON(payload)
                let frame = try parser.parse()
                guard inboundSlots.wait(timeout: .now()) == .success else { throw WireFailure.invalid }
                DispatchQueue.main.async { [weak self] in
                    guard let self = self else { return }
                    defer { self.inboundSlots.signal() }
                    self.onFrame?(frame)
                }
            } catch { fail(); return }
        }
    }

    private func readExactly(_ count: Int, allowEOF: Bool) throws -> Data? {
        var bytes = [UInt8](repeating: 0, count: count)
        var offset = 0
        while offset < count {
            let received: Int = bytes.withUnsafeMutableBytes { buffer in
                Darwin.read(STDIN_FILENO, buffer.baseAddress!.advanced(by: offset), count - offset)
            }
            if received == 0 {
                if offset == 0 && allowEOF { return nil }
                throw WireFailure.invalid
            }
            if received < 0 { if errno == EINTR { continue }; throw WireFailure.invalid }
            offset += received
        }
        return Data(bytes)
    }

    private func write(_ data: Data) -> Bool {
        let deadline = ProcessInfo.processInfo.systemUptime + 2
        var offset = 0
        while offset < data.count {
            guard ProcessInfo.processInfo.systemUptime < deadline else { return false }
            let sent: Int = data.withUnsafeBytes { buffer in
                Darwin.write(STDOUT_FILENO, buffer.baseAddress!.advanced(by: offset), data.count - offset)
            }
            if sent > 0 { offset += sent; continue }
            if sent < 0 && errno == EINTR { continue }
            guard sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) else { return false }
            var descriptor = pollfd(fd: STDOUT_FILENO, events: Int16(POLLOUT), revents: 0)
            let remaining = max(1, min(100, Int((deadline - ProcessInfo.processInfo.systemUptime) * 1000)))
            let ready = Darwin.poll(&descriptor, 1, Int32(remaining))
            if ready < 0 && errno != EINTR { return false }
            if (descriptor.revents & Int16(POLLERR | POLLHUP | POLLNVAL)) != 0 { return false }
        }
        return true
    }
}

private func metadataText(_ value: String?) -> String? {
    guard let value = value else { return nil }
    let cleaned = String(value.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) })
        .trimmingCharacters(in: .whitespacesAndNewlines)
    // Tags are untrusted. Avoid emitting path/URL-shaped tags or prefixes of them.
    guard !cleaned.isEmpty, !cleaned.contains("/"), !cleaned.contains("\\"),
          !cleaned.contains(":"), !cleaned.hasPrefix("~") else { return nil }
    var result = ""
    for character in cleaned {
        let next = String(character)
        if result.utf8.count + next.utf8.count > 256 { break }
        result += next
    }
    return result.isEmpty ? nil : result
}

private func mediaFailure(_ error: Error?, fallback: String) -> String {
    var current: NSError? = error as NSError?
    let unsupported: Set<Int> = [AVError.Code.decoderNotFound.rawValue,
                                 AVError.Code.fileFormatNotRecognized.rawValue,
                                 AVError.Code.formatUnsupported.rawValue,
                                 AVError.Code.contentIsProtected.rawValue]
    for _ in 0..<3 {
        guard let native = current else { break }
        if native.domain == AVFoundationErrorDomain && unsupported.contains(native.code) {
            return "NATIVE_UNSUPPORTED_MEDIA"
        }
        current = native.userInfo[NSUnderlyingErrorKey] as? NSError
    }
    return fallback
}

private final class LocalAudioOwner {
    private let pipe: FramedPipe
    private let player: AVPlayer = AVPlayer()
    private var handshaken: Bool = false
    private var closing: Bool = false
    private var teardownFinished: Bool = false
    private var lastCommandID: Int64 = 0
    private var generation: Int64?
    private var lastOpenedGeneration: Int64?
    private var itemToken: UInt64 = 0
    private var seekToken: UInt64 = 0
    private var asset: AVURLAsset?
    private var item: AVPlayerItem?
    private var loadTask: Task<Void, Never>?
    private var metadataTask: Task<Void, Never>?
    private var outstandingTasks: [UInt64: Task<Void, Never>] = [:]
    private var nextTaskID: UInt64 = 0
    private var itemObservation: NSKeyValueObservation?
    private var playerObservation: NSKeyValueObservation?
    private var notificationObservers: [NSObjectProtocol] = []
    private var clock: Timer?
    private var helloDeadline: Double = 0
    private var openDeadline: Double?
    private var seekDeadline: Double?
    private var playDeadline: Double?
    private var loading: Bool = false
    private var seekInFlight: Bool = false
    private var wantsPlaying: Bool = false
    private var playedSinceOpen: Bool = false
    private var lastProgressAt: Double = 0
    private var lastProgressMs: Int64 = 0
    private var desiredPositionMs: Int64 = 0
    private var durationMs: Int64?
    private var title: String?
    private var artist: String?
    private var status: String = "IDLE"
    private var errorCode: String?
    private var endedEmitted: Bool = false
    private var lastEvent: Data?

    init(pipe: FramedPipe) { self.pipe = pipe }

    func start() {
        precondition(Thread.isMainThread)
        player.actionAtItemEnd = .pause
        player.automaticallyWaitsToMinimizeStalling = true
        pipe.onFrame = { [weak self] frame in self?.receive(frame) }
        pipe.onEOF = { [weak self] in self?.shutdown(id: nil, code: 0) }
        pipe.onFailure = { [weak self] in self?.shutdown(id: nil, code: 2) }
        helloDeadline = now + 5
        pipe.send(["v": protocolVersion, "type": "hello", "protocol": protocolName,
                   "maxFrameBytes": maximumFrameBytes])
        playerObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            onMain { [weak self] in self?.observeTransport() }
        }
        clock = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(clock!, forMode: .common)
        pipe.start()
    }

    private var now: Double { ProcessInfo.processInfo.systemUptime }

    private func receive(_ frame: [String: WireValue]) {
        guard !closing else { return }
        if !handshaken {
            guard Set(frame.keys) == ["v", "type", "protocol", "maxFrameBytes"],
                  frame["v"]?.integer == protocolVersion, frame["type"]?.string == "hello",
                  frame["protocol"]?.string == protocolName,
                  frame["maxFrameBytes"]?.integer == Int64(maximumFrameBytes) else {
                shutdown(id: nil, code: 2); return
            }
            handshaken = true
            pipe.send(["v": protocolVersion, "type": "ready"])
            return
        }
        guard let command = try? Command(frame), command.id > lastCommandID else {
            shutdown(id: nil, code: 2); return
        }
        lastCommandID = command.id
        if command.op == "close" { shutdown(id: command.id, code: 0); return }
        pipe.send(["v": protocolVersion, "type": "ack", "id": command.id])
        if command.op == "open" {
            if let previous = lastOpenedGeneration, command.generation <= previous { return }
            open(command)
            return
        }
        guard command.generation == generation else { return }
        // EOF is terminal for this selected entry. Queue repeat/replay opens a
        // fresh generation; delayed metadata or transport callbacks cannot repeat EOF.
        if endedEmitted && ["play", "pause", "seek"].contains(command.op) { return }
        switch command.op {
        case "play":
            guard asset != nil, errorCode == nil else { return }
            wantsPlaying = true
            if !loading && !seekInFlight { startPlaying() }
        case "pause":
            wantsPlaying = false
            playDeadline = nil
            player.pause()
            if !loading && !seekInFlight && status != "ENDED" { observeTransport() }
        case "seek":
            desiredPositionMs = command.positionMs!
            if item?.status == .readyToPlay { performSeek() }
        case "stop":
            releaseItem()
            durationMs = nil
            title = nil
            artist = nil
            errorCode = nil
            status = "STOPPED"
            emit(force: true)
        default: break
        }
    }

    private func open(_ command: Command) {
        guard outstandingTasks.count <= maximumQueuedFrames - 2 else {
            shutdown(id: nil, code: 2); return
        }
        releaseItem()
        generation = command.generation
        lastOpenedGeneration = command.generation
        desiredPositionMs = command.positionMs!
        wantsPlaying = command.autoplay!
        durationMs = nil
        title = nil
        artist = nil
        errorCode = nil
        endedEmitted = false
        status = "LOADING"
        loading = true
        openDeadline = now + 10
        emit(force: true)
        let path = command.managedPath!
        guard path.hasPrefix("/"), !path.contains("\0"), path.utf8.count <= 4096 else {
            fail("NATIVE_INVALID_PATH"); return
        }
        let url = URL(fileURLWithPath: path, isDirectory: false).standardizedFileURL
        // Managed copies have a neutral .media suffix. AVFoundation must inspect
        // actual media; extension admission neither proves nor rejects a codec.
        guard url.isFileURL else { fail("NATIVE_INVALID_PATH"); return }
        do {
            let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
            guard values.isRegularFile == true, values.isSymbolicLink != true,
                  let size = values.fileSize, size > 0,
                  FileManager.default.isReadableFile(atPath: url.path),
                  url.resolvingSymlinksInPath().path == url.path else {
                fail("NATIVE_FILE_UNAVAILABLE"); return
            }
        } catch { fail("NATIVE_FILE_UNAVAILABLE"); return }
        let newAsset = AVURLAsset(url: url, options: [
            AVURLAssetPreferPreciseDurationAndTimingKey: true,
            AVURLAssetReferenceRestrictionsKey: NSNumber(value: AVAssetReferenceRestrictions.forbidAll.rawValue)
        ])
        asset = newAsset
        let token = itemToken
        loadTask = Task { @MainActor [weak self] in
            guard !Task.isCancelled else { return }
            do {
                let playable = try await newAsset.load(.isPlayable)
                let protected = try await newAsset.load(.hasProtectedContent)
                let duration = try await newAsset.load(.duration)
                let tracks = try await newAsset.loadTracks(withMediaType: .audio)
                guard !Task.isCancelled, let self = self, self.matches(token) else { return }
                let seconds = CMTimeGetSeconds(duration)
                guard playable, !protected, !tracks.isEmpty, seconds.isFinite, seconds >= 0.001,
                      seconds <= Double(maximumPositionMs) / 1000 else {
                    self.fail("NATIVE_UNSUPPORTED_MEDIA"); return
                }
                self.durationMs = Int64(seconds * 1000)
                self.installItem(newAsset, token: token)
            } catch {
                guard !Task.isCancelled, let self = self, self.matches(token) else { return }
                self.fail(mediaFailure(error, fallback: "NATIVE_OPEN_FAILED"))
            }
        }
        trackTask(loadTask!)
    }

    private func installItem(_ newAsset: AVURLAsset, token: UInt64) {
        let newItem = AVPlayerItem(asset: newAsset)
        item = newItem
        itemObservation = newItem.observe(\.status, options: [.initial, .new]) { [weak self] _, _ in
            DispatchQueue.main.async { [weak self] in
                guard let self = self, self.matches(token), self.item === newItem else { return }
                if newItem.status == .failed {
                    self.fail(mediaFailure(newItem.error,
                                           fallback: self.loading ? "NATIVE_OPEN_FAILED" : "NATIVE_PLAYBACK_FAILED"))
                }
                else if newItem.status == .readyToPlay && self.loading && !self.seekInFlight {
                    self.performSeek()
                }
            }
        }
        let center = NotificationCenter.default
        notificationObservers.append(center.addObserver(forName: .AVPlayerItemDidPlayToEndTime,
                                                        object: newItem, queue: .main) { [weak self] _ in
            self?.didEnd(newItem, token: token)
        })
        notificationObservers.append(center.addObserver(forName: .AVPlayerItemFailedToPlayToEndTime,
                                                        object: newItem, queue: .main) { [weak self] _ in
            guard let self = self, self.matches(token), self.item === newItem else { return }
            self.fail(mediaFailure(newItem.error, fallback: "NATIVE_PLAYBACK_FAILED"))
        })
        player.replaceCurrentItem(with: newItem)
        loadTask = nil
        loadMetadata(newAsset, token: token)
    }

    private func loadMetadata(_ newAsset: AVURLAsset, token: UInt64) {
        metadataTask = Task { @MainActor [weak self] in
            guard !Task.isCancelled else { return }
            do {
                let metadata = try await newAsset.load(.commonMetadata)
                let limited = Array(metadata.prefix(128))
                let titleItem = AVMetadataItem.metadataItems(from: limited, filteredByIdentifier: .commonIdentifierTitle).first
                let artistItem = AVMetadataItem.metadataItems(from: limited, filteredByIdentifier: .commonIdentifierArtist).first
                var loadedTitle: String?
                var loadedArtist: String?
                if let titleItem = titleItem { loadedTitle = try await titleItem.load(.stringValue) }
                if let artistItem = artistItem { loadedArtist = try await artistItem.load(.stringValue) }
                guard !Task.isCancelled, let self = self, self.matches(token) else { return }
                self.title = metadataText(loadedTitle)
                self.artist = metadataText(loadedArtist)
                if !self.endedEmitted { self.emit() }
            } catch { /* Metadata is optional; raw errors and filenames are never emitted. */ }
        }
        trackTask(metadataTask!)
        // Metadata must not hold the execution owner or close open indefinitely.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self = self, self.matches(token) else { return }
            self.metadataTask?.cancel()
            self.metadataTask = nil
        }
    }

    private func trackTask(_ task: Task<Void, Never>) {
        nextTaskID &+= 1
        let id = nextTaskID
        outstandingTasks[id] = task
        Task { @MainActor [weak self] in
            await task.value
            self?.outstandingTasks.removeValue(forKey: id)
        }
    }

    private func performSeek() {
        guard let current = item, current.status == .readyToPlay, errorCode == nil else { return }
        seekToken &+= 1
        let operation = seekToken
        let token = itemToken
        seekInFlight = true
        current.cancelPendingSeeks()
        player.pause()
        playDeadline = nil
        openDeadline = nil
        seekDeadline = now + 5
        let target = min(desiredPositionMs, durationMs ?? desiredPositionMs)
        status = "SEEKING"
        emit(force: true)
        player.seek(to: CMTime(value: target, timescale: 1000),
                    toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
            onMain { [weak self] in
                guard let self = self, self.matches(token), self.seekToken == operation,
                      self.item === current, self.seekInFlight else { return }
                self.seekInFlight = false
                self.seekDeadline = nil
                guard finished, self.position() != nil else { self.fail("NATIVE_SEEK_FAILED"); return }
                self.loading = false
                self.openDeadline = nil
                self.status = "READY"
                self.emit(force: true)
                if self.wantsPlaying { self.startPlaying() }
                else { self.observeTransport() }
            }
        }
    }

    private func startPlaying() {
        guard item?.status == .readyToPlay, errorCode == nil, !loading, !seekInFlight else { return }
        wantsPlaying = true
        playDeadline = now + 5
        lastProgressAt = now
        lastProgressMs = position() ?? 0
        player.play()
        observeTransport()
    }

    private func observeTransport() {
        guard !closing, item != nil, !loading, !seekInFlight, errorCode == nil else { return }
        if status == "ENDED" && !wantsPlaying { return }
        if player.timeControlStatus == .playing && player.rate > 0 {
            status = "PLAYING"
            playedSinceOpen = true
            playDeadline = nil
        } else if player.timeControlStatus == .waitingToPlayAtSpecifiedRate {
            status = "READY"
        } else {
            status = "PAUSED"
        }
        emit()
    }

    private func didEnd(_ finishedItem: AVPlayerItem, token: UInt64) {
        guard matches(token), item === finishedItem, !loading, !seekInFlight,
              !endedEmitted, errorCode == nil,
              let duration = durationMs, let actual = position(), actual >= max(0, duration - 250) else { return }
        endedEmitted = true
        wantsPlaying = false
        playDeadline = nil
        player.pause()
        status = "ENDED"
        emit(force: true)
    }

    private func tick() {
        guard !closing else { return }
        if !handshaken {
            if now >= helloDeadline { shutdown(id: nil, code: 2) }
            return
        }
        if let deadline = openDeadline, now >= deadline { fail("NATIVE_OPEN_TIMEOUT"); return }
        if let deadline = seekDeadline, now >= deadline { fail("NATIVE_SEEK_TIMEOUT"); return }
        if let deadline = playDeadline, now >= deadline { fail("NATIVE_PLAYBACK_FAILED"); return }
        guard item != nil, !loading, !seekInFlight, errorCode == nil else { return }
        if item?.status == .failed {
            fail(mediaFailure(item?.error, fallback: "NATIVE_PLAYBACK_FAILED")); return
        }
        if wantsPlaying {
            let actual = position() ?? lastProgressMs
            if actual > lastProgressMs { lastProgressMs = actual; lastProgressAt = now }
            if playedSinceOpen && now - lastProgressAt >= 10 { fail("NATIVE_PLAYBACK_FAILED"); return }
        }
        observeTransport()
    }

    private func matches(_ token: UInt64) -> Bool { !closing && token == itemToken && asset != nil }

    private func position() -> Int64? {
        guard item != nil else { return 0 }
        let seconds = CMTimeGetSeconds(player.currentTime())
        guard seconds.isFinite, seconds >= 0, seconds < Double(Int64.max) / 1000 else { return nil }
        return min(Int64(seconds * 1000), maximumPositionMs)
    }

    private func emit(force: Bool = false, fixedPosition: Int64? = nil) {
        guard let generation = generation else { return }
        if status == "ENDED" && !force { return }
        let actuallyPlaying = item != nil && player.timeControlStatus == .playing && player.rate > 0
        if status == "PLAYING" && !actuallyPlaying {
            status = player.timeControlStatus == .waitingToPlayAtSpecifiedRate ? "READY" : "PAUSED"
        }
        let wireStatus: String
        switch status {
        case "LOADING", "SEEKING": wireStatus = "BUFFERING"
        case "PLAYING", "PAUSED", "READY": wireStatus = "READY"
        case "STOPPED": wireStatus = "IDLE"
        default: wireStatus = status
        }
        var event: [String: Any] = ["v": protocolVersion, "type": "event", "generation": generation,
                                  "status": wireStatus, "positionMs": fixedPosition ?? position() ?? 0,
                                  "isPlaying": wireStatus == "READY" && actuallyPlaying]
        if let duration = durationMs { event["durationMs"] = duration }
        if let error = errorCode { event["errorCode"] = error }
        if let title = title { event["title"] = title }
        if let artist = artist { event["artist"] = artist }
        guard let encoded = try? JSONSerialization.data(withJSONObject: event, options: [.sortedKeys]) else {
            shutdown(id: nil, code: 2); return
        }
        if force || encoded != lastEvent { lastEvent = encoded; pipe.send(event) }
    }

    private func fail(_ code: String) {
        guard !closing, errorCode == nil else { return }
        let finalPosition = position() ?? 0
        releaseItem()
        errorCode = code
        status = "ERROR"
        emit(force: true, fixedPosition: finalPosition)
    }

    @discardableResult
    private func releaseItem() -> [Task<Void, Never>] {
        let cancelledTasks: [Task<Void, Never>] = [loadTask, metadataTask].compactMap { $0 }
        itemToken &+= 1
        seekToken &+= 1
        // Suppress synchronous transport observation while the current item is
        // being paused and removed; the caller emits the resulting terminal fact.
        seekInFlight = true
        loadTask?.cancel()
        loadTask = nil
        metadataTask?.cancel()
        metadataTask = nil
        asset?.cancelLoading()
        player.pause()
        item?.cancelPendingSeeks()
        itemObservation?.invalidate()
        itemObservation = nil
        for observer in notificationObservers { NotificationCenter.default.removeObserver(observer) }
        notificationObservers.removeAll()
        player.cancelPendingPrerolls()
        player.replaceCurrentItem(with: nil)
        item = nil
        asset = nil
        loading = false
        seekInFlight = false
        wantsPlaying = false
        playedSinceOpen = false
        openDeadline = nil
        seekDeadline = nil
        playDeadline = nil
        return cancelledTasks
    }

    private func shutdown(id: Int64?, code: Int32) {
        guard !closing else { return }
        closing = true
        // Keep the otherwise idle CLI run loop alive until cancellation and the
        // stdout drain finish. tick() becomes a no-op; the writer sentinel exits.
        releaseItem()
        // Include canceled tasks from superseded generations, not only tasks
        // retained by the current item. Their registry is bounded at 32 owners.
        let cancelledTasks = Array(outstandingTasks.values)
        for task in cancelledTasks { task.cancel() }
        playerObservation?.invalidate()
        playerObservation = nil
        // Wait for our asynchronous load owners to acknowledge cancellation.
        // If native cancellation fails to settle, do not send a successful close
        // acknowledgement; exit under a deadline and let the JVM finish teardown.
        Task { @MainActor [weak self] in
            for task in cancelledTasks { await task.value }
            self?.finishShutdown(id: id, code: code)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.finishShutdown(id: nil, code: 2)
        }
    }

    private func finishShutdown(id: Int64?, code: Int32) {
        guard !teardownFinished else { return }
        teardownFinished = true
        if let id = id {
            errorCode = nil
            status = "CLOSED"
            emit(force: true)
            pipe.send(["v": protocolVersion, "type": "ack", "id": id])
        }
        pipe.finish(code)
    }
}

// Suppress native diagnostic text, which can otherwise include a file capability.
private let diagnosticSink: Int32 = Darwin.open("/dev/null", O_WRONLY)
if diagnosticSink >= 0 { _ = dup2(diagnosticSink, STDERR_FILENO); Darwin.close(diagnosticSink) }
guard CommandLine.arguments.count == 1 else { exit(2) }
private let framedPipe: FramedPipe = FramedPipe()
private let audioOwner: LocalAudioOwner = LocalAudioOwner(pipe: framedPipe)
audioOwner.start()
RunLoop.main.run()
