// Experimental macOS CLI. Input is one in-memory JSON document on stdin.
// No credentials in argv, no raw URLs/errors in output, no persisted queue.
import AVFoundation
import Foundation
import UniformTypeIdentifiers

private let maximumBytes: Int64 = 32 * 1024 * 1024

private struct Input: Decodable {
    let kind: String
    let source: String
    let token: String?
    let action: String
}

private struct ProbeError: Error {
    let code: String
}

private func failure(_ code: Int) -> NSError {
    return NSError(domain: "AudioSpike", code: code, userInfo: nil)
}

// A custom resource scheme keeps HTTP authority and bearer injection in one owner.
// This deliberately rejects cross-origin redirects; it is NOT a production cache.
private final class ResourceLoader: NSObject, AVAssetResourceLoaderDelegate {
    let queue = DispatchQueue(label: "app.autplay.audio-spike.loader")
    let origin: URL
    let token: String
    private var active: [ObjectIdentifier: Fetch] = [:]
    private var counts: [String: Int] = ["started": 0, "cancelled": 0, "redirect_rejected": 0,
                                       "range_validated": 0, "completed": 0, "failed": 0]

    init(origin: URL, token: String) {
        self.origin = origin
        self.token = token
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader,
                        shouldWaitForLoadingOfRequestedResource loadingRequest: AVAssetResourceLoadingRequest) -> Bool {
        let key = ObjectIdentifier(loadingRequest)
        guard active.count < 8, counts["started", default: 0] < 128 else {
            loadingRequest.finishLoading(with: failure(1))
            return true
        }
        let dataRequest = loadingRequest.dataRequest
        let start = max(dataRequest?.requestedOffset ?? 0, dataRequest?.currentOffset ?? 0)
        let length = Int64(dataRequest?.requestedLength ?? 2)
        guard start >= 0, start < maximumBytes, length > 0 else {
            loadingRequest.finishLoading(with: failure(2))
            return true
        }
        let end = dataRequest?.requestsAllDataToEndOfResource == true
            ? maximumBytes - 1 : start + min(length, maximumBytes - start) - 1
        var request = URLRequest(url: origin, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.setValue("bytes=\(start)-\(end)", forHTTPHeaderField: "Range")
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        let fetch = Fetch(owner: self, key: key, loading: loadingRequest, start: start, end: end)
        active[key] = fetch
        counts["started", default: 0] += 1
        fetch.start(request)
        return true
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader,
                        didCancel loadingRequest: AVAssetResourceLoadingRequest) {
        let key = ObjectIdentifier(loadingRequest)
        if let fetch = active.removeValue(forKey: key) {
            counts["cancelled", default: 0] += 1
            fetch.cancel()
        }
    }

    func contains(_ key: ObjectIdentifier) -> Bool { return active[key] != nil }
    func increment(_ name: String) { counts[name, default: 0] += 1 }

    func finish(_ key: ObjectIdentifier, error: NSError?) {
        guard let fetch = active.removeValue(forKey: key) else { return }
        if let error = error {
            counts["failed", default: 0] += 1
            fetch.loading.finishLoading(with: error)
        } else {
            counts["completed", default: 0] += 1
            fetch.loading.finishLoading()
        }
        fetch.close()
    }

    func statistics() -> [String: Int] {
        return queue.sync {
            var result = counts
            result["active"] = active.count
            return result
        }
    }

    func close() {
        queue.sync {
            for fetch in active.values { fetch.cancel() }
            active.removeAll()
        }
    }
}

private final class Fetch: NSObject, URLSessionDataDelegate {
    weak var owner: ResourceLoader?
    let key: ObjectIdentifier
    let loading: AVAssetResourceLoadingRequest
    let startOffset: Int64
    let endOffset: Int64
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var expected: Int64 = 0
    private var received: Int64 = 0
    private var redirects: Int = 0
    private var accepted: Bool = false

    init(owner: ResourceLoader, key: ObjectIdentifier, loading: AVAssetResourceLoadingRequest,
         start: Int64, end: Int64) {
        self.owner = owner
        self.key = key
        self.loading = loading
        self.startOffset = start
        self.endOffset = end
    }

    func start(_ request: URLRequest) {
        guard let owner = owner else { return }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 10
        let delegateQueue = OperationQueue()
        delegateQueue.maxConcurrentOperationCount = 1
        delegateQueue.underlyingQueue = owner.queue
        let newSession = URLSession(configuration: configuration, delegate: self, delegateQueue: delegateQueue)
        session = newSession
        task = newSession.dataTask(with: request)
        task?.resume()
    }

    func cancel() { task?.cancel(); session?.invalidateAndCancel() }
    func close() { session?.finishTasksAndInvalidate() }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        guard let owner = owner, owner.contains(key), let target = request.url,
              target.scheme == owner.origin.scheme, target.host == owner.origin.host,
              target.port == owner.origin.port, target.user == nil, target.password == nil,
              redirects < 3 else {
            owner?.increment("redirect_rejected")
            completionHandler(nil)
            return
        }
        redirects += 1
        var authorized = request
        authorized.setValue("Bearer " + owner.token, forHTTPHeaderField: "Authorization")
        authorized.setValue("bytes=\(startOffset)-\(endOffset)", forHTTPHeaderField: "Range")
        completionHandler(authorized)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let owner = owner, owner.contains(key), let http = response as? HTTPURLResponse,
              http.statusCode == 206, let raw = http.value(forHTTPHeaderField: "Content-Range"),
              raw.hasPrefix("bytes ") else {
            completionHandler(.cancel)
            owner?.finish(key, error: failure(3))
            return
        }
        let pieces = raw.dropFirst(6).split(separator: "/")
        let bounds = pieces.first?.split(separator: "-") ?? []
        guard pieces.count == 2, bounds.count == 2, let begin = Int64(bounds[0]),
              let end = Int64(bounds[1]), let total = Int64(pieces[1]),
              begin == startOffset, end >= begin, end <= endOffset, end < total,
              total > 0, total <= maximumBytes, http.expectedContentLength == end - begin + 1,
              http.value(forHTTPHeaderField: "Content-Encoding") == nil else {
            completionHandler(.cancel)
            owner.finish(key, error: failure(4))
            return
        }
        expected = end - begin + 1
        accepted = true
        if let info = loading.contentInformationRequest {
            info.contentLength = total
            info.isByteRangeAccessSupported = true
            info.contentType = UTType(mimeType: http.mimeType ?? "application/octet-stream")?.identifier
        }
        owner.increment("range_validated")
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let owner = owner, owner.contains(key), accepted else { return }
        received += Int64(data.count)
        if received > expected {
            cancel()
            owner.finish(key, error: failure(5))
            return
        }
        loading.dataRequest?.respond(with: data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let owner = owner, owner.contains(key) else { return }
        owner.finish(key, error: error == nil && accepted && received == expected ? nil : failure(6))
    }
}

private final class Probe {
    let input: Input
    let player = AVPlayer()
    let started = ProcessInfo.processInfo.systemUptime
    var stage: String = "input"
    var loaders: [ResourceLoader] = []
    var seeks: [[String: Double]] = []
    var startMs: Double = 0
    var duration: Double = 0

    init(input: Input) { self.input = input }

    func wait(_ condition: () -> Bool, seconds: Double = 5) throws {
        let deadline = min(started + 25, ProcessInfo.processInfo.systemUptime + seconds)
        while !condition() {
            if ProcessInfo.processInfo.systemUptime >= deadline { throw ProbeError(code: "TIMEOUT") }
            if player.currentItem?.status == .failed { throw ProbeError(code: "ITEM_FAILED") }
            RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.02))
        }
    }

    func settle(_ seconds: Double) throws {
        let deadline = ProcessInfo.processInfo.systemUptime + seconds
        try wait({ ProcessInfo.processInfo.systemUptime >= deadline }, seconds: seconds + 1)
    }

    func replace() throws {
        guard let source = URL(string: input.source) else { throw ProbeError(code: "INVALID_SOURCE") }
        let asset: AVURLAsset
        if input.kind == "local", source.isFileURL {
            asset = AVURLAsset(url: source)
        } else if input.kind == "http", source.scheme == "http", source.host == "127.0.0.1",
                  source.port != nil, source.user == nil, source.password == nil,
                  source.query == nil, let token = input.token, !token.isEmpty {
            let loader = ResourceLoader(origin: source, token: token)
            loaders.append(loader)
            asset = AVURLAsset(url: URL(string: "autplay-spike://fixture/audio")!)
            asset.resourceLoader.setDelegate(loader, queue: loader.queue)
        } else { throw ProbeError(code: "INVALID_SOURCE") }
        player.replaceCurrentItem(with: AVPlayerItem(asset: asset))
    }

    func seek(_ target: Double) throws {
        var finished = false
        var succeeded = false
        player.seek(to: CMTime(seconds: target, preferredTimescale: 1000),
                    toleranceBefore: .zero, toleranceAfter: .zero) { result in
            DispatchQueue.main.async { succeeded = result; finished = true }
        }
        try wait({ finished })
        let actual = player.currentTime().seconds
        guard actual.isFinite else { throw ProbeError(code: "SEEK_INVALID_TIME") }
        seeks.append(["target_ms": target * 1000, "actual_ms": actual * 1000,
                      "delta_ms": abs(actual - target) * 1000])
        guard succeeded, abs(actual - target) <= 0.25 else {
            throw ProbeError(code: "SEEK_OUTSIDE_250MS")
        }
    }

    func run() throws {
        stage = "open"
        try replace()
        if input.action == "cancel" {
            stage = "cancel"
            player.play()
            try wait({ (loaders.last?.statistics()["started", default: 0] ?? 0) > 0 })
            player.pause()
            player.replaceCurrentItem(with: nil)
            try wait({ (loaders.last?.statistics()["cancelled", default: 0] ?? 0) > 0 }, seconds: 2)
            guard loaders.last?.statistics()["active"] == 0 else { throw ProbeError(code: "CANCEL_ACTIVE_REQUEST") }
            return
        }
        try wait({ player.currentItem?.status == .readyToPlay })
        let loadedDuration = player.currentItem?.duration.seconds ?? 0
        guard loadedDuration.isFinite, loadedDuration >= 3 else { throw ProbeError(code: "DURATION_TOO_SHORT") }
        duration = loadedDuration
        stage = "play"
        let playStarted = ProcessInfo.processInfo.systemUptime
        player.play()
        try wait({ player.currentTime().seconds > 0.25 && player.timeControlStatus == .playing })
        startMs = (ProcessInfo.processInfo.systemUptime - playStarted) * 1000
        stage = "pause"
        player.pause()
        try settle(0.1)
        let paused = player.currentTime().seconds
        try settle(0.35)
        guard abs(player.currentTime().seconds - paused) <= 0.1 else { throw ProbeError(code: "PAUSE_DRIFT") }
        stage = "seek"
        for target in [0.0, duration / 2, max(0, duration - 0.5)] { try seek(target) }
        // Exercise replacing an item, then returning to it (duplicate source, distinct item).
        // Shared queue next/previous policy is deliberately outside this CLI.
        for name in ["replace_next", "replace_previous"] {
            stage = name
            try replace()
            try wait({ player.currentItem?.status == .readyToPlay })
            player.play()
            try wait({ player.currentTime().seconds > 0.25 })
            player.pause()
        }
        stage = "teardown"
    }

    func output(status: String, code: String) -> [String: Any] {
        player.pause()
        player.replaceCurrentItem(with: nil)
        var statistics: [String: Int] = [:]
        for loader in loaders {
            for (key, value) in loader.statistics() { statistics[key, default: 0] += value }
            loader.close()
        }
        return ["status": status, "code": code, "stage": stage,
                "elapsed_ms": (ProcessInfo.processInfo.systemUptime - started) * 1000,
                "start_ms": startMs, "duration_ms": duration * 1000, "seeks": seeks,
                "loader": statistics, "audible_output": "not_verified"]
    }
}

private func emit(_ result: [String: Any], exitCode: Int32) -> Never {
    if let data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
    }
    exit(exitCode)
}

guard CommandLine.arguments.count == 1 else {
    emit(["status": "failed", "code": "STDIN_ONLY"], exitCode: 2)
}
let bytes = FileHandle.standardInput.readData(ofLength: 65537)
guard bytes.count <= 65536, let input = try? JSONDecoder().decode(Input.self, from: bytes),
      ["playback", "cancel"].contains(input.action) else {
    emit(["status": "failed", "code": "INVALID_INPUT"], exitCode: 2)
}
let probe = Probe(input: input)
do {
    try probe.run()
    emit(probe.output(status: "passed", code: "OK"), exitCode: 0)
} catch let error as ProbeError {
    emit(probe.output(status: "failed", code: error.code), exitCode: 1)
} catch {
    emit(probe.output(status: "failed", code: "NATIVE_FAILURE"), exitCode: 1)
}
