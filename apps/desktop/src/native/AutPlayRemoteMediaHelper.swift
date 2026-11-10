// ADR-060 / M1 source owner. Standalone Swift 5, ARM64, macOS 13+.
// This file is deliberately separate from local-audio IPC1 and is not packaged yet.
// No execution, codec, ATS, cancellation, or signed-package qualification is implied.
// Primary API references used in source review (no API probes):
// https://developer.apple.com/documentation/foundation/performing-manual-server-trust-authentication
// https://developer.apple.com/documentation/security/sectrustsetanchorcertificatesonly(_:_:)
// https://developer.apple.com/documentation/avfoundation/loading-media-data-asynchronously
// https://www.rfc-editor.org/rfc/rfc5280.html
import Foundation
import AVFoundation
import CoreMedia
import AppKit
import Network
import Security
import CryptoKit
import Darwin

private let maxInteger: Int64 = 9_007_199_254_740_991
private let maxObject: Int64 = 2_147_483_648
private let maxPosition: Int64 = 604_800_000
private let maxRange: Int64 = 262_144
private let maxData = 61_440
private let null = NSNull()

private enum Failure: String, Error {
    case auth = "AUTH_REQUIRED", denied = "AUTHORIZATION_DENIED", source = "SOURCE_UNKNOWN"
    case offline = "OFFLINE", capability = "CAPABILITY_UNAVAILABLE", waiting = "RESOURCE_WAITING"
    case resource = "RESOURCE_STALE", tls = "TLS_UNTRUSTED", origin = "ORIGIN_REJECTED"
    case redirect = "REDIRECT_REJECTED", representation = "REPRESENTATION_INVALID"
    case changed = "REPRESENTATION_CHANGED", range = "RANGE_INVALID", timeout = "NETWORK_TIMEOUT"
    case dropped = "NETWORK_DROPPED", media = "UNSUPPORTED_MEDIA", seek = "SEEK_FAILED"
    case interrupted = "SEEK_INTERRUPTED", storage = "STORAGE_FULL", checksum = "CHECKSUM_MISMATCH"
    case publication = "PUBLICATION_UNCERTAIN", cancelled = "CANCELLED", stale = "OWNER_STALE"
    case unavailable = "OWNER_UNAVAILABLE", reconciliation = "RECONCILIATION_REQUIRED"
    case renewal = "ACCESS_RENEWAL_FAILED", intent = "INTENT_STALE", wire = "PROTOCOL_ERROR"
    case capacity = "CAPACITY_EXCEEDED", drain = "DRAIN_UNCONFIRMED"
}

private func next(_ value: Int64) throws -> Int64 {
    guard value >= 0, value < maxInteger else { throw Failure.wire }
    return value + 1
}

private func matches(_ text: String, _ pattern: String) -> Bool {
    guard let range = text.range(of: pattern, options: .regularExpression) else { return false }
    return range.lowerBound == text.startIndex && range.upperBound == text.endIndex
}
private func uuid(_ value: String) -> Bool {
    matches(value, "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
}
private func strongETag(_ value: String) -> Bool { matches(value, "^\"sha256-[0-9a-f]{64}\"$") }
private func wipe(_ data: inout Data) { data.resetBytes(in: 0..<data.count); data.removeAll(keepingCapacity: false) }

private struct Instant {
    let mono: Double = ProcessInfo.processInfo.systemUptime
    let wall: Double = Date().timeIntervalSince1970 * 1000
}

private struct Deadline {
    let absolute: Int64
    let monotonic: Double
    init(absolute: Int64, remaining: Int64, received: Instant, maximum: Int64 = 10_000) throws {
        guard absolute > 0, absolute <= maxInteger, remaining > 0, remaining <= maximum else { throw Failure.wire }
        self.absolute = absolute
        monotonic = received.mono + Double(remaining) / 1000
        guard live() else { throw Failure.stale }
    }
    func live(margin: Double = 1) -> Bool {
        let now = Instant()
        return now.wall + margin * 1000 < Double(absolute) && now.mono + margin < monotonic
    }
    func contains(_ other: Deadline) -> Bool { other.absolute <= absolute && other.monotonic <= monotonic }
    func capped(seconds: Double) -> Deadline { Deadline(absolute: absolute, monotonic: min(monotonic, Instant().mono + seconds)) }
    private init(absolute: Int64, monotonic: Double) { self.absolute = absolute; self.monotonic = monotonic }
}

private enum Scalar {
    case text(String), integer(Int64), boolean(Bool), none
    var string: String? { if case let .text(v) = self { return v }; return nil }
    var number: Int64? { if case let .integer(v) = self { return v }; return nil }
    var bool: Bool? { if case let .boolean(v) = self { return v }; return nil }
    var isNull: Bool { if case .none = self { return true }; return false }
}

// Token parser, rather than NSNumber coercion or JSONSerialization's duplicate-key collapse.
// Only the individual quoted string is decoded by JSONDecoder; surrogate pairs are checked first.
private struct FlatJSON {
    var bytes: Data
    var index = 1
    init(_ bytes: Data) throws {
        guard bytes.first == 1, String(data: bytes.dropFirst(), encoding: .utf8) != nil else { throw Failure.wire }
        self.bytes = bytes
    }
    mutating func parse() throws -> [String: Scalar] {
        defer { wipe(&bytes) }
        ws(); try take(123); ws()
        var fields: [String: Scalar] = [:]
        if !consume(125) {
            while true {
                let key = try string()
                guard fields[key] == nil, fields.count < 40, key.utf8.count <= 64 else { throw Failure.wire }
                ws(); try take(58); ws(); fields[key] = try scalar(); ws()
                if consume(125) { break }
                try take(44); ws()
            }
        }
        ws(); guard index == bytes.count else { throw Failure.wire }
        return fields
    }
    private mutating func scalar() throws -> Scalar {
        guard index < bytes.count else { throw Failure.wire }
        if bytes[index] == 34 { return .text(try string()) }
        if word("true") { return .boolean(true) }
        if word("false") { return .boolean(false) }
        if word("null") { return .none }
        let start = index
        guard (48...57).contains(bytes[index]) else { throw Failure.wire }
        if bytes[index] == 48 { index += 1 }
        else { while index < bytes.count, (48...57).contains(bytes[index]) { index += 1 } }
        guard let s = String(data: bytes.subdata(in: start..<index), encoding: .utf8),
              let n = Int64(s), n <= maxInteger else { throw Failure.wire }
        return .integer(n)
    }
    private mutating func string() throws -> String {
        let start = index
        try take(34)
        while index < bytes.count {
            let b = bytes[index]; index += 1
            guard b >= 32 else { throw Failure.wire }
            if b == 34 { return try JSONDecoder().decode(String.self, from: bytes.subdata(in: start..<index)) }
            if b == 92 {
                guard index < bytes.count else { throw Failure.wire }
                let escape = bytes[index]; index += 1
                if escape == 117 {
                    let high = try hex()
                    if (0xd800...0xdbff).contains(high) {
                        try take(92); try take(117)
                        guard (0xdc00...0xdfff).contains(try hex()) else { throw Failure.wire }
                    } else if (0xdc00...0xdfff).contains(high) { throw Failure.wire }
                } else if ![34, 92, 47, 98, 102, 110, 114, 116].contains(escape) { throw Failure.wire }
            }
        }
        throw Failure.wire
    }
    private mutating func hex() throws -> UInt32 {
        guard index + 4 <= bytes.count else { throw Failure.wire }
        var value: UInt32 = 0
        for _ in 0..<4 {
            let b = bytes[index]; index += 1
            let n: UInt32
            switch b { case 48...57: n = UInt32(b - 48); case 65...70: n = UInt32(b - 55)
            case 97...102: n = UInt32(b - 87); default: throw Failure.wire }
            value = value * 16 + n
        }
        return value
    }
    private mutating func word(_ text: String) -> Bool {
        let word = Array(text.utf8)
        guard index + word.count <= bytes.count, Array(bytes[index..<(index + word.count)]) == word else { return false }
        index += word.count; return true
    }
    private mutating func ws() { while index < bytes.count, [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
    private mutating func consume(_ b: UInt8) -> Bool {
        if index < bytes.count, bytes[index] == b { index += 1; return true }; return false
    }
    private mutating func take(_ b: UInt8) throws { guard consume(b) else { throw Failure.wire } }
}

private struct Fields {
    let raw: [String: Scalar]
    func s(_ key: String) -> String { raw[key]!.string! }
    func n(_ key: String) -> Int64 { raw[key]!.number! }
    func b(_ key: String) -> Bool { raw[key]!.bool! }
    func deadline(_ received: Instant) throws -> Deadline {
        try Deadline(absolute: n("authorityExpiresAtMs"), remaining: n("remainingLifetimeMs"), received: received)
    }
}

private enum Rule {
    case number(Int64, Int64), text(Int), choice(Set<String>), boolean, identifier, etag, digest
    indirect case nullable(Rule)
    func accepts(_ v: Scalar) -> Bool {
        switch self {
        case let .number(a, b): return v.number.map { $0 >= a && $0 <= b } ?? false
        case let .text(max): return v.string.map { !$0.isEmpty && $0.utf8.count <= max && !$0.unicodeScalars.contains { $0.value < 32 || $0.value == 127 } } ?? false
        case let .choice(set): return v.string.map { set.contains($0) } ?? false
        case .boolean: return v.bool != nil
        case .identifier: return v.string.map(uuid) ?? false
        case .etag: return v.string.map(strongETag) ?? false
        case .digest: return v.string.map { matches($0, "^[0-9a-f]{64}$") } ?? false
        case let .nullable(rule): return v.isNull || rule.accepts(v)
        }
    }
}

private enum Registry {
    static let positive = Rule.number(1, maxInteger), natural = Rule.number(0, maxInteger)
    static let source: [String: Rule] = ["permitId": .identifier, "generation": positive]
    static let intent: [String: Rule] = ["desiredIntentRevision": positive, "selectionRevision": positive,
        "sleepFenceRevision": natural, "timerFenceRevision": natural]
    static let lifetime: [String: Rule] = ["authorityExpiresAtMs": positive, "remainingLifetimeMs": .number(1, 10_000)]
    static let representation: [String: Rule] = ["etag": .etag, "totalBytes": .number(1, maxObject),
        "mediaType": .text(128), "formatSuffix": .choice(Set(Format.suffixes))]
    static func merge(_ maps: [String: Rule]...) -> [String: Rule] { maps.reduce([:]) { $0.merging($1) { _, b in b } } }
    static func validate(_ raw: [String: Scalar], hello: Bool) throws -> Fields {
        let f = Fields(raw: raw)
        let base: [String: Rule] = ["v": .number(1, 1), "type": .text(32)]
        let schema: [String: Rule]
        if hello {
            schema = merge(base, ["type": .choice(["hello"]), "protocol": .choice(["autplay.remote-media"]), "maxFrameBytes": .number(65_536, 65_536)])
        } else if raw["type"]?.string == "command" {
            guard let op = raw["op"]?.string else { throw Failure.wire }
            let c = merge(base, ["type": .choice(["command"]), "id": positive, "op": .text(32), "runId": .identifier])
            let extra: [String: Rule]
            switch op {
            case "setNetworkPolicy": extra = ["revision": positive, "allowMeteredDownloads": .boolean]
            case "installSource": extra = merge(source, lifetime, ["purpose": .choice(["INSPECT_PLAYBACK", "INSPECT_DOWNLOAD"]),
                "executionAttempt": positive, "copyId": .identifier, "variantId": .identifier, "streamOrigin": .text(4096), "streamUrl": .text(4096),
                "transportMode": .choice(["PLATFORM_PKI", "CONFIRMED_SPKI"]), "transportPolicyRevision": positive,
                "tlsSpkiSha256": .nullable(.digest), "accessToken": .text(8192), "credentialGeneration": natural,
                "sessionGeneration": natural, "accessExpiresAtMs": positive, "permitRevision": positive])
            case "installRetained": extra = merge(source, lifetime, representation, ["etag": .nullable(.etag), "copyId": .identifier,
                "managedPath": .text(4096), "fileIdentity": .text(41), "localLeaseRevision": positive, "permitRevision": positive])
            case "authorizeBytes": extra = merge(source, lifetime, representation, ["permitRevision": positive,
                "resourceType": .choice(["PLAY_INSTANCE", "DOWNLOAD_INTENT"]), "operationId": .identifier, "activationId": .identifier,
                "resourceGeneration": positive, "attachmentRevision": .nullable(natural)])
            case "renewPermit": extra = merge(source, lifetime, ["expectedPermitRevision": positive, "permitRevision": positive])
            case "beginAccessRenewal": extra = merge(source, ["renewalId": positive, "renewalReason": .choice(["PREEXPIRY", "REJECTED_401"]),
                "expectedPermitRevision": positive, "expectedCredentialGeneration": natural, "expectedSessionGeneration": natural,
                "renewalDeadlineAtMs": positive, "remainingRenewalMs": .number(1, 30_000)])
            case "replaceAccess": extra = merge(source, lifetime, ["renewalId": positive, "renewalReason": .choice(["PREEXPIRY", "REJECTED_401"]),
                "expectedPermitRevision": positive, "permitRevision": positive, "expectedCredentialGeneration": natural,
                "credentialGeneration": natural, "expectedSessionGeneration": natural, "sessionGeneration": natural,
                "accessToken": .text(8192), "accessExpiresAtMs": positive])
            case "updateIntent": extra = merge(intent, ["wantsPlay": .boolean])
            case "activateAudio": extra = merge(source, intent, ["audioLeaseId": .identifier, "positionMs": .number(0, maxPosition), "autoplay": .boolean])
            case "play", "pause": extra = merge(source, intent, ["audioLeaseId": .identifier])
            case "seek": extra = merge(source, intent, ["audioLeaseId": .identifier, "seekId": positive, "positionMs": .number(0, maxPosition)])
            case "startTransfer": extra = merge(source, ["transferId": .identifier, "attempt": positive, "intentRevision": positive,
                "attemptLeaseRevision": positive, "offset": .number(0, maxObject)])
            case "retireSource": extra = merge(source, ["reason": .choice(["SELECTION", "CANCEL", "AUTHORITY", "SLEEP", "ATTEMPT_BOUNDARY", "ERROR"])])
            case "close": extra = [:]
            default: throw Failure.wire
            }
            schema = merge(c, extra)
        } else if raw["type"]?.string == "dataConsumed" {
            schema = merge(base, source, ["type": .choice(["dataConsumed"]), "runId": .identifier, "transferId": .identifier,
                "attempt": positive, "sequence": natural, "nextOffset": .number(0, maxObject), "creditBytes": .number(0, Int64(maxData)),
                "disposition": .choice(["START", "WRITTEN", "DISCARDED"])])
        } else if raw["type"]?.string == "readDecision" {
            schema = merge(base, source, ["type": .choice(["readDecision"]), "runId": .identifier, "readId": positive,
                "permitRevision": positive, "credentialGeneration": .nullable(natural), "transportPolicyRevision": .nullable(positive),
                "decision": .choice(["ALLOW", "DENY"]), "authorityExpiresAtMs": .nullable(positive),
                "remainingLifetimeMs": .nullable(.number(1, 10_000)), "code": .nullable(.choice(Set(Failure.allCodes)))])
        } else { throw Failure.wire }
        guard Set(raw.keys) == Set(schema.keys), raw.allSatisfy({ schema[$0.key]!.accepts($0.value) }) else { throw Failure.wire }
        if raw["accessToken"] != nil {
            guard f.s("accessToken").utf8.allSatisfy({ $0 >= 33 && $0 <= 126 }), f.n("authorityExpiresAtMs") <= f.n("accessExpiresAtMs") else { throw Failure.wire }
        }
        if raw["formatSuffix"] != nil { _ = try Format(mime: f.s("mediaType"), suffix: f.s("formatSuffix")) }
        if raw["op"]?.string == "installSource" {
            guard (f.s("transportMode") == "PLATFORM_PKI") == raw["tlsSpkiSha256"]!.isNull else { throw Failure.wire }
        }
        if raw["op"]?.string == "activateAudio", f.b("autoplay") { throw Failure.wire }
        if raw["op"]?.string == "authorizeBytes" {
            guard (f.s("resourceType") == "DOWNLOAD_INTENT") == raw["attachmentRevision"]!.isNull else { throw Failure.wire }
        }
        if raw["type"]?.string == "readDecision" {
            let allow = f.s("decision") == "ALLOW"
            guard allow != raw["authorityExpiresAtMs"]!.isNull, allow != raw["remainingLifetimeMs"]!.isNull,
                  allow == raw["code"]!.isNull, raw["credentialGeneration"]!.isNull == raw["transportPolicyRevision"]!.isNull else { throw Failure.wire }
        }
        return f
    }
}

private extension Failure {
    static let allCodes: [String] = [auth, denied, source, offline, capability, waiting, resource, tls, origin, redirect,
        representation, changed, range, timeout, dropped, media, seek, interrupted, storage, checksum, publication,
        cancelled, stale, unavailable, reconciliation, renewal, intent, wire, capacity, drain].map { $0.rawValue }
}

private struct Format: Equatable {
    static let suffixes = ["mp3", "aac", "m4a", "flac", "opus", "wav"]
    let mime: String
    let suffix: String
    var uti: String {
        switch suffix { case "mp3": return AVFileType.mp3.rawValue; case "m4a": return AVFileType.m4a.rawValue
        case "wav": return AVFileType.wav.rawValue; case "aac": return "public.aac-audio"
        case "flac": return "org.xiph.flac"; default: return "org.xiph.ogg-audio" }
    }
    init(mime: String, suffix: String? = nil) throws {
        let chosen: String
        switch mime {
        case "audio/mpeg": chosen = "mp3"; case "audio/aac": chosen = "aac"
        case "audio/mp4", "audio/x-m4a": chosen = "m4a"
        case "audio/flac", "audio/x-flac": chosen = "flac"; case "audio/ogg": chosen = "opus"
        case "audio/wav", "audio/x-wav", "audio/vnd.wave": chosen = "wav"
        default: throw Failure.media
        }
        guard suffix == nil || suffix == chosen else { throw Failure.media }
        self.mime = mime; self.suffix = chosen
    }
    // These are actual track codec subtypes, not claims inferred from a file extension.
    func acceptsCodec(_ code: FourCharCode) -> Bool {
        let allowed: [String: Set<FourCharCode>] = ["mp3": [0x2e6d7033], "aac": [0x61616320],
            "m4a": [0x61616320], "flac": [0x664c6143], "opus": [0x6f707573], "wav": [0x6c70636d]]
        return allowed[suffix]?.contains(code) == true
    }
    func acceptsContainer(_ bytes: Data) -> Bool {
        func has(_ text: String, at: Int = 0) -> Bool {
            let marker = Data(text.utf8)
            return at >= 0 && at + marker.count <= bytes.count && bytes.subdata(in: at..<(at + marker.count)) == marker
        }
        switch suffix {
        case "mp3": return has("ID3") || (bytes.count >= 3 && bytes[0] == 255 && bytes[1] & 0xe0 == 0xe0 && bytes[1] & 6 == 2)
        case "aac": return bytes.count >= 2 && bytes[0] == 255 && bytes[1] & 0xf6 == 0xf0
        case "flac": return has("fLaC")
        case "wav": return has("RIFF") && has("WAVE", at: 8)
        case "opus":
            guard has("OggS"), bytes.count >= 28, bytes[4] == 0, bytes[5] & 2 != 0 else { return false }
            let segments = Int(bytes[26]), packet = 27 + segments
            return segments > 0 && packet + 8 <= bytes.count && bytes[27] >= 8 && has("OpusHead", at: packet)
        case "m4a":
            var offset = 0
            while offset + 8 <= bytes.count {
                let length = bytes[offset..<(offset + 4)].reduce(0) { ($0 << 8) | Int($1) }
                if has("ftyp", at: offset + 4) { return length >= 16 && offset + length <= bytes.count }
                guard length >= 8, length <= bytes.count - offset,
                      has("free", at: offset + 4) || has("skip", at: offset + 4) || has("wide", at: offset + 4) else { return false }
                offset += length
            }
            return false
        default: return false
        }
    }
}

private struct Representation: Equatable {
    let etag: String?
    let total: Int64
    let format: Format
    init(_ f: Fields) throws {
        etag = f.raw["etag"]!.string; total = f.n("totalBytes")
        format = try Format(mime: f.s("mediaType"), suffix: f.s("formatSuffix"))
    }
    init(etag: String, total: Int64, format: Format) { self.etag = etag; self.total = total; self.format = format }
    var fields: [String: Any] { ["etag": etag as Any? ?? null, "totalBytes": total, "mediaType": format.mime, "formatSuffix": format.suffix] }
}

private struct Intent: Equatable {
    let desired: Int64, selection: Int64, sleep: Int64, timer: Int64
    init(_ f: Fields) { desired = f.n("desiredIntentRevision"); selection = f.n("selectionRevision"); sleep = f.n("sleepFenceRevision"); timer = f.n("timerFenceRevision") }
    var fields: [String: Any] { ["desiredIntentRevision": desired, "selectionRevision": selection, "sleepFenceRevision": sleep, "timerFenceRevision": timer] }
    func follows(_ old: Intent) -> Bool { desired > old.desired && selection >= old.selection && sleep >= old.sleep && timer >= old.timer }
}

// Shared only for the final synchronous main-thread admission check. Mutable transport,
// loader descriptors and events remain owned by RemoteOwner.queue.
private final class LifecycleFence {
    private let lock = NSLock()
    private var revision: Int64 = 0
    func capture() -> Int64 { lock.lock(); defer { lock.unlock() }; return revision }
    func matches(_ value: Int64) -> Bool { lock.lock(); defer { lock.unlock() }; return revision == value && revision < maxInteger }
    func retireAll() { lock.lock(); if revision < maxInteger { revision += 1 }; lock.unlock() }
}
private final class Admission {
    private let lock = NSLock()
    private let lifecycle: LifecycleFence, epoch: Int64
    private var retired = false, waiting = false
    private var revision: Int64 = 0
    private var deadline: Deadline
    init(_ deadline: Deadline, lifecycle: LifecycleFence) {
        self.deadline = deadline; self.lifecycle = lifecycle; epoch = lifecycle.capture()
    }
    func block(retire: Bool) {
        lock.lock(); waiting = true; retired = retired || retire
        if revision < maxInteger { revision += 1 } else { retired = true }
        lock.unlock()
    }
    func publish(_ deadline: Deadline, reopen: Bool = false) {
        lock.lock(); self.deadline = deadline; if reopen && !retired { waiting = false }; lock.unlock()
    }
    func capture() -> Int64 { lock.lock(); defer { lock.unlock() }; return revision }
    func live(_ expected: Int64? = nil) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return (expected == nil || expected == revision) && !retired && !waiting && deadline.live() && lifecycle.matches(epoch)
    }
}

private final class IntentGate {
    private let lock = NSLock()
    private var intent: Intent?
    private var wants = false
    func publish(_ value: Intent, wants: Bool) throws {
        lock.lock(); defer { lock.unlock() }
        guard intent == nil || value.follows(intent!) else { throw Failure.intent }
        intent = value; self.wants = wants
    }
    func matches(_ value: Intent, play: Bool = false) -> Bool {
        lock.lock(); defer { lock.unlock() }; return intent == value && (!play || wants)
    }
    func stop() { lock.lock(); wants = false; lock.unlock() }
}

// Independent bounded FIFO pipe owners. No main/loader callback waits for pipe capacity.
private final class Pipe {
    private let input = DispatchQueue(label: "app.autplay.remote-media.stdin")
    private let output = DispatchQueue(label: "app.autplay.remote-media.stdout")
    private let lock = NSLock()
    private var inbound = 0, outbound = 0, secret = false, stopped = false, broken = false
    private let readyBarrier = DispatchSemaphore(value: 0)
    private var partialDeadline: Double?
    var onFrame: ((Fields, Instant, @escaping () -> Void) -> Void)?
    var onFailure: (() -> Void)?
    var onReaderEnd: (() -> Void)?
    init() {
        signal(SIGPIPE, SIG_IGN)
        for fd in [STDIN_FILENO, STDOUT_FILENO] {
            let flags = fcntl(fd, F_GETFL)
            guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) >= 0 else { exit(2) }
        }
    }
    func start() { input.async { self.readLoop() } }
    func send(_ fields: [String: Any], done: @escaping (Bool) -> Void = { _ in }) {
        guard var body = try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys]), body.count < 65_536 else { fail(); done(false); return }
        let control = ["hello", "ready", "ack", "sourceFailure"].contains(fields["type"] as? String ?? "")
        body.insert(1, at: 0); sendBody(body, control: control, done: done)
        wipe(&body)
    }
    private final class OutboundBuffer {
        var bytes: Data
        init(_ bytes: Data) { self.bytes = bytes }
        func erase() { wipe(&bytes) }
        deinit { erase() }
    }
    func sendBody(_ body: Data, control: Bool = false, done: @escaping (Bool) -> Void) {
        guard body.count >= 1, body.count <= 65_536 else { fail(); done(false); return }
        lock.lock()
        guard !broken, !stopped, outbound < (control ? 32 : 28) else { lock.unlock(); fail(); done(false); return }
        outbound += 1; lock.unlock()
        var size = UInt32(body.count).bigEndian
        var frame = withUnsafeBytes(of: &size) { Data($0) }; frame.append(body)
        let owned = OutboundBuffer(frame); frame = Data()
        output.async {
            self.lock.lock(); let broken = self.broken; self.lock.unlock()
            let success = !broken && self.write(owned.bytes)
            owned.erase()
            self.lock.lock(); self.outbound -= 1; self.lock.unlock()
            done(success); if !success { self.fail() }
        }
    }
    func stopReader() { lock.lock(); stopped = true; lock.unlock() }
    func confirmHandshake() { readyBarrier.signal() }
    func abort() { fail() }
    func finish(_ code: Int32) {
        // Called only after the reader and all source writes settle. The sentinel is FIFO.
        output.async { exit(code) }
    }
    private func fail() {
        lock.lock(); let first = !broken; broken = true; lock.unlock()
        if first { onFailure?() }
    }
    private func readLoop() {
        var handshake = false, window = Instant().mono, count = 0
        defer { onReaderEnd?() }
        do {
            while true {
                var header = try read(4, idle: true)
                if header.isEmpty { return }
                let size = header.reduce(0) { ($0 << 8) | Int($1) }; wipe(&header)
                guard size > 0, size <= 65_536 else { throw Failure.wire }
                var body = try read(size, idle: false)
                defer { wipe(&body) }
                if body.isEmpty { return } // The close sentinel stopped a partial read.
                partialDeadline = nil
                let received = Instant()
                if received.mono - window >= 1 { count = 0; window = received.mono }
                count += 1; guard count <= 256 else { throw Failure.capacity }
                var parser = try FlatJSON(body)
                let fields = try Registry.validate(parser.parse(), hello: !handshake)
                let first = !handshake; handshake = true
                let isFence = ["updateIntent", "pause", "retireSource", "close"].contains(fields.raw["op"]?.string ?? "")
                let isSecret = fields.raw["accessToken"] != nil
                lock.lock()
                guard inbound < (isFence ? 32 : 28), !secret || !isSecret else { lock.unlock(); throw Failure.capacity }
                inbound += 1; if isSecret { secret = true }; lock.unlock()
                onFrame?(fields, received, {
                    self.lock.lock(); self.inbound -= 1; if isSecret { self.secret = false }; self.lock.unlock()
                })
                // Only the dedicated reader waits; the main/loader owners never wait for IO.
                if first, readyBarrier.wait(timeout: .now() + 5) != .success { throw Failure.wire }
            }
        } catch { fail() }
    }
    private func read(_ size: Int, idle: Bool) throws -> Data {
        var result = Data(count: size), offset = 0
        var deadline: Double? = idle ? nil : partialDeadline
        while offset < size {
            lock.lock(); let stop = stopped; lock.unlock()
            if stop { wipe(&result); return Data() }
            if let deadline = deadline, Instant().mono >= deadline { wipe(&result); throw Failure.wire }
            let n = result.withUnsafeMutableBytes { Darwin.read(STDIN_FILENO, $0.baseAddress!.advanced(by: offset), size - offset) }
            if n > 0 {
                offset += n
                if deadline == nil { deadline = Instant().mono + 5; partialDeadline = deadline }
                continue
            }
            if n == 0 { wipe(&result); if idle && offset == 0 { return Data() }; throw Failure.wire }
            if errno == EINTR { continue }
            guard errno == EAGAIN || errno == EWOULDBLOCK else { wipe(&result); throw Failure.wire }
            var fd = pollfd(fd: STDIN_FILENO, events: Int16(POLLIN), revents: 0)
            let ready = Darwin.poll(&fd, 1, 100)
            if ready < 0 && errno != EINTR { wipe(&result); throw Failure.wire }
        }
        return result
    }
    private func write(_ frame: Data) -> Bool {
        let deadline = Instant().mono + 2
        var offset = 0
        while offset < frame.count {
            guard Instant().mono < deadline else { return false }
            let n = frame.withUnsafeBytes { Darwin.write(STDOUT_FILENO, $0.baseAddress!.advanced(by: offset), frame.count - offset) }
            if n > 0 { offset += n; continue }
            if n < 0 && errno == EINTR { continue }
            guard n < 0, errno == EAGAIN || errno == EWOULDBLOCK else { return false }
            var fd = pollfd(fd: STDOUT_FILENO, events: Int16(POLLOUT), revents: 0)
            let ready = Darwin.poll(&fd, 1, 100)
            if ready < 0 && errno != EINTR { return false }
            if fd.revents & Int16(POLLERR | POLLHUP | POLLNVAL) != 0 { return false }
        }
        return true
    }
}

private struct StreamLocation {
    let origin: String, host: String, port: Int, route: String, initial: URL
    init(_ f: Fields) throws {
        origin = f.s("streamOrigin")
        let base = try Self.components(origin)
        guard base.percentEncodedPath.isEmpty else { throw Failure.origin }
        host = Self.unbracket(base.host!); port = base.port ?? 443
        route = "/api/v1/stream/audio-variants/" + f.s("variantId")
        guard let initial = URL(string: f.s("streamUrl")) else { throw Failure.origin }
        self.initial = initial
        _ = try checked(initial.absoluteString)
    }
    func checked(_ value: String) throws -> URL {
        let c = try Self.components(value)
        guard c.scheme == "https", Self.unbracket(c.host!) == host, (c.port ?? 443) == port,
              c.percentEncodedPath == route || c.percentEncodedPath == route + "/", let url = c.url else { throw Failure.origin }
        return url
    }
    static func unbracket(_ text: String) -> String {
        text.hasPrefix("[") && text.hasSuffix("]") ? String(text.dropFirst().dropLast()) : text
    }
    private static func components(_ value: String) throws -> URLComponents {
        guard value.utf8.count <= 4096, !value.isEmpty,
              value.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 && $0 != 92 && $0 != 37 }),
              let c = URLComponents(string: value), c.scheme == "https", let rawHost = c.host,
              c.user == nil, c.password == nil, c.query == nil, c.fragment == nil,
              c.port == nil || (c.port! > 0 && c.port! <= 65535 && c.port! != 443) else { throw Failure.origin }
        let host = unbracket(rawHost)
        let authority: String
        if host.contains(":") {
            guard try canonicalIPv6(host) == host else { throw Failure.origin }
            authority = "[" + host + "]"
        } else {
            guard host == host.lowercased(), host.utf8.count <= 253,
                  matches(host, "^[a-z0-9]+([.-][a-z0-9]+)*$"),
                  host.split(separator: ".").allSatisfy({ $0.count <= 63 && !$0.hasPrefix("-") && !$0.hasSuffix("-") }) else { throw Failure.origin }
            if host.allSatisfy({ $0.isNumber || $0 == "." }) {
                let parts = host.split(separator: ".", omittingEmptySubsequences: false)
                guard parts.count == 4, parts.allSatisfy({ part in
                    guard let value = Int(part), value >= 0 && value <= 255 else { return false }
                    return String(value) == part
                }) else { throw Failure.origin }
            }
            authority = host
        }
        let canonical = "https://" + authority + (c.port.map { ":\($0)" } ?? "") + c.percentEncodedPath
        guard value == canonical else { throw Failure.origin }
        return c
    }
    private static func canonicalIPv6(_ text: String) throws -> String {
        // Same closed hexadecimal spelling as RemoteMediaUrls on the JVM. Do not let
        // inet_ntop silently substitute a dotted IPv4-mapped spelling on one platform.
        guard matches(text, "^[0-9a-f:]+$"), text.filter({ $0 == ":" }).count >= 2 else { throw Failure.origin }
        let halves = text.components(separatedBy: "::")
        guard halves.count <= 2 else { throw Failure.origin }
        func groups(_ part: String) throws -> [Int] {
            if part.isEmpty { return [] }
            return try part.split(separator: ":", omittingEmptySubsequences: false).map {
                guard !$0.isEmpty, $0.count <= 4, let value = Int($0, radix: 16) else { throw Failure.origin }; return value
            }
        }
        let left = try groups(halves[0])
        let right: [Int]
        if halves.count == 2 { right = try groups(halves[1]) } else { right = [] }
        let missing = 8 - left.count - right.count
        guard (halves.count == 1 && missing == 0) || (halves.count == 2 && missing >= 1) else { throw Failure.origin }
        let values = left + Array(repeating: 0, count: missing) + right
        var best = -1, length = 0, cursor = 0
        while cursor < 8 {
            if values[cursor] != 0 { cursor += 1; continue }
            let start = cursor
            while cursor < 8 && values[cursor] == 0 { cursor += 1 }
            if cursor - start >= 2 && cursor - start > length { best = start; length = cursor - start }
        }
        func joined(_ parts: ArraySlice<Int>) -> String { parts.map { String($0, radix: 16) }.joined(separator: ":") }
        if best < 0 { return joined(values[...]) }
        return joined(values[..<best]) + "::" + joined(values[(best + length)...])
    }
}

// Bounded DER TLV reader for RFC5280 Certificate/TBSCertificate/SPKI. It preserves the
// original AlgorithmIdentifier, parameters and BIT STRING; no SecKey export/re-encoding.
private struct CertificateDER {
    private struct Node { let tag: UInt8; let whole: Range<Int>; let body: Range<Int> }
    private let data: Data
    private var nodes = 0
    init(_ data: Data) throws { guard !data.isEmpty, data.count <= 65_536 else { throw Failure.tls }; self.data = data }
    private mutating func read(_ cursor: inout Int, end: Int) throws -> Node {
        nodes += 1
        guard nodes <= 4096, cursor + 2 <= end else { throw Failure.tls }
        let start = cursor, tag = data[cursor]; cursor += 1
        guard tag & 31 != 31, tag != 0 else { throw Failure.tls }
        let first = data[cursor]; cursor += 1
        var length = Int(first)
        if first & 128 != 0 {
            let count = Int(first & 127)
            guard count >= 1, count <= 3, cursor + count <= end, data[cursor] != 0 else { throw Failure.tls }
            length = 0
            for _ in 0..<count { length = length * 256 + Int(data[cursor]); cursor += 1 }
            guard length >= 128 else { throw Failure.tls }
        }
        guard length <= end - cursor else { throw Failure.tls }
        let result = Node(tag: tag, whole: start..<(cursor + length), body: cursor..<(cursor + length))
        cursor += length; return result
    }
    private mutating func children(_ node: Node, tags: [UInt8]? = nil) throws -> [Node] {
        var cursor = node.body.lowerBound, result: [Node] = []
        while cursor < node.body.upperBound { result.append(try read(&cursor, end: node.body.upperBound)) }
        if let tags = tags, result.map({ $0.tag }) != tags { throw Failure.tls }
        return result
    }
    private mutating func structural(_ node: Node, depth: Int) throws {
        guard depth <= 16 else { throw Failure.tls }
        if node.tag & 192 == 0 {
            if node.tag & 32 != 0 { guard node.tag == 48 || node.tag == 49 else { throw Failure.tls } }
            else if node.tag == 16 || node.tag == 17 { throw Failure.tls }
        }
        if node.tag & 32 != 0 {
            let children = try children(node)
            var previous: Data?
            for child in children {
                if node.tag == 49 {
                    let encoded = data.subdata(in: child.whole)
                    if let previous = previous, encoded.lexicographicallyPrecedes(previous) { throw Failure.tls }
                    previous = encoded
                }
                try structural(child, depth: depth + 1)
            }
        } else if node.tag == 1 {
            guard node.body.count == 1, data[node.body.lowerBound] == 0 || data[node.body.lowerBound] == 255 else { throw Failure.tls }
        } else if node.tag == 2 {
            guard !node.body.isEmpty else { throw Failure.tls }
            if node.body.count > 1 {
                let a = data[node.body.lowerBound], b = data[node.body.lowerBound + 1]
                guard !(a == 0 && b & 128 == 0), !(a == 255 && b & 128 != 0) else { throw Failure.tls }
            }
        } else if node.tag == 3 {
            try bitString(node)
        } else if node.tag == 5 {
            guard node.body.isEmpty else { throw Failure.tls }
        } else if node.tag == 6 {
            guard !node.body.isEmpty else { throw Failure.tls }
            var start = true
            for b in data[node.body] {
                guard !start || b != 128 else { throw Failure.tls }
                start = b & 128 == 0
            }
            guard start else { throw Failure.tls }
        }
    }
    private func bitString(_ node: Node) throws {
        // Also used for [1]/[2] IMPLICIT UniqueIdentifier: their tag hides the
        // universal BIT STRING, but the contents and DER padding rules are identical.
        guard !node.body.isEmpty else { throw Failure.tls }
        let unused = data[node.body.lowerBound]
        guard unused <= 7, node.body.count > 1 || unused == 0 else { throw Failure.tls }
        if unused > 0, data[node.body.upperBound - 1] & UInt8((1 << unused) - 1) != 0 { throw Failure.tls }
    }
    private mutating func extensions(_ node: Node) throws {
        let wrapper = try children(node, tags: [48])
        let entries = try children(wrapper[0])
        guard !entries.isEmpty else { throw Failure.tls }
        var identifiers: Set<Data> = []
        for entry in entries {
            guard entry.tag == 48 else { throw Failure.tls }
            let fields = try children(entry)
            guard fields.map({ $0.tag }) == [6, 4] || fields.map({ $0.tag }) == [6, 1, 4],
                  identifiers.insert(data.subdata(in: fields[0].body)).inserted else { throw Failure.tls }
            // critical DEFAULT FALSE must be absent in DER; an explicit value is TRUE.
            if fields.count == 3 { guard data[fields[1].body.lowerBound] == 255 else { throw Failure.tls } }
            let value = fields[fields.count - 1]
            var cursor = value.body.lowerBound
            let inner = try read(&cursor, end: value.body.upperBound)
            guard cursor == value.body.upperBound else { throw Failure.tls }
            try structural(inner, depth: 4)
        }
    }
    mutating func spkiAndValidity() throws -> (Data, Date, Date) {
        var cursor = 0
        let certificate = try read(&cursor, end: data.count)
        guard certificate.tag == 48, cursor == data.count else { throw Failure.tls }
        // Bound and check nested DER lengths/minimal scalar representations independently.
        try structural(certificate, depth: 0)
        nodes = 0
        let cert = try children(certificate, tags: [48, 48, 3])
        let tbs = try children(cert[0])
        let shift = tbs.first?.tag == 160 ? 1 : 0
        guard tbs.count >= shift + 6 else { throw Failure.tls }
        var version: UInt8 = 0
        if shift == 1 {
            let v = try children(tbs[0], tags: [2])
            // RFC5280 4.1 + X.690 11.5: v1 is DEFAULT and cannot be explicit in DER.
            guard v[0].body.count == 1, (1...2).contains(data[v[0].body.lowerBound]) else { throw Failure.tls }
            version = data[v[0].body.lowerBound]
        }
        guard tbs[shift].tag == 2, tbs[shift + 1].tag == 48, tbs[shift + 2].tag == 48,
              tbs[shift + 3].tag == 48, tbs[shift + 4].tag == 48, tbs[shift + 5].tag == 48 else { throw Failure.tls }
        var last: UInt8 = 128
        for tail in tbs.dropFirst(shift + 6) {
            guard [129, 130, 163].contains(tail.tag), tail.tag > last else { throw Failure.tls }; last = tail.tag
            if tail.tag == 163 {
                guard version == 2 else { throw Failure.tls }
                try extensions(tail)
            } else {
                guard version >= 1 else { throw Failure.tls }
                try bitString(tail)
            }
        }
        let validity = try children(tbs[shift + 3])
        guard validity.count == 2 else { throw Failure.tls }
        let before = try time(validity[0]), after = try time(validity[1])
        guard before < after else { throw Failure.tls }
        let spki = tbs[shift + 5]
        let content = try children(spki, tags: [48, 3])
        let algorithm = try children(content[0])
        guard (1...2).contains(algorithm.count), algorithm[0].tag == 6, !algorithm[0].body.isEmpty,
              content[1].body.count >= 2, data[content[1].body.lowerBound] == 0 else { throw Failure.tls }
        return (data.subdata(in: spki.whole), before, after)
    }
    private func time(_ node: Node) throws -> Date {
        guard node.tag == 23 || node.tag == 24, let text = String(data: data.subdata(in: node.body), encoding: .ascii),
              text.last == "Z", text.count == (node.tag == 23 ? 13 : 15), text.dropLast().allSatisfy({ $0 >= "0" && $0 <= "9" }) else { throw Failure.tls }
        let digits = Array(text.dropLast())
        var p = 0
        func number(_ count: Int) -> Int { let n = Int(String(digits[p..<(p + count)]))!; p += count; return n }
        var year = number(node.tag == 23 ? 2 : 4)
        if node.tag == 23 { year += year >= 50 ? 1900 : 2000 }
        let month = number(2), day = number(2), hour = number(2), minute = number(2), second = number(2)
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let parts = DateComponents(calendar: calendar, timeZone: calendar.timeZone, year: year, month: month, day: day, hour: hour, minute: minute, second: second)
        guard let date = calendar.date(from: parts) else { throw Failure.tls }
        let check = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        guard check.year == year, check.month == month, check.day == day, check.hour == hour, check.minute == minute, check.second == second else { throw Failure.tls }
        return date
    }
}

private enum TrustEvaluation {
    // Runs ONLY on the bounded trust executor. Every call remains counted until return,
    // even if URLSession task cancellation and its terminal callback win the race.
    static func evaluate(_ trust: SecTrust, host: String, pin: String?) -> Bool {
        // Set policies first: replacing them can discard their network-fetch options.
        // Disable auxiliary fetch before CopyCertificateChain can trigger evaluation.
        guard SecTrustSetPolicies(trust, SecPolicyCreateSSL(true, host as CFString)) == errSecSuccess,
              SecTrustSetNetworkFetchAllowed(trust, false) == errSecSuccess,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], !chain.isEmpty, chain.count <= 8 else { return false }
        for certificate in chain { if CFDataGetLength(SecCertificateCopyData(certificate)) > 65_536 { return false } }
        if let pin = pin {
            do {
                var reader = try CertificateDER(SecCertificateCopyData(chain[0]) as Data)
                let (spki, before, after) = try reader.spkiAndValidity()
                // Source only: this platform primitive is never executed by the authoring task.
                let actual = SHA256.hash(data: spki).map { String(format: "%02x", $0) }.joined()
                let now = Date()
                guard actual == pin, now >= before, now < after,
                      SecTrustSetAnchorCertificates(trust, [chain[0]] as CFArray) == errSecSuccess,
                      SecTrustSetAnchorCertificatesOnly(trust, true) == errSecSuccess else { return false }
                guard SecTrustEvaluateWithError(trust, nil) else { return false }
                let final = Date(); return final >= before && final < after
            } catch { return false }
        }
        // No custom anchors for PLATFORM_PKI; the session's platform trust remains authoritative.
        return SecTrustEvaluateWithError(trust, nil)
    }
}

private final class RetainedFile {
    private(set) var descriptor: Int32 = -1
    private var directory: Int32 = -1
    private var filename = ""
    let identity: String, length: Int64
    init(path: String, identity: String, length: Int64, suffix: String) throws {
        self.identity = identity; self.length = length
        guard matches(identity, "^(0|[1-9][0-9]{0,19}):(0|[1-9][0-9]{0,19})$"),
              path.utf8.count <= 4096, !path.contains("\\"), !path.contains("\0") else { throw Failure.source }
        let root = FileManager.default.homeDirectoryForCurrentUser.path + "/Library/Application Support/AutPlay/media/remote-media-v1/retained"
        guard path.hasPrefix(root + "/"), path.split(separator: "/", omittingEmptySubsequences: false).dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else { throw Failure.source }
        let name = String(path.dropFirst(root.count + 1))
        guard !name.contains("/"), name.hasSuffix("." + suffix), uuid(String(name.dropLast(suffix.count + 1))) else { throw Failure.source }
        var parent = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard parent >= 0 else { throw Failure.source }
        defer { Darwin.close(parent) }
        let components = root.split(separator: "/")
        for (index, part) in components.enumerated() {
            let child = openat(parent, String(part), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard child >= 0 else { throw Failure.source }
            var st = stat()
            guard fstat(child, &st) == 0, st.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR) else { Darwin.close(child); throw Failure.source }
            if index >= components.count - 4 && (st.st_uid != geteuid() || st.st_mode & 0o077 != 0) { Darwin.close(child); throw Failure.source }
            Darwin.close(parent); parent = child
        }
        let fd = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
        guard fd >= 0 else { throw Failure.source }
        descriptor = fd; directory = fcntl(parent, F_DUPFD_CLOEXEC, 0); filename = name
        do { try verify() } catch { close(); throw error }
    }
    func verify() throws {
        var st = stat(), linked = stat()
        guard descriptor >= 0, directory >= 0, fstat(descriptor, &st) == 0,
              fstatat(directory, filename, &linked, AT_SYMLINK_NOFOLLOW) == 0,
              linked.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), linked.st_dev == st.st_dev, linked.st_ino == st.st_ino,
              linked.st_size == st.st_size, linked.st_uid == st.st_uid, st.st_uid == geteuid(),
              st.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), st.st_mode & 0o077 == 0,
              st.st_size == length, "\(UInt64(UInt32(bitPattern: st.st_dev))):\(st.st_ino)" == identity else { throw Failure.source }
    }
    func read(start: Int64, end: Int64) throws -> Data {
        try verify()
        guard start >= 0, end >= start, end < length, end - start + 1 <= maxRange else { throw Failure.range }
        var data = Data(count: Int(end - start + 1)), count = 0
        let size = data.count
        do {
            while count < data.count {
                let n = data.withUnsafeMutableBytes { pread(descriptor, $0.baseAddress!.advanced(by: count), size - count, off_t(start + Int64(count))) }
                if n < 0 && errno == EINTR { continue }
                guard n > 0 else { throw Failure.source }; count += n
            }
            try verify(); return data
        } catch { wipe(&data); throw error }
    }
    func close() {
        if descriptor >= 0 { Darwin.close(descriptor); descriptor = -1 }
        if directory >= 0 { Darwin.close(directory); directory = -1 }
    }
    deinit { close() }
}

private struct SourceKey: Hashable {
    let run: String, permit: String, generation: Int64
    init(_ f: Fields) { run = f.s("runId"); permit = f.s("permitId"); generation = f.n("generation") }
    var fields: [String: Any] { ["runId": run, "permitId": permit, "generation": generation] }
}
private final class AttemptBudget {
    let attempt: Int64, variant: String, copy: String
    let started = Instant().mono
    var rejected = false, reconnect = false, failed = false
    init(_ f: Fields) { attempt = f.n("executionAttempt"); variant = f.s("variantId"); copy = f.s("copyId") }
}
private final class Renewal {
    let id: Int64, revision: Int64, credential: Int64, session: Int64
    var reason: String
    var deadline: Deadline
    var quiesced = false, audioPaused = false
    init(id: Int64, reason: String, source: Source, deadline: Deadline) {
        self.id = id; self.reason = reason; self.deadline = deadline
        revision = source.revision; credential = source.credential!; session = source.sessionGeneration!
    }
}
private final class AudioPreparation {
    let fields: Fields
    var started = Instant().mono
    init(_ fields: Fields) { self.fields = fields }
}
private final class Source {
    let key: SourceKey, purpose: String?, copy: String, attempt: AttemptBudget?
    let location: StreamLocation?, policy: Int64?, pin: String?
    let admission: Admission
    var revision: Int64, deadline: Deadline, accessExpiry: Int64?
    var credential: Int64?, sessionGeneration: Int64?, bearer = Data()
    var representation: Representation?, authorized = false, randomAccess = false
    var resourceType: String?, operation: String?, activation: String?, resourceGeneration: Int64?, attachment: Int64?
    var file: RetainedFile?, localLease: Int64?
    var urlSession: URLSession?, sessionDelegate: SessionDelegate?
    var invalidating = false, invalidated = true
    var httpReservations = 0, cancelledReadThrough: Int64 = 0
    var retry: HTTPWork?, audioLease: String?, pendingAudio: AudioPreparation?, preparingAudio = false
    var tasks: [Int: HTTPWork] = [:], reads: [Int64: PendingRead] = [:], loads: [ObjectIdentifier: Load] = [:]
    var cache: [CacheBlock] = [], cacheBytes = 0
    var eventSeq: Int64 = 0, readSeq: Int64 = 0, lastRenewal: Int64 = 0
    var renewal: Renewal?, inspecting = false, trustCount = 0, pipeCount = 0
    var retiring = false, mainDrained = true, mainCallbacks = 0, retireID: Int64?, failure: Failure?, drainStarted: Double?
    var transfer: Transfer?, started = Instant().mono
    init(_ f: Fields, received: Instant, lifecycle: LifecycleFence, budget: AttemptBudget? = nil) throws {
        key = SourceKey(f); copy = f.s("copyId"); attempt = budget
        if let budget = budget { started = budget.started }
        let admittedDeadline = try f.deadline(received), installedPurpose = f.raw["purpose"]?.string
        revision = f.n("permitRevision"); deadline = admittedDeadline; admission = Admission(admittedDeadline, lifecycle: lifecycle)
        purpose = installedPurpose
        if installedPurpose != nil {
            location = try StreamLocation(f); policy = f.n("transportPolicyRevision"); pin = f.raw["tlsSpkiSha256"]!.string
            credential = f.n("credentialGeneration"); sessionGeneration = f.n("sessionGeneration")
            accessExpiry = f.n("accessExpiresAtMs"); bearer = Data(f.s("accessToken").utf8)
        } else {
            location = nil; policy = nil; pin = nil
            representation = try Representation(f); authorized = true; randomAccess = true
            localLease = f.n("localLeaseRevision")
            file = try RetainedFile(path: f.s("managedPath"), identity: f.s("fileIdentity"), length: representation!.total, suffix: representation!.format.suffix)
        }
    }
    var download: Bool { purpose == "INSPECT_DOWNLOAD" }
    var active: Bool { !retiring && renewal == nil && admission.live() }
    var synthetic: URL? { representation.flatMap { URL(string: "autplay-remote-media://\(key.permit)/asset.\($0.format.suffix)") } }
    func erase() { wipe(&bearer); for index in cache.indices { wipe(&cache[index].bytes) }; cache.removeAll(); cacheBytes = 0 }
    deinit { erase(); file?.close() }
}
private final class PendingRead {
    let id: Int64, revision: Int64, credential: Int64?, policy: Int64?, kind: String, start: Int64?, end: Int64?
    let deadline: Deadline, authorityDeadline: Deadline
    let consumer: ObjectIdentifier?
    let completion: (Deadline?) -> Void
    init(id: Int64, source: Source, kind: String, start: Int64?, end: Int64?, consumer: ObjectIdentifier?, completion: @escaping (Deadline?) -> Void) {
        self.id = id; revision = source.revision; credential = source.credential; policy = source.policy
        self.kind = kind; self.start = start; self.end = end; deadline = source.deadline.capped(seconds: 5)
        authorityDeadline = source.deadline
        self.consumer = consumer
        self.completion = completion
    }
}
private final class Load {
    let request: AVAssetResourceLoadingRequest, end: Int64
    var waiting = false, finished = false, cancelled = false
    init(_ request: AVAssetResourceLoadingRequest, total: Int64) throws {
        self.request = request
        if let data = request.dataRequest {
            guard data.requestedOffset >= 0, data.currentOffset >= 0, data.requestedLength >= 0,
                  data.requestedOffset <= maxObject, data.currentOffset <= maxObject,
                  Int64(data.requestedLength) <= maxInteger - data.requestedOffset else { throw Failure.range }
            end = data.requestsAllDataToEndOfResource ? total : min(total, data.requestedOffset + Int64(data.requestedLength))
        } else { end = 0 }
    }
}
private struct CacheBlock { let start: Int64; var bytes: Data }
private final class Transfer {
    let id: String, attempt: Int64, intent: Int64, lease: Int64
    var nextOffset: Int64, sequence: Int64 = 0, credit = 0
    var started = false, waiting = false, ended = false, outstanding: (sequence: Int64, count: Int)?
    var creditRenewal: Int64?, startEpoch: Int64?
    var buffer = Data(), cursor = 0
    var grant: Deadline?
    init(_ f: Fields) { id = f.s("transferId"); attempt = f.n("attempt"); intent = f.n("intentRevision"); lease = f.n("attemptLeaseRevision"); nextOffset = f.n("offset") }
}
private final class HTTPWork {
    let source: Source, method: String, start: Int64?, end: Int64?, deadline: Double
    let completion: (Result<(Data, Deadline), Failure>) -> Void
    let consumer: ObjectIdentifier?
    var grant: Deadline, task: URLSessionDataTask?, body = Data(), response: HTTPURLResponse?
    var headRepresentation: Representation?
    var hops = 0, visited: Set<String>, failure: Failure?, terminal = false, trust = 0, challenged = false
    var redirected: ((URLRequest?) -> Void)?
    init(source: Source, method: String, start: Int64?, end: Int64?, grant: Deadline, consumer: ObjectIdentifier?,
         completion: @escaping (Result<(Data, Deadline), Failure>) -> Void) {
        self.source = source; self.method = method; self.start = start; self.end = end; self.grant = grant
        self.consumer = consumer
        deadline = Instant().mono + 15; visited = [source.location!.initial.absoluteString]; self.completion = completion
    }
}

private final class OwnerSlots {
    private let lock = NSLock()
    private var count = 0
    func take() -> Bool { lock.lock(); defer { lock.unlock() }; guard count < 32 else { return false }; count += 1; return true }
    func release() { lock.lock(); count -= 1; lock.unlock() }
}

private final class RemoteOwner {
    let queue = DispatchQueue(label: "app.autplay.remote-media.loader")
    let slots = OwnerSlots(), intent = IntentGate()
    let lifecycle = LifecycleFence()
    private let pipe: Pipe
    private let delegateQueue = OperationQueue()
    private let trustQueue = OperationQueue()
    private var trustOutstanding = 0
    private var sources: [SourceKey: Source] = [:], budgets: [String: AttemptBudget] = [:]
    private var lastGeneration: [String: Int64] = [:]
    private var handshaken = false, readyWritten = false, run: String?, lastCommand: Int64 = 0
    private var ordinary: Int64?, intentCommand: Int64?
    private var policyRevision: Int64 = 0, allowMetered = false
    private var monitor: NWPathMonitor?, networkRevision: Int64 = 0
    private var pathStatus = "UNKNOWN", expensive: Bool?, constrained: Bool?
    private var closing = false, failed = false, closeID: Int64?, closeWritten = false, readerEnded = false, processFinishing = false
    private var timer: DispatchSourceTimer?, lastClock = Instant(), handshakeAt = Instant().mono
    private var pendingReleases: [Int64: () -> Void] = [:]
    private lazy var audio = AudioOwner(owner: self)
    init(pipe: Pipe) {
        self.pipe = pipe
        delegateQueue.maxConcurrentOperationCount = 1; delegateQueue.underlyingQueue = queue
        delegateQueue.name = "app.autplay.remote-media.delegates"
        trustQueue.maxConcurrentOperationCount = 1; trustQueue.name = "app.autplay.remote-media.trust"
    }
    func start() {
        // Initial AppKit registration and every AVPlayer access occur on the main RunLoop.
        audio.startLifecycle()
        pipe.onFrame = { [weak self] f, received, release in
            guard let self = self else { release(); return }
            self.queue.async { self.receive(f, received: received, release: release) }
        }
        pipe.onFailure = { [weak self] in
            guard let self = self else { return }
            self.lifecycle.retireAll(); self.intent.stop()
            DispatchQueue.main.async { self.audio.stopImmediately() }
            self.queue.async { self.fatal() }
        }
        pipe.onReaderEnd = { [weak self] in self?.queue.async {
            guard let self = self else { return }; self.readerEnded = true
            if !self.closing { self.fatal() }; self.finishClose()
        } }
        queue.async {
            self.pipe.send(["v": 1, "type": "hello", "protocol": "autplay.remote-media", "maxFrameBytes": 65_536])
            let timer = DispatchSource.makeTimerSource(queue: self.queue)
            timer.schedule(deadline: .now() + 0.1, repeating: 0.1)
            timer.setEventHandler { [weak self] in self?.tick() }; self.timer = timer; timer.resume()
            self.pipe.start()
        }
    }
    private func receive(_ f: Fields, received: Instant, release: @escaping () -> Void) {
        var retained = false
        defer { if !retained { release() } }
        do {
            if !handshaken {
                guard !closing, f.s("type") == "hello", Instant().mono - handshakeAt < 5 else { throw Failure.wire }
                handshaken = true
                pipe.send(["v": 1, "type": "ready"]) { success in self.queue.async {
                    self.readyWritten = success; self.pipe.confirmHandshake(); if !success { self.fatal() }
                } }
                return
            }
            guard readyWritten, f.raw["runId"]?.string != nil else { throw Failure.wire }
            if run == nil {
                guard f.s("type") == "command", f.s("op") == "setNetworkPolicy", !closing else { throw Failure.wire }
                run = f.s("runId")
            }
            guard f.s("runId") == run else { throw Failure.wire }
            if f.s("type") == "dataConsumed" { try receipt(f); return }
            if f.s("type") == "readDecision" { try readDecision(f, received: received); return }
            guard f.s("type") == "command", !failed else { throw Failure.wire }
            let id = f.n("id"), op = f.s("op")
            guard !closing || (op == "updateIntent" && !f.b("wantsPlay")) else { throw Failure.wire }
            guard id > lastCommand else { throw Failure.wire }; lastCommand = id
            if op == "updateIntent" {
                guard intentCommand == nil else { throw Failure.wire }; intentCommand = id
            } else { guard ordinary == nil else { throw Failure.wire }; ordinary = id }
            // The input capacity/secret slot stays owned until this command's actual ACK.
            pendingReleases[id] = release; retained = true
            switch op {
            case "setNetworkPolicy":
                guard f.n("revision") > policyRevision else { throw Failure.stale }
                policyRevision = f.n("revision"); allowMetered = f.b("allowMeteredDownloads")
                ack(id, "APPLIED")
                if monitor == nil { startMonitor() }
                enforceNetwork()
            case "updateIntent":
                let fence = Intent(f); try intent.publish(fence, wants: f.b("wantsPlay"))
                DispatchQueue.main.async { self.audio.applyIntent(fence); self.queue.async { self.ack(id, "APPLIED") } }
            case "installSource", "installRetained":
                guard sources.count < 2 else { throw Failure.capacity }
                let lane = op == "installRetained" ? "INSPECT_PLAYBACK" : f.s("purpose")
                guard !sources.values.contains(where: { ($0.purpose ?? "INSPECT_PLAYBACK") == lane }),
                      f.n("generation") > (lastGeneration[lane] ?? 0) else { throw Failure.stale }
                var budget: AttemptBudget?
                if op == "installSource" {
                    if let old = budgets[lane] {
                        guard f.n("executionAttempt") >= old.attempt else { throw Failure.stale }
                        if f.n("executionAttempt") == old.attempt {
                            guard !old.failed, old.copy == f.s("copyId"), old.variant == f.s("variantId") else { throw Failure.auth }
                            budget = old
                        }
                    }
                    if budget == nil { budget = AttemptBudget(f) }
                }
                let source = try Source(f, received: received, lifecycle: lifecycle, budget: budget)
                guard sources[source.key] == nil else { throw Failure.stale }
                lastGeneration[lane] = source.key.generation; sources[source.key] = source
                if let budget = budget { budgets[lane] = budget }
                if source.purpose != nil {
                    ack(id, "ADMITTED"); createSession(source); inspect(source)
                } else { ack(id, "APPLIED") }
            case "close":
                closing = true; closeID = id; intent.stop()
                monitor?.pathUpdateHandler = nil; monitor?.cancel(); monitor = nil
                for source in Array(sources.values) { retire(source, code: nil) }
                DispatchQueue.main.async { self.audio.stopLifecycle() }
                settleAll()
            default:
                guard let source = sources[SourceKey(f)] else { throw Failure.source }
                if op == "retireSource" {
                    guard source.retireID == nil else { throw Failure.stale }
                    source.retireID = id; retire(source, code: nil); settle(source); return
                }
                guard !source.retiring else { throw Failure.stale }
                switch op {
                case "authorizeBytes":
                    guard source.active, source.purpose != nil, !source.authorized,
                          f.n("permitRevision") > source.revision, try Representation(f) == source.representation,
                          (f.s("resourceType") == "DOWNLOAD_INTENT") == source.download,
                          f.n("authorityExpiresAtMs") <= source.accessExpiry! else { throw Failure.resource }
                    let deadline = try f.deadline(received)
                    source.resourceType = f.s("resourceType"); source.operation = f.s("operationId"); source.activation = f.s("activationId")
                    source.resourceGeneration = f.n("resourceGeneration"); source.attachment = f.raw["attachmentRevision"]!.number
                    source.authorized = true; source.revision = f.n("permitRevision"); source.deadline = deadline; source.admission.publish(deadline)
                    ack(id, "APPLIED")
                case "renewPermit":
                    guard source.active, source.renewal == nil, f.n("expectedPermitRevision") == source.revision,
                          f.n("permitRevision") > source.revision,
                          source.accessExpiry == nil || f.n("authorityExpiresAtMs") <= source.accessExpiry! else { throw Failure.stale }
                    let deadline = try f.deadline(received)
                    source.deadline = deadline; source.revision = f.n("permitRevision"); source.admission.publish(deadline)
                    ack(id, "APPLIED")
                case "beginAccessRenewal":
                    try beginRenewal(source, f: f, received: received); ack(id, "ADMITTED"); settle(source)
                case "replaceAccess":
                    try replaceAccess(source, f: f, received: received, id: id)
                case "activateAudio", "play", "pause", "seek":
                    if op != "pause" { guard source.active, source.authorized, intent.matches(Intent(f), play: op == "play") else { throw Failure.intent } }
                    guard !source.download else { throw Failure.intent }
                    if op == "activateAudio" {
                        guard sources.values.allSatisfy({ $0.mainDrained }), source.representation != nil else { throw Failure.capacity }
                        source.mainDrained = false; source.audioLease = f.s("audioLeaseId"); source.pendingAudio = AudioPreparation(f)
                    } else {
                        guard source.audioLease == f.s("audioLeaseId") else { throw Failure.intent }
                    }
                    ack(id, "ADMITTED")
                    onAudio(source) { self.audio.command(f, source: source) }
                    if op == "activateAudio" { prepareAudio(source) }
                case "startTransfer":
                    guard source.active, source.authorized, source.download, source.transfer == nil,
                          sources.values.allSatisfy({ $0.transfer == nil }), f.n("attempt") == source.attempt!.attempt,
                          f.n("offset") < source.representation!.total else { throw Failure.stale }
                    source.transfer = Transfer(f); ack(id, "ADMITTED")
                default: throw Failure.wire
                }
            }
        } catch {
            let code = error as? Failure ?? .wire
            if let id = f.raw["id"]?.number, pendingReleases[id] != nil { ack(id, "REJECTED", code) }
            if f.s("type") != "command" || code == .wire || code == .capacity { fatal() }
        }
    }
    private func ack(_ id: Int64, _ outcome: String, _ code: Failure? = nil) {
        pipe.send(["v": 1, "type": "ack", "id": id, "outcome": outcome, "code": code?.rawValue as Any? ?? null])
        if ordinary == id { ordinary = nil }; if intentCommand == id { intentCommand = nil }
        pendingReleases.removeValue(forKey: id)?()
    }
    func emit(_ source: Source, _ type: String, _ fields: [String: Any]) {
        guard sources[source.key] === source else { return }
        do {
            source.eventSeq = try next(source.eventSeq)
            let message = source.key.fields.merging(["v": 1, "type": type, "eventSeq": source.eventSeq]) { _, b in b }.merging(fields) { _, b in b }
            source.pipeCount += 1
            pipe.send(message) { success in self.queue.async {
                source.pipeCount -= 1; if !success { self.fatal() }; self.settle(source)
            } }
        } catch { fatal() }
    }
    func audioEvent(_ source: Source, type: String, fields: [String: Any]) {
        guard slots.take() else { lifecycle.retireAll(); intent.stop(); pipe.abort(); return }
        queue.async { self.emit(source, type, fields); self.slots.release() }
    }
    private func onAudio(_ source: Source, _ action: @escaping () -> Void) {
        guard slots.take() else { fatal(); return }
        source.mainCallbacks += 1
        DispatchQueue.main.async {
            action()
            self.queue.async { source.mainCallbacks -= 1; self.slots.release(); self.settle(source) }
        }
    }
    func audioFailed(_ source: Source, _ code: Failure) { queue.async { self.fail(source, code) } }
    func audioDrained(_ source: Source) { queue.async { source.mainDrained = true; self.settle(source) } }
    private func fail(_ source: Source, _ code: Failure) {
        guard !source.retiring else { return }
        source.admission.block(retire: true)
        source.failure = code
        if code == .auth || code == .renewal { source.attempt?.failed = true }
        emit(source, "sourceFailure", ["code": code.rawValue]); retire(source, code: code)
    }
    private func fatal() {
        guard !failed else { return }
        failed = true; closing = true; intent.stop(); lifecycle.retireAll()
        for source in sources.values { source.admission.block(retire: true) }
        monitor?.pathUpdateHandler = nil; monitor?.cancel(); monitor = nil
        for source in Array(sources.values) { retire(source, code: .wire) }
        pipe.stopReader()
        DispatchQueue.main.async { self.audio.stopLifecycle() }
        // An exceptional exit is never a DRAINED witness. The host retains its owners.
        queue.asyncAfter(deadline: .now() + 2) { self.pipe.finish(2) }
    }
    private func live(_ source: Source, grant: Deadline? = nil) -> Bool {
        source.active && (grant?.live() ?? true)
    }
    private func readCount() -> Int { sources.values.reduce(0) { $0 + $1.reads.count } }
    private func admit(_ source: Source, kind: String, start: Int64? = nil, end: Int64? = nil, consumer: ObjectIdentifier? = nil, completion: @escaping (Deadline?) -> Void) {
        guard source.active else { completion(nil); return }
        if kind != "HEAD" {
            guard source.authorized, let start = start, let end = end, let rep = source.representation,
                  start >= 0, end >= start, end < rep.total, end - start + 1 <= maxRange else { fail(source, .range); completion(nil); return }
        } else if source.authorized || source.purpose == nil { fail(source, .wire); completion(nil); return }
        guard readCount() < 8, slots.take() else { fail(source, .capacity); completion(nil); return }
        do {
            source.readSeq = try next(source.readSeq)
            let pending = PendingRead(id: source.readSeq, source: source, kind: kind, start: start, end: end, consumer: consumer, completion: completion)
            source.reads[pending.id] = pending
            emit(source, "readAdmission", ["readId": pending.id, "permitRevision": pending.revision,
                "credentialGeneration": pending.credential as Any? ?? null, "transportPolicyRevision": pending.policy as Any? ?? null,
                "kind": kind, "start": start as Any? ?? null, "end": end as Any? ?? null])
        } catch { slots.release(); fatal() }
    }
    private func readDecision(_ f: Fields, received: Instant) throws {
        guard let source = sources[SourceKey(f)] else { throw Failure.wire }
        guard let pending = source.reads.removeValue(forKey: f.n("readId")) else {
            if f.n("readId") <= source.cancelledReadThrough { return }
            throw Failure.wire
        }
        slots.release()
        guard f.n("permitRevision") == pending.revision, f.raw["credentialGeneration"]!.number == pending.credential,
              f.raw["transportPolicyRevision"]!.number == pending.policy else { pending.completion(nil); throw Failure.wire }
        guard source.active, pending.deadline.live(), f.s("decision") == "ALLOW" else {
            if source.active { fail(source, f.raw["code"]!.string.flatMap(Failure.init(rawValue:)) ?? .stale) }
            pending.completion(nil)
            return
        }
        let granted = try f.deadline(received)
        guard source.deadline.contains(granted), pending.authorityDeadline.contains(granted), pending.deadline.live() else { pending.completion(nil); throw Failure.wire }
        pending.completion(granted)
    }
    private func cancelReads(_ source: Source) {
        source.cancelledReadThrough = source.readSeq
        let reads = Array(source.reads.values); source.reads.removeAll()
        for read in reads { slots.release(); read.completion(nil) }
    }
    private func startMonitor() {
        let monitor = NWPathMonitor(); self.monitor = monitor
        monitor.pathUpdateHandler = { [weak self] path in
            guard let self = self, !self.closing else { return }
            self.pathStatus = path.status == .satisfied ? "SATISFIED" : (path.status == .unsatisfied ? "UNSATISFIED" : "UNKNOWN")
            self.expensive = path.status == .satisfied ? path.isExpensive : nil
            self.constrained = path.status == .satisfied ? path.isConstrained : nil
            do { self.networkRevision = try next(self.networkRevision) } catch { self.fatal(); return }
            self.pipe.send(["v": 1, "type": "network", "runId": self.run!, "revision": self.networkRevision,
                "status": self.pathStatus, "expensive": self.expensive as Any? ?? null, "constrained": self.constrained as Any? ?? null])
            self.enforceNetwork()
        }
        monitor.start(queue: queue)
    }
    private func networkAllows(_ source: Source) -> Bool {
        if !source.download { return pathStatus != "UNSATISFIED" }
        return pathStatus == "SATISFIED" && constrained == false && (allowMetered || expensive == false)
    }
    private func enforceNetwork() {
        for source in Array(sources.values) where source.purpose != nil && !source.retiring {
            if !networkAllows(source) {
                // Cost waiting never spends auth/reconnect budget or invents a new attempt.
                for work in source.tasks.values { work.failure = pathStatus == "UNSATISFIED" ? .offline : .waiting; work.task?.cancel() }
                if let transfer = source.transfer { wipe(&transfer.buffer); transfer.cursor = 0; transfer.grant = nil }
            } else { if source.representation == nil { inspect(source) }; pump(source) }
        }
    }
    private func createSession(_ source: Source) {
        guard source.purpose != nil, source.urlSession == nil, source.invalidated, source.tasks.isEmpty,
              source.trustCount == 0, !source.retiring, source.renewal == nil else { return }
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.httpShouldSetCookies = false; config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 5; config.timeoutIntervalForResource = 15
        config.httpMaximumConnectionsPerHost = source.download ? 1 : 2
        config.waitsForConnectivity = false; config.allowsExpensiveNetworkAccess = !source.download || allowMetered
        config.allowsConstrainedNetworkAccess = !source.download
        let delegate = SessionDelegate(owner: self, source: source)
        source.sessionDelegate = delegate; source.invalidated = false; source.invalidating = false
        source.urlSession = URLSession(configuration: config, delegate: delegate, delegateQueue: delegateQueue)
    }
    private func invalidate(_ source: Source) {
        guard !source.invalidating else { return }
        source.invalidating = true
        if let session = source.urlSession { session.invalidateAndCancel() }
        else { source.invalidated = true }
    }
    func invalidated(_ source: Source, session: URLSession) {
        guard source.urlSession === session, source.invalidating else { fatal(); return }
        source.invalidated = true; source.urlSession = nil; source.sessionDelegate = nil
        settle(source)
    }
    private func request(_ source: Source, url: URL, method: String, start: Int64?, end: Int64?) throws -> URLRequest {
        guard source.active, networkAllows(source), let location = source.location, source.bearer.count > 0,
              source.accessExpiry! > Int64(Instant().wall + 1000), try location.checked(url.absoluteString) == url else { throw Failure.stale }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
        request.httpMethod = method
        request.setValue("Bearer " + String(decoding: source.bearer, as: UTF8.self), forHTTPHeaderField: "Authorization")
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.httpShouldHandleCookies = false
        request.allowsExpensiveNetworkAccess = !source.download || allowMetered
        request.allowsConstrainedNetworkAccess = !source.download
        if method == "GET" {
            guard source.authorized, let start = start, let end = end, start >= 0, end >= start,
                  end < source.representation!.total, end - start + 1 <= maxRange else { throw Failure.range }
            request.setValue("bytes=\(start)-\(end)", forHTTPHeaderField: "Range")
            request.setValue(source.representation!.etag!, forHTTPHeaderField: "If-Range")
            request.setValue(source.resourceType!, forHTTPHeaderField: "AutPlay-Resource-Type")
            request.setValue(source.operation!, forHTTPHeaderField: "AutPlay-Operation-ID")
            request.setValue(source.activation!, forHTTPHeaderField: "AutPlay-Activation-ID")
            request.setValue(String(source.resourceGeneration!), forHTTPHeaderField: "AutPlay-Generation")
        }
        return request
    }
    private func http(_ source: Source, method: String, start: Int64? = nil, end: Int64? = nil, consumer: ObjectIdentifier? = nil,
                      completion: @escaping (Result<(Data, Deadline), Failure>) -> Void) {
        guard !source.invalidating, source.tasks.count + source.httpReservations < (source.download ? 1 : 2) else { completion(.failure(.waiting)); return }
        source.httpReservations += 1
        admit(source, kind: method == "HEAD" ? "HEAD" : "RANGE", start: start, end: end, consumer: consumer) { grant in
            source.httpReservations -= 1
            guard let grant = grant, self.live(source, grant: grant), self.networkAllows(source) else {
                completion(.failure(source.active && (source.retry != nil || !self.networkAllows(source)) ? .waiting : .stale)); return
            }
            self.createSession(source)
            guard let session = source.urlSession, !source.invalidating,
                  source.tasks.count < (source.download ? 1 : 2) else { completion(.failure(.waiting)); return }
            guard self.slots.take() else { self.fail(source, .capacity); completion(.failure(.capacity)); return }
            do {
                let request = try self.request(source, url: source.location!.initial, method: method, start: start, end: end)
                let work = HTTPWork(source: source, method: method, start: start, end: end, grant: grant, consumer: consumer, completion: completion)
                let task = session.dataTask(with: request); work.task = task; source.tasks[task.taskIdentifier] = work; task.resume()
            } catch { self.slots.release(); completion(.failure(error as? Failure ?? .wire)) }
        }
    }
    private func inspect(_ source: Source) {
        guard source.active, source.representation == nil, !source.inspecting, networkAllows(source), !source.invalidating else { return }
        source.inspecting = true
        http(source, method: "HEAD") { result in
            source.inspecting = false
            guard source.active else { return }
            if case let .failure(code) = result, code != .waiting, code != .offline { self.fail(source, code) }
        }
    }
    private func selectedHeaders(_ response: HTTPURLResponse) throws -> [String: String] {
        // Accepted M1-H1 checks Foundation-observable values, not original wire multiplicity.
        // Hidden duplicate fields may survive normalization; they cannot be reconstructed here.
        // Representation, fixed-length framing and exact-origin checks still precede body use.
        var values: [String: String] = [:], bytes = 0
        for (key, value) in response.allHeaderFields {
            guard let name = key as? String, let text = value as? String else { throw Failure.representation }
            let lower = name.lowercased()
            guard values[lower] == nil, name.utf8.count <= 256, text.utf8.count <= 4096,
                  !text.unicodeScalars.contains(where: { $0.value < 32 && $0.value != 9 || $0.value == 127 }) else { throw Failure.representation }
            bytes += name.utf8.count + text.utf8.count; guard bytes <= 16_384 else { throw Failure.representation }
            values[lower] = text
        }
        return values
    }
    private func decimal(_ value: String?) throws -> Int64 {
        guard let value = value, matches(value, "^(0|[1-9][0-9]*)$"), let n = Int64(value), n <= maxObject else { throw Failure.representation }; return n
    }
    func response(_ source: Source, task: URLSessionDataTask, response: URLResponse, completion: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let work = source.tasks[task.taskIdentifier], !work.terminal,
              let response = response as? HTTPURLResponse, work.response == nil else { completion(.cancel); return }
        do {
            let headers = try selectedHeaders(response)
            // Fixed-length HEAD/206 metadata cannot coexist with observable transfer coding.
            // Foundation still owns the original wire fields; this proves no raw multiplicity.
            guard headers["transfer-encoding"] == nil else { throw Failure.representation }
            if response.statusCode == 401 {
                work.failure = .auth
                if !source.retiring && (source.active || source.renewal?.deadline.live() == true) { rejected401(source) }
                completion(.cancel); return
            }
            guard live(source, grant: work.grant) else { completion(.cancel); return }
            if response.statusCode == 403 || response.statusCode == 404 { throw Failure.denied }
            if response.statusCode == 409 || response.statusCode == 429 || response.statusCode == 503 {
                // The existing enforcing stream maps stale/busy to 409, capacity to 429
                // and service/config unavailability to 503. Only the host may re-enter
                // admission after this source drains; never replay GET on this activation.
                let code: Failure = response.statusCode == 409 ? .resource : (response.statusCode == 429 ? .waiting : .capability)
                work.failure = code; completion(.cancel); fail(source, code); return
            }
            guard headers["content-encoding"] == nil || headers["content-encoding"] == "identity",
                  let etag = headers["etag"], strongETag(etag), let mime = headers["content-type"], !mime.contains(";") else { throw Failure.representation }
            let format = try Format(mime: mime)
            let length = try decimal(headers["content-length"])
            if work.method == "HEAD" {
                guard response.statusCode == 200, length > 0, headers["accept-ranges"] == "bytes",
                      headers["content-range"] == nil else { throw Failure.representation }
                work.headRepresentation = Representation(etag: etag, total: length, format: format)
            } else {
                let rep = source.representation!
                guard response.statusCode != 200 else { throw Failure.changed }
                guard response.statusCode == 206, etag == rep.etag, mime == rep.format.mime,
                      length == work.end! - work.start! + 1,
                      headers["content-range"] == "bytes \(work.start!)-\(work.end!)/\(rep.total)" else {
                    throw response.statusCode == 416 ? Failure.range : Failure.changed
                }
            }
            work.response = response; completion(.allow)
        } catch { work.failure = error as? Failure ?? .representation; completion(.cancel); task.cancel() }
    }
    func data(_ source: Source, task: URLSessionDataTask, bytes: Data) {
        guard let work = source.tasks[task.taskIdentifier], !work.terminal, work.failure == nil,
              live(source, grant: work.grant), networkAllows(source) else { task.cancel(); return }
        let expected = work.method == "HEAD" ? 0 : Int(work.end! - work.start! + 1)
        guard work.response != nil, bytes.count <= expected - work.body.count, work.body.count + bytes.count <= Int(maxRange) else {
            work.failure = .representation; task.cancel(); return
        }
        work.body.append(bytes)
    }
    func completed(_ source: Source, task: URLSessionTask, error: Error?) {
        guard let work = source.tasks[task.taskIdentifier], !work.terminal else { fatal(); return }
        work.terminal = true
        if work.failure == nil && error != nil { work.failure = .dropped }
        finishWork(work)
    }
    private func finishWork(_ work: HTTPWork) {
        let source = work.source
        guard work.terminal, work.trust == 0, let task = work.task else { return }
        source.tasks.removeValue(forKey: task.taskIdentifier); slots.release()
        defer { wipe(&work.body); settle(source); pump(source) }
        guard source.active, work.grant.live() else { work.completion(.failure(.stale)); return }
        if let failure = work.failure {
            if failure == .dropped, let budget = source.attempt, !budget.reconnect, source.retry == nil {
                budget.reconnect = true; source.retry = work
                for other in source.tasks.values { other.failure = .waiting; other.task?.cancel() }
                invalidate(source); return
            }
            work.completion(.failure(failure)); return
        }
        guard work.response != nil, work.body.count == (work.method == "HEAD" ? 0 : Int(work.end! - work.start! + 1)) else { work.completion(.failure(.representation)); return }
        if work.method == "HEAD" {
            guard let representation = work.headRepresentation else { work.completion(.failure(.representation)); return }
            source.representation = representation
            emit(source, "representation", source.representation!.fields.merging(["permitRevision": source.revision,
                "credentialGeneration": source.credential!, "transportPolicyRevision": source.policy!]) { _, b in b })
        } else { source.randomAccess = true }
        work.completion(.success((work.body, work.grant)))
    }
    func redirect(_ source: Source, task: URLSessionTask, response: HTTPURLResponse, proposed: URLRequest,
                  completion: @escaping (URLRequest?) -> Void) {
        guard let work = source.tasks[task.taskIdentifier], !work.terminal, live(source, grant: work.grant) else { completion(nil); return }
        do {
            guard [301, 302, 303, 307, 308].contains(response.statusCode), work.hops < 3,
                  let location = try selectedHeaders(response)["location"], location.utf8.count <= 4096,
                  location.utf8.allSatisfy({ $0 >= 33 && $0 <= 126 }),
                  !location.contains(","), !location.contains("\\"), !location.contains("%"),
                  !location.split(separator: "/", omittingEmptySubsequences: false).contains(where: { $0 == "." || $0 == ".." }),
                  let base = response.url, let resolved = URL(string: location, relativeTo: base)?.absoluteURL else { throw Failure.redirect }
            let url = try source.location!.checked(resolved.absoluteString)
            guard !work.visited.contains(url.absoluteString),
                  Instant().mono < work.deadline else { throw Failure.redirect }
            work.hops += 1; work.visited.insert(url.absoluteString); work.redirected = completion
            admit(source, kind: work.method == "HEAD" ? "HEAD" : "RANGE", start: work.start, end: work.end, consumer: work.consumer) { grant in
                guard let callback = work.redirected else { return }; work.redirected = nil
                guard let grant = grant, self.live(source, grant: grant), !work.terminal, Instant().mono < work.deadline,
                      let rebuilt = try? self.request(source, url: url, method: work.method, start: work.start, end: work.end) else { callback(nil); work.task?.cancel(); return }
                work.grant = grant; callback(rebuilt)
            }
        } catch { work.failure = .redirect; completion(nil); task.cancel() }
    }
    func challenge(_ source: Source, task: URLSessionTask, challenge: URLAuthenticationChallenge,
                   completion: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard let work = source.tasks[task.taskIdentifier], !work.terminal, !work.challenged,
              live(source, grant: work.grant), Instant().mono < work.deadline,
              challenge.previousFailureCount == 0, challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust, let location = source.location,
              StreamLocation.unbracket(challenge.protectionSpace.host) == location.host,
              challenge.protectionSpace.protocol == "https", challenge.protectionSpace.port == location.port,
              trustOutstanding < 3, slots.take() else {
            source.tasks[task.taskIdentifier]?.failure = .tls; completion(.cancelAuthenticationChallenge, nil)
            if source.active { fail(source, .tls) }; return
        }
        work.challenged = true; work.trust += 1; source.trustCount += 1; trustOutstanding += 1
        let host = location.host, pin = source.pin
        trustQueue.addOperation {
            let accepted = TrustEvaluation.evaluate(trust, host: host, pin: pin)
            self.queue.async {
                work.trust -= 1; source.trustCount -= 1; self.trustOutstanding -= 1; self.slots.release()
                let valid = accepted && self.live(source, grant: work.grant) && Instant().mono < work.deadline && !work.terminal
                if !valid { work.failure = .tls }
                completion(valid ? .useCredential : .cancelAuthenticationChallenge, valid ? URLCredential(trust: trust) : nil)
                if !valid && source.active { self.fail(source, .tls) }
                if work.terminal { self.finishWork(work) }; self.settle(source)
            }
        }
    }
}

extension RemoteOwner {
    func beginRenewal(_ source: Source, f: Fields, received: Instant) throws {
        guard source.purpose != nil, f.n("expectedPermitRevision") == source.revision,
              f.n("expectedCredentialGeneration") == source.credential, f.n("expectedSessionGeneration") == source.sessionGeneration,
              f.n("renewalId") == (try next(source.lastRenewal)) else { throw Failure.renewal }
        let deadline = try Deadline(absolute: f.n("renewalDeadlineAtMs"), remaining: f.n("remainingRenewalMs"), received: received, maximum: 30_000)
        if let renewal = source.renewal {
            guard renewal.id == f.n("renewalId"), f.s("renewalReason") != "REJECTED_401" || renewal.reason == "REJECTED_401" else { throw Failure.renewal }
            // Host's tighter authority deadline can shorten the autonomous first-401 wait.
            guard deadline.absolute <= renewal.deadline.absolute, deadline.monotonic <= renewal.deadline.monotonic else { throw Failure.renewal }
            renewal.deadline = deadline
        } else {
            guard source.active, f.s("renewalReason") == "PREEXPIRY" else { throw Failure.renewal }
            source.renewal = Renewal(id: f.n("renewalId"), reason: "PREEXPIRY", source: source, deadline: deadline)
            quiesce(source)
        }
    }
    func rejected401(_ source: Source) {
        guard !source.retiring, let budget = source.attempt else { return }
        if let renewal = source.renewal, renewal.reason == "REJECTED_401" { return }
        guard !budget.rejected else { budget.failed = true; fail(source, .auth); return }
        budget.rejected = true
        if let renewal = source.renewal { renewal.reason = "REJECTED_401"; return }
        do {
            let now = Instant()
            let deadline = try Deadline(absolute: Int64(now.wall) + 30_000, remaining: 30_000, received: now, maximum: 30_000)
            source.renewal = Renewal(id: try next(source.lastRenewal), reason: "REJECTED_401", source: source, deadline: deadline)
            quiesce(source)
        } catch { fail(source, .auth) }
    }
    func quiesce(_ source: Source) {
        source.admission.block(retire: false)
        if let retry = source.retry { source.retry = nil; retry.completion(.failure(.stale)) }
        cancelReads(source)
        for work in source.tasks.values { work.task?.cancel() }
        invalidate(source)
        source.erase()
        if let transfer = source.transfer {
            wipe(&transfer.buffer); transfer.cursor = 0; transfer.grant = nil; transfer.credit = 0
            if transfer.started { transfer.creditRenewal = source.renewal!.id }
        }
        DispatchQueue.main.async {
            self.audio.quiesce(source)
            self.queue.async { source.renewal?.audioPaused = true; self.settle(source) }
        }
    }
    func accessFields(_ source: Source, _ renewal: Renewal) -> [String: Any] {
        ["renewalId": renewal.id, "renewalReason": renewal.reason, "permitRevision": source.revision,
         "credentialGeneration": source.credential!, "sessionGeneration": source.sessionGeneration!]
    }
    func replaceAccess(_ source: Source, f: Fields, received: Instant, id: Int64) throws {
        guard let renewal = source.renewal, renewal.quiesced, renewal.deadline.live(),
              source.invalidated, source.urlSession == nil, source.tasks.isEmpty, source.trustCount == 0,
              source.transfer?.outstanding == nil, f.n("renewalId") == renewal.id, f.s("renewalReason") == renewal.reason,
              f.n("expectedPermitRevision") == renewal.revision, f.n("expectedCredentialGeneration") == renewal.credential,
              f.n("expectedSessionGeneration") == renewal.session, f.n("permitRevision") > source.revision,
              f.n("credentialGeneration") > renewal.credential, f.n("sessionGeneration") > renewal.session else { throw Failure.renewal }
        let deadline = try f.deadline(received)
        // The exact same binding/family K publication is an indispensable external host witness.
        // This child cannot perform or infer that publication; IPC contains no refresh capability.
        wipe(&source.bearer); source.bearer = Data(f.s("accessToken").utf8)
        source.accessExpiry = f.n("accessExpiresAtMs"); source.credential = f.n("credentialGeneration")
        source.sessionGeneration = f.n("sessionGeneration"); source.revision = f.n("permitRevision"); source.deadline = deadline
        source.lastRenewal = renewal.id; source.renewal = nil; source.admission.publish(deadline, reopen: true)
        source.pendingAudio?.started = Instant().mono
        ack(id, "ADMITTED")
        emit(source, "accessApplied", accessFields(source, renewal))
        onAudio(source) { self.audio.accessApplied(source) }
        createSession(source)
        // No captured autoplay action. A fresh explicit play command must pass the latest I gate.
        if source.representation == nil { inspect(source) }
        pump(source)
    }
    func retire(_ source: Source, code: Failure?) {
        guard !source.retiring else { return }
        source.retiring = true; source.admission.block(retire: true); source.drainStarted = Instant().mono
        source.pendingAudio = nil
        if let retry = source.retry { source.retry = nil; retry.completion(.failure(.stale)) }
        cancelReads(source)
        for load in Array(source.loads.values) { finishLoad(source, load: load, code: .cancelled) }
        for work in source.tasks.values {
            if let redirect = work.redirected { work.redirected = nil; redirect(nil) }
            work.task?.cancel()
        }
        invalidate(source); source.erase()
        if let transfer = source.transfer { transfer.credit = 0; wipe(&transfer.buffer); transfer.cursor = 0; transfer.grant = nil }
        DispatchQueue.main.async { self.audio.retire(source) }
        settle(source)
    }
    func settle(_ source: Source) {
        guard sources[source.key] === source else { return }
        let transportDrained = source.tasks.isEmpty && source.invalidated && source.trustCount == 0 && source.reads.isEmpty && source.httpReservations == 0 && source.transfer?.outstanding == nil
        if let retry = source.retry, source.active, transportDrained {
            source.retry = nil; createSession(source)
            http(source, method: retry.method, start: retry.start, end: retry.end, consumer: retry.consumer, completion: retry.completion)
            return
        }
        if source.active, transportDrained, source.invalidating, source.retry == nil {
            createSession(source); pump(source)
        }
        if let renewal = source.renewal, !source.retiring, !renewal.quiesced, transportDrained, renewal.audioPaused {
            renewal.quiesced = true
            if renewal.reason == "REJECTED_401" {
                emit(source, "authNeeded", ["executionAttempt": source.attempt!.attempt, "renewalId": renewal.id,
                    "permitRevision": renewal.revision, "rejectedCredentialGeneration": renewal.credential, "sessionGeneration": renewal.session])
            }
            emit(source, "accessQuiesced", accessFields(source, renewal))
        }
        guard source.retiring, transportDrained, source.mainDrained, source.mainCallbacks == 0, source.loads.isEmpty else { return }
        if let transfer = source.transfer, !transfer.ended {
            endTransfer(source, transfer, complete: transfer.nextOffset == source.representation?.total)
        }
        guard source.pipeCount == 0 else { return }
        source.file?.close(); source.file = nil
        source.erase()
        if let id = source.retireID {
            source.retireID = nil; sources.removeValue(forKey: source.key); ack(id, "DRAINED")
        } else if closing { sources.removeValue(forKey: source.key) }
        settleAll()
    }
    func settleAll() {
        guard closing else { return }
        for source in Array(sources.values) where source.retiring {
            let done = source.tasks.isEmpty && source.invalidated && source.trustCount == 0 && source.reads.isEmpty && source.httpReservations == 0 && source.loads.isEmpty && source.mainDrained && source.mainCallbacks == 0 && source.pipeCount == 0 && source.transfer?.outstanding == nil
            if done {
                if let transfer = source.transfer, !transfer.ended { endTransfer(source, transfer, complete: transfer.nextOffset == source.representation?.total); continue }
                source.file?.close(); source.file = nil; source.erase(); sources.removeValue(forKey: source.key)
            }
        }
        guard sources.isEmpty, !failed, let closeID = closeID else { return }
        self.closeID = nil
        // AppKit registration removal is joined on main before the final process ACK.
        DispatchQueue.main.async {
            self.audio.stopLifecycle()
            self.queue.async {
                self.pipe.send(["v": 1, "type": "ack", "id": closeID, "outcome": "DRAINED", "code": null]) { success in
                    self.queue.async { self.closeWritten = success; if !success { self.fatal() }; self.finishClose() }
                }
                self.ordinary = nil; self.pendingReleases.removeValue(forKey: closeID)?()
                self.pipe.stopReader()
            }
        }
    }
    func finishClose() {
        guard closing, !failed, !processFinishing, closeWritten, readerEnded, sources.isEmpty else { return }
        processFinishing = true
        timer?.cancel(); timer = nil; pipe.finish(0)
    }
    func sleep() {
        // A fresh install may later use a new host generation; no wake callback revives these.
        lifecycle.retireAll(); intent.stop()
        queue.async { for source in Array(self.sources.values) { self.fail(source, .stale) } }
    }
    func tick() {
        let now = Instant(), elapsed = now.mono - lastClock.mono, wallDelta = now.wall - lastClock.wall
        lastClock = now
        if !handshaken && now.mono - handshakeAt >= 5 { fatal(); return }
        if elapsed < 0 || wallDelta < 0 || abs(wallDelta - elapsed * 1000) > 1000 || elapsed > 2 {
            lifecycle.retireAll(); intent.stop(); for source in Array(sources.values) { fail(source, .stale) }
        }
        for source in Array(sources.values) {
            if source.retiring {
                if let start = source.drainStarted, now.mono - start >= 2 { fatal(); return }
                settle(source); continue
            }
            if let renewal = source.renewal {
                if !renewal.deadline.live() { fail(source, .renewal) }; continue
            }
            if !source.deadline.live() { fail(source, .stale); continue }
            if source.download && now.mono - source.started >= 15 * 60 { fail(source, .stale); continue }
            if let pending = source.pendingAudio, now.mono - pending.started >= 10 { fail(source, .timeout); continue }
            if let pending = source.reads.values.first(where: { !$0.deadline.live() }) {
                source.reads.removeValue(forKey: pending.id); slots.release(); fail(source, .timeout); pending.completion(nil)
                continue
            }
            for work in source.tasks.values where !work.terminal {
                if now.mono >= work.deadline || !work.grant.live() { work.failure = .timeout; work.task?.cancel() }
            }
            pump(source)
        }
    }
    func pump(_ source: Source) {
        guard source.active, source.retry == nil else { return }
        if source.pendingAudio != nil { prepareAudio(source) }
        for load in Array(source.loads.values) where !load.waiting && !load.finished && !load.cancelled { service(source, load: load) }
        if let transfer = source.transfer { pumpTransfer(source, transfer) }
    }
    func load(_ source: Source, request: AVAssetResourceLoadingRequest) -> Bool {
        guard sources[source.key] === source, !source.retiring, request.request.url == source.synthetic,
              request.request.httpMethod == "GET" || request.request.httpMethod == nil,
              request.dataRequest != nil || request.contentInformationRequest != nil,
              source.loads.count < 8, slots.take() else {
            request.finishLoading(with: NSError(domain: "AutPlayRemoteMedia", code: 1)); fail(source, .media); return true
        }
        do {
            let load = try Load(request, total: source.representation!.total)
            source.loads[ObjectIdentifier(request)] = load
            if source.active { service(source, load: load) }; return true
        } catch { slots.release(); request.finishLoading(with: NSError(domain: "AutPlayRemoteMedia", code: 1)); fail(source, .range); return true }
    }
    func cancelledLoad(_ source: Source, request: AVAssetResourceLoadingRequest) {
        guard let load = source.loads[ObjectIdentifier(request)] else { return }
        load.cancelled = true
        finishLoad(source, load: load, code: nil)
        let consumer = ObjectIdentifier(request)
        for pending in Array(source.reads.values) where pending.consumer == consumer {
            source.reads.removeValue(forKey: pending.id); source.cancelledReadThrough = max(source.cancelledReadThrough, pending.id)
            slots.release(); pending.completion(nil)
        }
        for work in source.tasks.values where work.consumer == consumer { work.failure = .cancelled; work.task?.cancel() }
        if let retry = source.retry, retry.consumer == consumer { source.retry = nil; retry.completion(.failure(.cancelled)); settle(source) }
        // Retain URLSession task owners until didComplete; no canceled descriptor receives bytes.
    }
    func finishLoad(_ source: Source, load: Load, code: Failure?) {
        guard !load.finished else { return }; load.finished = true
        source.loads.removeValue(forKey: ObjectIdentifier(load.request)); slots.release()
        if !load.cancelled && !load.request.isCancelled && !load.request.isFinished {
            if let code = code { load.request.finishLoading(with: NSError(domain: "AutPlayRemoteMedia", code: code == .cancelled ? 2 : 1)) }
            else { load.request.finishLoading() }
        }
    }
    func contentInfo(_ source: Source, load: Load) {
        guard let info = load.request.contentInformationRequest else { return }
        info.contentType = source.representation!.format.uti
        info.contentLength = source.representation!.total
        info.isByteRangeAccessSupported = source.randomAccess
    }
    func service(_ source: Source, load: Load) {
        guard source.active, !load.waiting, !load.finished, !load.cancelled, !load.request.isCancelled else { return }
        if source.file == nil && !networkAllows(source) { return }
        if source.file == nil && (!source.randomAccess || source.invalidating || source.tasks.count + source.httpReservations >= 2) {
            guard !source.randomAccess, !source.invalidating, source.tasks.count + source.httpReservations < 2 else { return }
            load.waiting = true
            fetch(source, start: 0, end: min(1, source.representation!.total - 1), consumer: ObjectIdentifier(load.request)) { result in
                load.waiting = false
                guard !load.finished, !load.cancelled, source.active else { return }
                switch result { case .success: self.contentInfo(source, load: load); self.service(source, load: load)
                case let .failure(code): if code != .waiting && code != .offline { self.fail(source, code) } }
            }
            return
        }
        contentInfo(source, load: load)
        guard let data = load.request.dataRequest else { finishLoad(source, load: load, code: nil); return }
        let offset = max(data.requestedOffset, data.currentOffset)
        guard offset <= maxObject else { fail(source, .range); return }
        if offset >= load.end { finishLoad(source, load: load, code: nil); return }
        let end = min(load.end - 1, offset + maxRange - 1)
        load.waiting = true
        fetch(source, start: offset, end: end, consumer: ObjectIdentifier(load.request)) { result in
            load.waiting = false
            guard source.active, !load.finished, !load.cancelled, !load.request.isCancelled else { return }
            switch result {
            case let .success((bytes, grant)):
                guard self.live(source, grant: grant), Int64(bytes.count) == end - offset + 1,
                      max(data.requestedOffset, data.currentOffset) == offset else { self.fail(source, .range); return }
                self.contentInfo(source, load: load)
                // Each callback provides only this authorized bounded interval. The descriptor
                // finishes only after its entire requested interval (not its first chunk).
                data.respond(with: bytes)
                self.service(source, load: load)
            case let .failure(code): if code != .waiting && code != .offline { self.fail(source, code) }
            }
        }
    }
    func fetch(_ source: Source, start: Int64, end: Int64, consumer: ObjectIdentifier? = nil, completion: @escaping (Result<(Data, Deadline), Failure>) -> Void) {
        if let file = source.file {
            admit(source, kind: "FILE", start: start, end: end, consumer: consumer) { grant in
                guard let grant = grant, self.live(source, grant: grant) else { completion(.failure(.stale)); return }
                do {
                    var data = try file.read(start: start, end: end)
                    defer { wipe(&data) }
                    guard self.live(source, grant: grant) else { throw Failure.stale }
                    completion(.success((data, grant)))
                } catch { completion(.failure(error as? Failure ?? .source)) }
            }
            return
        }
        if let index = source.cache.firstIndex(where: { $0.start <= start && $0.start + Int64($0.bytes.count) > end }) {
            let block = source.cache.remove(at: index); source.cache.insert(block, at: 0)
            admit(source, kind: "CACHE", start: start, end: end, consumer: consumer) { grant in
                guard let grant = grant, self.live(source, grant: grant) else { completion(.failure(.stale)); return }
                completion(.success((block.bytes.subdata(in: Int(start - block.start)..<Int(end - block.start + 1)), grant)))
            }
            return
        }
        http(source, method: "GET", start: start, end: end, consumer: consumer) { result in
            if case let .success((bytes, grant)) = result, !source.download, self.live(source, grant: grant) {
                while (source.cache.count >= 32 || source.cacheBytes + bytes.count > 8 * 1024 * 1024), !source.cache.isEmpty {
                    var old = source.cache.removeLast(); source.cacheBytes -= old.bytes.count; wipe(&old.bytes)
                }
                source.cache.insert(CacheBlock(start: start, bytes: bytes), at: 0); source.cacheBytes += bytes.count
            }
            completion(result)
        }
    }
    func receipt(_ f: Fields) throws {
        guard let source = sources[SourceKey(f)], let transfer = source.transfer, !transfer.ended,
              transfer.id == f.s("transferId"), transfer.attempt == f.n("attempt") else { throw Failure.wire }
        let disposition = f.s("disposition"), credit = Int(f.n("creditBytes"))
        if disposition == "START" {
            // The same START opens a new empty credit slot once after exact accessApplied;
            // it never acknowledges a frame or changes this transfer's sequence/prefix/lease.
            let resumed = transfer.startEpoch != source.lastRenewal &&
                (source.active ? transfer.creditRenewal == source.lastRenewal : transfer.creditRenewal == source.renewal?.id)
            guard !source.retiring, source.active || source.renewal?.deadline.live() == true,
                  !transfer.started || resumed, transfer.credit == 0, transfer.outstanding == nil, f.n("sequence") == 0,
                  f.n("nextOffset") == transfer.nextOffset, credit > 0 else { throw Failure.wire }
            transfer.started = true
            transfer.startEpoch = source.lastRenewal
            transfer.creditRenewal = source.renewal?.id
        } else {
            guard let outstanding = transfer.outstanding, f.n("sequence") == outstanding.sequence,
                  disposition == "WRITTEN" || disposition == "DISCARDED" else { throw Failure.wire }
            if disposition == "WRITTEN" {
                guard f.n("nextOffset") == transfer.nextOffset + Int64(outstanding.count) else { throw Failure.wire }
                transfer.nextOffset = f.n("nextOffset")
            } else {
                guard source.retiring, credit == 0, f.n("nextOffset") == transfer.nextOffset else { throw Failure.wire }
            }
            guard !source.retiring || credit == 0 else { throw Failure.wire }
            transfer.outstanding = nil
        }
        // A pre-quiescence positive candidate can already be in the pipe when an autonomous
        // 401 closes admission. Settle it once, withholding its credit until the host observes
        // quiescence and supplies a freshly guarded START after the applied successor.
        transfer.credit = source.renewal == nil ? credit : 0
        guard transfer.nextOffset != source.representation!.total || credit == 0 else { throw Failure.wire }
        if source.active { pumpTransfer(source, transfer) }
        settle(source)
    }
    func pumpTransfer(_ source: Source, _ transfer: Transfer) {
        guard !transfer.ended, transfer.outstanding == nil else { return }
        if transfer.nextOffset == source.representation!.total {
            guard !transfer.waiting, source.tasks.isEmpty else { return }
            endTransfer(source, transfer, complete: true); return
        }
        guard source.active, transfer.started, transfer.credit > 0, !transfer.waiting, networkAllows(source), !source.invalidating else { return }
        if transfer.cursor < transfer.buffer.count {
            guard let grant = transfer.grant, live(source, grant: grant) else { fail(source, .stale); return }
            let count = min(transfer.credit, maxData, transfer.buffer.count - transfer.cursor)
            guard count > 0, transfer.nextOffset + Int64(count) <= source.representation!.total else { fail(source, .wire); return }
            do {
                transfer.sequence = try next(transfer.sequence)
                var id = UUID(uuidString: transfer.id)!.uuid
                var body = Data([2]); withUnsafeBytes(of: &id) { body.append(contentsOf: $0) }
                for value in [transfer.attempt, transfer.sequence, transfer.nextOffset] {
                    var value = UInt64(value).bigEndian; withUnsafeBytes(of: &value) { body.append(contentsOf: $0) }
                }
                body.append(transfer.buffer.subdata(in: transfer.cursor..<(transfer.cursor + count)))
                transfer.cursor += count; transfer.credit = 0; transfer.outstanding = (transfer.sequence, count)
                if transfer.cursor == transfer.buffer.count { wipe(&transfer.buffer); transfer.cursor = 0; transfer.grant = nil }
                source.pipeCount += 1
                pipe.sendBody(body) { success in self.queue.async {
                    source.pipeCount -= 1; if !success { self.fatal() }; self.settle(source)
                } }
                wipe(&body)
            } catch { fatal() }
            return
        }
        guard source.tasks.isEmpty, source.httpReservations == 0 else { return }
        let start = transfer.nextOffset, end = min(source.representation!.total - 1, start + maxRange - 1)
        transfer.waiting = true
        fetch(source, start: start, end: end) { result in
            transfer.waiting = false
            guard source.active, !transfer.ended else { self.settle(source); return }
            switch result {
            case let .success((bytes, grant)):
                transfer.buffer = bytes; transfer.cursor = 0; transfer.grant = grant; self.pumpTransfer(source, transfer)
            case let .failure(code): if code != .waiting && code != .offline { self.fail(source, code) }
            }
        }
    }
    func endTransfer(_ source: Source, _ transfer: Transfer, complete: Bool) {
        guard !transfer.ended, transfer.outstanding == nil else { return }
        transfer.ended = true; wipe(&transfer.buffer); transfer.credit = 0
        let outcome = complete ? "DELIVERED" : (source.failure == nil ? "CANCELLED" : "FAILED")
        emit(source, "transferEnd", ["transferId": transfer.id, "attempt": transfer.attempt, "nextOffset": transfer.nextOffset,
            "outcome": outcome, "code": complete ? null : ((source.failure ?? .cancelled).rawValue as Any)])
    }
    func prepareAudio(_ source: Source) {
        guard let pending = source.pendingAudio, !source.preparingAudio, source.active, source.retry == nil,
              source.file != nil || (networkAllows(source) && !source.invalidating && source.tasks.count + source.httpReservations < 2) else { return }
        source.preparingAudio = true
        fetch(source, start: 0, end: min(65_535, source.representation!.total - 1)) { result in
            guard source.active, source.pendingAudio === pending else { source.preparingAudio = false; return }
            switch result {
            case let .success((bytes, grant)):
                guard self.live(source, grant: grant), source.representation!.format.acceptsContainer(bytes) else {
                    source.preparingAudio = false; self.fail(source, .media); return
                }
                guard self.slots.take() else { source.preparingAudio = false; self.fail(source, .capacity); return }
                let admission = source.admission.capture(), lease = pending.fields.s("audioLeaseId")
                source.mainCallbacks += 1
                DispatchQueue.main.async {
                    let confirmed = self.audio.confirmContainer(source, lease: lease, admission: admission, grant: grant)
                    self.queue.async {
                        // The preparation stays owned through its matching main-queue ACK.
                        // Renewal invalidates the old capture, leaving one recoverable pending
                        // preparation for a fresh read under the successor, with the same lease.
                        if source.pendingAudio === pending, confirmed { source.pendingAudio = nil }
                        source.preparingAudio = false; source.mainCallbacks -= 1; self.slots.release()
                        self.settle(source)
                        if !confirmed, source.admission.capture() != admission { self.pump(source) }
                    }
                }
            case let .failure(code):
                source.preparingAudio = false
                if code != .waiting && code != .offline { self.fail(source, code) }
            }
        }
    }
}

private final class SessionDelegate: NSObject, URLSessionDataDelegate {
    private weak var owner: RemoteOwner?
    private let source: Source
    init(owner: RemoteOwner, source: Source) { self.owner = owner; self.source = source }
    func urlSession(_ session: URLSession, didBecomeInvalidWithError error: Error?) { owner?.invalidated(source, session: session) }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let owner = owner else { completionHandler(.cancel); return }
        owner.response(source, task: dataTask, response: response, completion: completionHandler)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) { owner?.data(source, task: dataTask, bytes: data) }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) { owner?.completed(source, task: task, error: error) }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let owner = owner else { completionHandler(nil); return }
        owner.redirect(source, task: task, response: response, proposed: request, completion: completionHandler)
    }
    // Apple routes session-wide challenges here when the session-level method is absent.
    // Keeping the task identifies the exact bounded deadline and drain obligation.
    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard let owner = owner else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        owner.challenge(source, task: task, challenge: challenge, completion: completionHandler)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    willCacheResponse proposedResponse: CachedURLResponse, completionHandler: @escaping (CachedURLResponse?) -> Void) { completionHandler(nil) }
}

private final class ResourceDelegate: NSObject, AVAssetResourceLoaderDelegate {
    private weak var owner: RemoteOwner?
    private let source: Source
    init(owner: RemoteOwner, source: Source) { self.owner = owner; self.source = source }
    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, shouldWaitForLoadingOfRequestedResource loadingRequest: AVAssetResourceLoadingRequest) -> Bool {
        guard let owner = owner else { return false }; return owner.load(source, request: loadingRequest)
    }
    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, didCancel loadingRequest: AVAssetResourceLoadingRequest) { owner?.cancelledLoad(source, request: loadingRequest) }
    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, shouldWaitForRenewalOfRequestedResource renewalRequest: AVAssetResourceRenewalRequest) -> Bool { false }
    func resourceLoader(_ resourceLoader: AVAssetResourceLoader, shouldWaitForResponseTo authenticationChallenge: URLAuthenticationChallenge) -> Bool { false }
}

private final class SeekOperation {
    let token: Int64, id: Int64?, fence: Intent, position: Int64, deadline: Double
    var reported = false, dispatched = false, settled = false
    init(token: Int64, id: Int64?, fence: Intent, position: Int64) {
        self.token = token; self.id = id; self.fence = fence; self.position = position; deadline = Instant().mono + 5
    }
}
private final class AudioState {
    let source: Source, lease: String, asset: AVURLAsset, loader: ResourceDelegate, player: AVPlayer
    let installFence: Intent
    var latestFence: Intent
    var item: AVPlayerItem?, itemObservation: NSKeyValueObservation?, transportObservation: NSKeyValueObservation?
    var notifications: [NSObjectProtocol] = [], period: Any?
    var loading: Task<Void, Never>?, pendingCallbacks = 0, pendingSeek: SeekOperation?
    var seeks: [Int64: SeekOperation] = [:], seekToken: Int64 = 0, lastSeek: Int64 = 0
    var duration: Int64?, desiredPosition: Int64, playFence: Intent?
    var status = "BUFFERING", error: Failure?, retired = false, detached = false, ended = false, ready = false, containerVerified = false
    var endObservation: NSObjectProtocol?
    var openedAt = Instant().mono, startDeadline: Double?, lastProgressAt = Instant().mono, lastPosition: Int64 = 0
    var lastEventAt: Double = 0
    init(source: Source, f: Fields, owner: RemoteOwner) {
        self.source = source; lease = f.s("audioLeaseId"); installFence = Intent(f); latestFence = Intent(f); desiredPosition = f.n("positionMs")
        let resourceDelegate = ResourceDelegate(owner: owner, source: source)
        let resourceAsset = AVURLAsset(url: source.synthetic!, options: [AVURLAssetPreferPreciseDurationAndTimingKey: true,
            AVURLAssetReferenceRestrictionsKey: NSNumber(value: AVAssetReferenceRestrictions.forbidAll.rawValue)])
        let audioPlayer = AVPlayer()
        loader = resourceDelegate; asset = resourceAsset; player = audioPlayer
        resourceAsset.resourceLoader.setDelegate(resourceDelegate, queue: owner.queue)
        audioPlayer.actionAtItemEnd = .pause; audioPlayer.automaticallyWaitsToMinimizeStalling = true
    }
}

// Every method in this class is called on the main RunLoop. It never reads a token,
// resolves a queue entry, rotates credentials, advances a logical listen or opens a file.
private final class AudioOwner {
    private unowned let owner: RemoteOwner
    private var current: AudioState?
    private var retired: [SourceKey: AudioState] = [:]
    private var workspace: [NSObjectProtocol] = []
    private var timer: Timer?
    init(owner: RemoteOwner) { self.owner = owner }
    func startLifecycle() {
        precondition(Thread.isMainThread)
        let center = NSWorkspace.shared.notificationCenter
        workspace.append(center.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in
            guard let self = self else { return }; self.owner.sleep(); self.stopImmediately()
        })
        workspace.append(center.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            guard let self = self else { return }; self.owner.sleep(); self.stopImmediately()
        })
        let timer = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common); self.timer = timer
    }
    func stopLifecycle() {
        for observer in workspace { NSWorkspace.shared.notificationCenter.removeObserver(observer) }; workspace.removeAll()
        timer?.invalidate(); timer = nil
    }
    func stopImmediately() {
        guard let state = current else { return }
        state.playFence = nil; state.startDeadline = nil; state.player.pause()
    }
    func applyIntent(_ fence: Intent) {
        guard let state = current else { return }
        if fence.selection == state.installFence.selection { state.latestFence = fence }
        if state.playFence != fence || !owner.intent.matches(fence, play: true) {
            state.playFence = nil; state.startDeadline = nil; state.player.pause()
        }
        if let seek = state.pendingSeek, seek.fence != fence { interrupt(state, seek); state.item?.cancelPendingSeeks() }
        observe(state)
    }
    func command(_ f: Fields, source: Source) {
        precondition(Thread.isMainThread)
        if f.s("op") == "activateAudio" { activate(f, source: source); return }
        guard let state = current, state.source === source, state.lease == f.s("audioLeaseId"), !state.retired else {
            owner.audioFailed(source, .intent); return
        }
        let fence = Intent(f)
        if f.s("op") == "pause" {
            state.playFence = nil; state.startDeadline = nil; state.player.pause(); observe(state); return
        }
        guard source.admission.live(), owner.intent.matches(fence), !state.ended, state.error == nil else { owner.audioFailed(source, .intent); return }
        if f.s("op") == "play" {
            guard owner.intent.matches(fence, play: true) else { owner.audioFailed(source, .intent); return }
            state.playFence = fence
            if state.ready && state.pendingSeek == nil { play(state) }
        } else if f.s("op") == "seek" {
            guard f.n("seekId") > state.lastSeek else { owner.audioFailed(source, .intent); return }
            state.lastSeek = f.n("seekId")
            seek(state, id: f.n("seekId"), fence: fence, position: f.n("positionMs"))
        }
    }
    private func activate(_ f: Fields, source: Source) {
        guard current == nil, retired.isEmpty, source.admission.live(), owner.intent.matches(Intent(f)),
              !f.b("autoplay") else { owner.audioFailed(source, .capacity); return }
        let state = AudioState(source: source, f: f, owner: owner); current = state
        state.player.pause(); emit(state)
    }
    func confirmContainer(_ source: Source, lease: String, admission: Int64, grant: Deadline) -> Bool {
        guard let state = current, state.source === source, state.lease == lease, isCurrent(state),
              source.admission.live(admission), grant.live() else { return false }
        if !state.containerVerified {
            state.containerVerified = true
            if state.loading == nil && state.item == nil { loadAsset(state) }
        }
        return true
    }
    private func loadAsset(_ state: AudioState) {
        guard isCurrent(state), state.loading == nil, state.item == nil, owner.slots.take() else { fail(state, .capacity); return }
        let source = state.source
        state.pendingCallbacks += 1
        state.loading = Task { @MainActor [self, state] in
            defer {
                state.loading = nil; state.pendingCallbacks -= 1; owner.slots.release(); drained(state)
            }
            do {
                guard !Task.isCancelled, isCurrent(state) else { return }
                let playable = try await state.asset.load(.isPlayable)
                guard !Task.isCancelled, isCurrent(state) else { return }
                let protected = try await state.asset.load(.hasProtectedContent)
                guard !Task.isCancelled, isCurrent(state) else { return }
                let duration = try await state.asset.load(.duration)
                guard !Task.isCancelled, isCurrent(state) else { return }
                let tracks = try await state.asset.load(.tracks)
                guard !Task.isCancelled, isCurrent(state), tracks.count == 1, tracks[0].mediaType == .audio else {
                    if isCurrent(state) { fail(state, .media) }; return
                }
                let descriptions = try await tracks[0].load(.formatDescriptions)
                guard !Task.isCancelled, isCurrent(state) else { return }
                let seconds = CMTimeGetSeconds(duration)
                guard playable, !protected, seconds.isFinite, seconds >= 0.001, seconds <= Double(maxPosition) / 1000,
                      !descriptions.isEmpty, descriptions.count <= 8,
                      descriptions.allSatisfy({ CMFormatDescriptionGetMediaType($0) == kCMMediaType_Audio && source.representation!.format.acceptsCodec(CMFormatDescriptionGetMediaSubType($0)) }) else { fail(state, .media); return }
                state.duration = Int64(seconds * 1000)
                installItem(state)
            } catch {
                if !Task.isCancelled, isCurrent(state) { fail(state, .media) }
            }
        }
    }
    private func installItem(_ state: AudioState) {
        guard isCurrent(state), state.source.admission.live() else { return }
        let item = AVPlayerItem(asset: state.asset); state.item = item
        state.itemObservation = item.observe(\.status, options: [.initial, .new]) { [weak self, weak state] _, _ in
            DispatchQueue.main.async {
                guard let self = self, let state = state, self.isCurrent(state), state.item === item else { return }
                if item.status == .failed { self.fail(state, .media) }
                else if item.status == .readyToPlay, !state.ready, state.pendingSeek == nil {
                    state.ready = true
                    // Readiness never restores the installation's autoplay flag (always false).
                    self.seek(state, id: nil, fence: state.latestFence, position: state.desiredPosition)
                }
            }
        }
        state.transportObservation = state.player.observe(\.timeControlStatus, options: [.new]) { [weak self, weak state] _, _ in
            DispatchQueue.main.async {
                guard let self = self, let state = state, self.isCurrent(state) else { return }
                self.observe(state)
            }
        }
        let center = NotificationCenter.default
        state.notifications.append(center.addObserver(forName: .AVPlayerItemFailedToPlayToEndTime, object: item, queue: .main) { [weak self, weak state] _ in
            guard let self = self, let state = state, self.isCurrent(state) else { return }; self.fail(state, .media)
        })
        state.period = state.player.addPeriodicTimeObserver(forInterval: CMTime(value: 250, timescale: 1000), queue: .main) { [weak self, weak state] _ in
            guard let self = self, let state = state, self.isCurrent(state) else { return }; self.observe(state)
        }
        state.player.replaceCurrentItem(with: item)
    }
    private func isCurrent(_ state: AudioState) -> Bool { current === state && !state.retired && state.error == nil }
    private func observedPosition(_ state: AudioState) -> Int64? {
        let seconds = CMTimeGetSeconds(state.player.currentTime())
        guard seconds.isFinite, seconds >= 0, seconds <= Double(maxPosition) / 1000 else { return nil }
        return Int64(seconds * 1000)
    }
    private func position(_ state: AudioState) -> Int64 { observedPosition(state) ?? state.lastPosition }
    private func removeEnd(_ state: AudioState) {
        if let observer = state.endObservation { NotificationCenter.default.removeObserver(observer); state.endObservation = nil }
    }
    private func armEnd(_ state: AudioState, epoch: Int64) {
        removeEnd(state)
        guard let item = state.item else { return }
        state.endObservation = NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main) { [weak self, weak state] _ in
            guard let self = self, let state = state, self.isCurrent(state), state.seekToken == epoch,
                  state.pendingSeek == nil, state.ready, state.source.admission.live(), !state.ended,
                  let duration = state.duration, let actual = self.observedPosition(state), actual >= max(0, duration - 250) else { return }
            state.player.pause(); state.playFence = nil; state.ended = true; state.status = "ENDED"; self.emit(state)
        }
    }
    private func play(_ state: AudioState) {
        guard isCurrent(state), !state.ended, state.ready, state.pendingSeek == nil,
              let fence = state.playFence, owner.intent.matches(fence, play: true), state.source.admission.live(),
              state.item?.status == .readyToPlay, state.player.currentItem === state.item else { return }
        state.startDeadline = Instant().mono + 5; state.lastProgressAt = Instant().mono
        // Final gate immediately adjacent to the only native play call in this process.
        guard owner.intent.matches(fence, play: true), state.source.admission.live() else { return }
        state.player.play(); observe(state)
    }
    private func seek(_ state: AudioState, id: Int64?, fence: Intent, position: Int64) {
        guard isCurrent(state), state.source.admission.live(), owner.intent.matches(fence), !state.ended else {
            if let id = id { seekResult(state, id: id, fence: fence, outcome: "INTERRUPTED", code: .interrupted) }; return
        }
        if let old = state.pendingSeek { interrupt(state, old) }
        removeEnd(state); state.player.pause(); state.startDeadline = nil; state.item?.cancelPendingSeeks()
        guard owner.slots.take() else { fail(state, .capacity); return }
        do { state.seekToken = try next(state.seekToken) } catch { owner.slots.release(); fail(state, .wire); return }
        let operation = SeekOperation(token: state.seekToken, id: id, fence: fence, position: min(position, state.duration ?? position))
        state.pendingSeek = operation; state.seeks[operation.token] = operation
        state.pendingCallbacks += 1; state.status = "BUFFERING"; emit(state)
        if state.item?.status == .readyToPlay { dispatchSeek(state, operation) }
        // If still loading, readiness will dispatch this same identified seek; no optimistic result.
    }
    private func dispatchSeek(_ state: AudioState, _ operation: SeekOperation) {
        guard !operation.dispatched, !operation.settled else { return }
        guard isCurrent(state), state.pendingSeek === operation, state.source.admission.live(), owner.intent.matches(operation.fence) else {
            completeUndispatched(state, operation); return
        }
        operation.dispatched = true
        state.player.seek(to: CMTime(value: operation.position, timescale: 1000), toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
            DispatchQueue.main.async {
                guard let self = self, !operation.settled else { return }
                operation.settled = true
                state.seeks.removeValue(forKey: operation.token); state.pendingCallbacks -= 1; self.owner.slots.release()
                defer { self.drained(state) }
                guard self.isCurrent(state), state.pendingSeek === operation, !operation.reported else { return }
                state.pendingSeek = nil
                guard state.source.admission.live(), self.owner.intent.matches(operation.fence) else {
                    self.interrupt(state, operation); return
                }
                if finished, let actual = self.observedPosition(state) {
                    operation.reported = true
                    state.lastPosition = actual; state.lastProgressAt = Instant().mono
                    if let id = operation.id { self.seekResult(state, id: id, fence: operation.fence, outcome: "COMPLETED", code: nil) }
                    self.armEnd(state, epoch: operation.token); state.status = "READY"; self.emit(state)
                    // A seek can resume only a current explicit play, never captured autoplay.
                    self.play(state)
                } else {
                    operation.reported = true
                    if let id = operation.id { self.seekResult(state, id: id, fence: operation.fence, outcome: "FAILED", code: .seek) }
                    self.fail(state, .seek)
                }
            }
        }
    }
    private func completeUndispatched(_ state: AudioState, _ operation: SeekOperation) {
        guard !operation.dispatched, !operation.settled else { return }
        operation.settled = true
        interrupt(state, operation)
        state.seeks.removeValue(forKey: operation.token); state.pendingCallbacks -= 1; owner.slots.release(); drained(state)
    }
    private func interrupt(_ state: AudioState, _ operation: SeekOperation) {
        if !operation.reported {
            operation.reported = true
            if let id = operation.id { seekResult(state, id: id, fence: operation.fence, outcome: "INTERRUPTED", code: .interrupted) }
        }
        if state.pendingSeek === operation { state.pendingSeek = nil }
        if !operation.dispatched && !operation.settled { completeUndispatched(state, operation) }
    }
    private func seekResult(_ state: AudioState, id: Int64, fence: Intent, outcome: String, code: Failure?) {
        owner.audioEvent(state.source, type: "seekResult", fields: fence.fields.merging(["audioLeaseId": state.lease, "seekId": id,
            "outcome": outcome, "positionMs": position(state), "code": code?.rawValue as Any? ?? null]) { _, b in b })
    }
    private func observe(_ state: AudioState) {
        guard isCurrent(state), !state.ended else { return }
        if !state.source.admission.live() || state.playFence.map({ !owner.intent.matches($0, play: true) }) == true { state.player.pause(); state.startDeadline = nil }
        let playing = state.player.timeControlStatus == .playing && state.player.rate > 0
        let actual = position(state)
        if actual > state.lastPosition { state.lastPosition = actual; state.lastProgressAt = Instant().mono }
        if playing { state.startDeadline = nil }
        state.status = !state.ready || state.pendingSeek != nil || state.player.timeControlStatus == .waitingToPlayAtSpecifiedRate ? "BUFFERING" : "READY"
        emit(state)
    }
    private func emit(_ state: AudioState) {
        let fence = state.playFence ?? state.pendingSeek?.fence ?? state.latestFence
        let playing = state.status == "READY" && !state.retired && state.player.timeControlStatus == .playing && state.player.rate > 0
        state.lastEventAt = Instant().mono
        owner.audioEvent(state.source, type: "playback", fields: fence.fields.merging(["audioLeaseId": state.lease,
            "status": state.status, "positionMs": position(state), "durationMs": state.duration as Any? ?? null,
            "isPlaying": playing, "code": state.error?.rawValue as Any? ?? null]) { _, b in b })
    }
    private func fail(_ state: AudioState, _ code: Failure) {
        guard isCurrent(state) else { return }
        state.player.pause(); state.playFence = nil; state.error = code; state.status = "ERROR"; emit(state)
        owner.audioFailed(state.source, code)
    }
    func quiesce(_ source: Source) {
        guard let state = current, state.source === source else { return }
        state.player.pause(); state.playFence = nil; state.startDeadline = nil
        removeEnd(state)
        if let operation = state.pendingSeek { interrupt(state, operation); state.item?.cancelPendingSeeks() }
        observe(state)
    }
    func accessApplied(_ source: Source) {
        guard let state = current, state.source === source, isCurrent(state), source.admission.live() else { return }
        state.openedAt = Instant().mono; state.lastProgressAt = Instant().mono
        if state.containerVerified && state.item == nil && state.loading == nil { loadAsset(state) }
        // Re-arm only the current native epoch; no pre-renewal play request is restored.
        if state.ready && state.pendingSeek == nil { armEnd(state, epoch: state.seekToken) }
        observe(state)
    }
    func retire(_ source: Source) {
        guard let state = current, state.source === source else {
            if retired[source.key] == nil { owner.audioDrained(source) }; return
        }
        state.retired = true; state.player.pause(); state.playFence = nil; state.startDeadline = nil
        state.itemObservation?.invalidate(); state.itemObservation = nil
        state.transportObservation?.invalidate(); state.transportObservation = nil
        if let period = state.period { state.player.removeTimeObserver(period); state.period = nil }
        for observer in state.notifications { NotificationCenter.default.removeObserver(observer) }; state.notifications.removeAll()
        removeEnd(state)
        if let operation = state.pendingSeek { interrupt(state, operation) }
        state.loading?.cancel(); state.asset.cancelLoading(); state.item?.cancelPendingSeeks(); state.player.cancelPendingPrerolls()
        state.player.replaceCurrentItem(with: nil); state.item = nil
        state.status = "IDLE"; state.error = nil; emit(state)
        current = nil; retired[source.key] = state; state.detached = true; drained(state)
    }
    private func drained(_ state: AudioState) {
        guard state.retired, state.detached, state.pendingCallbacks == 0, state.seeks.isEmpty else { return }
        state.asset.resourceLoader.setDelegate(nil, queue: nil)
        retired.removeValue(forKey: state.source.key); owner.audioDrained(state.source)
    }
    private func tick() {
        guard let state = current, isCurrent(state) else { return }
        let now = Instant().mono
        if !state.source.admission.live() {
            state.player.pause(); state.playFence = nil; state.startDeadline = nil; return
        }
        if !state.ready && now - state.openedAt >= 10 { fail(state, .timeout); return }
        if let operation = state.pendingSeek {
            if now >= operation.deadline { interrupt(state, operation); state.item?.cancelPendingSeeks(); fail(state, .seek); return }
            if state.item?.status == .readyToPlay, !state.ready { state.ready = true; dispatchSeek(state, operation) }
        }
        if let start = state.startDeadline, now >= start { fail(state, .timeout); return }
        if state.playFence != nil && state.ready && state.pendingSeek == nil && now - state.lastProgressAt >= 10 { fail(state, .timeout); return }
        if now - state.lastEventAt >= 0.5 { observe(state) }
    }
}

// Fixed application payload accounting: one <=8 MiB/32-entry playback cache, <=8 cached
// read captures of <=256 KiB, <=3 task bodies of <=256 KiB (including retired tasks),
// one <=256 KiB transfer buffer, one outstanding <=61,440-byte transfer frame, <=32
// bounded protocol writes, and <=3 queued trust evaluations with <=8*64 KiB DER each.
// Even charging temporary body/frame/DER copies, these pools fit within 16 MiB; there
// is no object-sized buffer or persistent stream cache. Foundation/decoder/kernel/TLS
// allocations are outside this application payload bound and require real measurement.
//
// SOURCE-ONLY handoff: future compiler/codec/redirect/raw duplicate-header/TLS/DER/ATS,
// Range/credit/renewal/audio-exclusion/drain/sleep/cost/bounds/native-format/seek and
// signed-package gates remain UNVERIFIED. HTTPURLResponse exposes Foundation-normalized
// headers: accepted M1-H1 validates observable values and can accept hidden duplicates.
// Dictionary uniqueness is not proof of original wire uniqueness; record actual outcomes.
// Apple documents replacement of a previous differently-cased key's value here:
// https://developer.apple.com/documentation/foundation/httpurlresponse/allheaderfields
// Consequently this public dictionary cannot attest original header multiplicity.
// H1 explicitly replaces the universal raw duplicate refusal guarantee; it does not change
// transport or relax origin/credentials/Range/TLS/authority/drain requirements.
// The JVM still must supply real package identity, authority/read/K-publication/audio
// witnesses and file/J ownership. No build integration, K custody or host adoption is
// implemented by this file; no matrix point follows from source implementation.
//
// Stdout is protocol-only; native framework diagnostics may contain runtime capabilities.
// Owned Data is wiped; immutable Swift/Foundation/URLSession/kernel copies are not promised erased.
private let diagnosticSink = Darwin.open("/dev/null", O_WRONLY)
if diagnosticSink >= 0 { _ = dup2(diagnosticSink, STDERR_FILENO); Darwin.close(diagnosticSink) }
guard CommandLine.arguments.count == 1 else { exit(2) }
private let remotePipe = Pipe()
private let remoteOwner = RemoteOwner(pipe: remotePipe)
remoteOwner.start()
RunLoop.main.run()
