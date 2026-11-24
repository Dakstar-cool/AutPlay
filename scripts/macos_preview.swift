// Capture only the application launched with this fresh preview profile.
import Foundation
import CoreGraphics

let arguments = CommandLine.arguments
guard arguments.count == 3 else { exit(2) }
let profile = FileManager.default.temporaryDirectory.appendingPathComponent("AutPlay-preview-" + UUID().uuidString)
try FileManager.default.createDirectory(at: profile, withIntermediateDirectories: true)
let app = Process()
app.executableURL = URL(fileURLWithPath: arguments[1])
app.environment = ProcessInfo.processInfo.environment.merging([
    "JAVA_TOOL_OPTIONS": "-Duser.home=\(profile.path) -Duser.language=ru -Duser.country=RU",
]) { _, new in new }
try app.run()

func capture() -> Bool {
    let deadline = Date().addingTimeInterval(45)
    while app.isRunning && Date() < deadline {
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        let window = windows.first { row in
            guard (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == app.processIdentifier,
                  (row[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
                  let bounds = row[kCGWindowBounds as String] as? [String: NSNumber] else { return false }
            return (bounds["Width"]?.intValue ?? 0) >= 600 && (bounds["Height"]?.intValue ?? 0) >= 400
        }
        if let id = window?[kCGWindowNumber as String] as? NSNumber {
            Thread.sleep(forTimeInterval: 3)
            let screenshot = Process()
            screenshot.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            screenshot.arguments = ["-x", "-o", "-l", id.stringValue, arguments[2]]
            do { try screenshot.run(); screenshot.waitUntilExit() } catch { return false }
            return screenshot.terminationStatus == 0
        }
        Thread.sleep(forTimeInterval: 0.5)
    }
    return false
}

let captured = capture()
if app.isRunning { app.terminate() }
app.waitUntilExit()
try? FileManager.default.removeItem(at: profile)
exit(captured ? 0 : 1)
