// JVM-owned system-media ABI 1. Independent of AutPlayAudioHelper and local-audio IPC 1.
#import <AppKit/AppKit.h>
#import <MediaPlayer/MediaPlayer.h>
#include <dispatch/dispatch.h>
#include <jni.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <mutex>

static constexpr jint kAbi = 1;
static constexpr jlong kMaxPosition = 604800000;
static constexpr jsize kMaxPng = 1024 * 1024;

@interface APSystemMediaFrame : NSObject
@property(nonatomic) jlong token;
@property(nonatomic) jlong position;
@property(nonatomic) jlong duration;
@property(nonatomic) jint commands;
@property(nonatomic) BOOL playing;
@property(nonatomic, copy) NSString *title;
@property(nonatomic, copy) NSString *artist;
@property(nonatomic, strong) NSData *png;
@property(nonatomic) jint width;
@property(nonatomic) jint height;
@end
@implementation APSystemMediaFrame
@end

namespace {
struct Owner {
    jlong lease = 0;
    std::atomic<bool> active{true};
    std::atomic<jlong> token{0};
    // gMutex protects JNI references, the bounded mailbox and accepted command semantics.
    jobject listener = nullptr;
    jmethodID callback = nullptr;
    jlong highWater = 0;
    jint mask = 0;
    jint toggle = 0;
    bool scheduled = false;
    bool cleanupQueued = false;
    APSystemMediaFrame *__strong pending = nil;
    // The following fields belong exclusively to the AppKit main queue.
    jlong installedToken = 0;
    NSMutableArray<NSArray *> *__strong handlers = nil;
    NSData *__strong artworkBytes = nil;
    MPMediaItemArtwork *__strong artwork = nil;
};
std::mutex gMutex;
std::shared_ptr<Owner> gOwner;
JavaVM *gVm = nullptr;
jlong gNextLease = 0;

// Never share JNIEnv between threads; do not detach an already attached JVM/AWT thread.
struct Environment {
    JNIEnv *env = nullptr;
    bool attached = false;
    Environment() {
        if (!gVm) return;
        const jint result = gVm->GetEnv(reinterpret_cast<void **>(&env), JNI_VERSION_1_8);
        if (result == JNI_EDETACHED) {
            attached = gVm->AttachCurrentThreadAsDaemon(reinterpret_cast<void **>(&env), nullptr) == JNI_OK;
            if (!attached) env = nullptr;
        } else if (result != JNI_OK) env = nullptr;
    }
    ~Environment() { if (attached) gVm->DetachCurrentThread(); }
};

static bool clearException(JNIEnv *env) {
    if (!env->ExceptionCheck()) return false;
    // Do not print Java/native exception descriptions or personal metadata.
    env->ExceptionClear();
    return true;
}

static NSString *readText(JNIEnv *env, jstring text, bool blankAllowed) {
    if (!text) return nil;
    const jsize length = env->GetStringLength(text);
    if (length > 4096 || (!blankAllowed && length == 0)) return nil;
    const jchar *units = env->GetStringChars(text, nullptr);
    if (!units) return nil;
    bool valid = true;
    for (jsize i = 0; i < length; ++i) {
        const jchar unit = units[i];
        if (unit == 0 || (unit >= 0xDC00 && unit <= 0xDFFF)) { valid = false; break; }
        if (unit >= 0xD800 && unit <= 0xDBFF) {
            if (++i >= length || units[i] < 0xDC00 || units[i] > 0xDFFF) { valid = false; break; }
        }
    }
    NSString *result = valid ? [[NSString alloc] initWithCharacters:units length:length] : nil;
    env->ReleaseStringChars(text, units);
    return result;
}

static uint32_t bigEndian(const uint8_t *bytes) {
    return (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) |
        (uint32_t(bytes[2]) << 8) | uint32_t(bytes[3]);
}

// Only the existing cache's single-image 8-bit RGBA PNG shape is accepted. No linked images,
// text/profile chunks, animation, deferred file loading or unbounded original artwork decoding.
static bool canonicalPng(NSData *png, jint width, jint height) {
    if (!png) return width == 0 && height == 0;
    if (width < 1 || width > 512 || height < 1 || height > 512 || png.length < 57 || png.length > kMaxPng) return false;
    const uint8_t *bytes = static_cast<const uint8_t *>(png.bytes);
    const uint8_t signature[] = {137, 80, 78, 71, 13, 10, 26, 10};
    if (memcmp(bytes, signature, 8) != 0) return false;
    size_t offset = 8;
    bool header = false, data = false;
    for (int count = 0; count < 1024 && offset + 12 <= png.length; ++count) {
        const uint32_t length = bigEndian(bytes + offset);
        if (length > png.length - offset - 12) return false;
        const uint8_t *type = bytes + offset + 4;
        if (memcmp(type, "IHDR", 4) == 0) {
            if (header || offset != 8 || length != 13) return false;
            const uint8_t *body = bytes + offset + 8;
            if (bigEndian(body) != uint32_t(width) || bigEndian(body + 4) != uint32_t(height) ||
                body[8] != 8 || body[9] != 6 || body[10] != 0 || body[11] != 0 || body[12] != 0) return false;
            header = true;
        } else if (memcmp(type, "IDAT", 4) == 0) {
            if (!header) return false;
            data = true;
        } else if (memcmp(type, "IEND", 4) == 0) {
            return header && data && length == 0 && offset + 12 == png.length;
        } else return false;
        offset += size_t(length) + 12;
    }
    return false;
}

static void removeHandlers(const std::shared_ptr<Owner>& owner) {
    for (NSArray *pair in owner->handlers) {
        MPRemoteCommand *command = pair[0];
        [command removeTarget:pair[1]];
        command.enabled = NO;
    }
    owner->handlers = nil;
    owner->installedToken = 0;
}

static void cleanupOnMain(const std::shared_ptr<Owner>& owner) {
    @autoreleasepool {
        @try {
            removeHandlers(owner);
            MPNowPlayingInfoCenter *center = [MPNowPlayingInfoCenter defaultCenter];
            center.playbackState = MPNowPlayingPlaybackStateStopped;
            center.nowPlayingInfo = nil;
            owner->artwork = nil;
            owner->artworkBytes = nil;
        } @catch (NSException *exception) {
            (void)exception;
            // Keep the lease pinned on uncertain cleanup. Never allow a replacement owner.
            return;
        }
        std::lock_guard<std::mutex> guard(gMutex);
        if (gOwner == owner) gOwner.reset();
    }
}

static void retire(JNIEnv *env, const std::shared_ptr<Owner>& owner) {
    bool queueCleanup = false;
    {
        std::lock_guard<std::mutex> guard(gMutex);
        owner->active.store(false);
        owner->token.store(0);
        owner->pending = nil;
        if (owner->listener) { env->DeleteGlobalRef(owner->listener); owner->listener = nullptr; }
        if (!owner->cleanupQueued) { owner->cleanupQueued = true; queueCleanup = true; }
    }
    if (queueCleanup) {
        // Capture a shared_ptr value, never the reference parameter's caller-owned storage.
        const auto retainedOwner = owner;
        dispatch_async(dispatch_get_main_queue(), ^{ cleanupOnMain(retainedOwner); });
    }
}

static MPRemoteCommandHandlerStatus deliver(const std::shared_ptr<Owner>& owner,
    jlong capturedToken, jint command, jlong position) {
    if (!owner->active.load() || owner->token.load() != capturedToken) return MPRemoteCommandHandlerStatusCommandFailed;
    Environment connection;
    JNIEnv *env = connection.env;
    if (!env) return MPRemoteCommandHandlerStatusCommandFailed;
    jobject local = nullptr;
    {
        std::lock_guard<std::mutex> guard(gMutex);
        if (owner->active.load() && owner->token.load() == capturedToken && owner->listener)
            local = env->NewLocalRef(owner->listener);
    }
    if (clearException(env) || !local) return MPRemoteCommandHandlerStatusCommandFailed;
    // No native mutex across Java. A local reference survives concurrent DeleteGlobalRef.
    const jboolean accepted = env->CallBooleanMethod(local, owner->callback, command, capturedToken, position);
    const bool failed = clearException(env);
    env->DeleteLocalRef(local);
    return !failed && accepted ? MPRemoteCommandHandlerStatusSuccess : MPRemoteCommandHandlerStatusCommandFailed;
}

static void addHandler(const std::shared_ptr<Owner>& owner, MPRemoteCommand *command,
    jlong capturedToken, jint action, bool enabled) {
    command.enabled = enabled;
    if (!enabled) return;
    const std::weak_ptr<Owner> weak = owner;
    id target = [command addTargetWithHandler:^MPRemoteCommandHandlerStatus(MPRemoteCommandEvent *event) {
        @autoreleasepool {
            const auto capturedOwner = weak.lock();
            if (!capturedOwner) return MPRemoteCommandHandlerStatusCommandFailed;
            jlong position = -1;
            if (action == 5) {
                if (![event isKindOfClass:[MPChangePlaybackPositionCommandEvent class]]) return MPRemoteCommandHandlerStatusCommandFailed;
                const double seconds = ((MPChangePlaybackPositionCommandEvent *)event).positionTime;
                if (!std::isfinite(seconds) || seconds < 0 || seconds > double(kMaxPosition) / 1000.0)
                    return MPRemoteCommandHandlerStatusCommandFailed;
                position = static_cast<jlong>(std::llround(seconds * 1000.0));
            }
            // Token/action are captured by this registration, never looked up from a newer item.
            return deliver(capturedOwner, capturedToken, action, position);
        }
    }];
    if (!target) @throw [NSException exceptionWithName:@"APSystemMediaRegistration" reason:nil userInfo:nil];
    [owner->handlers addObject:@[command, target]];
}

static void applyOnMain(const std::shared_ptr<Owner>& owner, APSystemMediaFrame *frame) {
    if (!owner->active.load()) return;
    if (!frame) {
        removeHandlers(owner);
        [MPNowPlayingInfoCenter defaultCenter].nowPlayingInfo = nil;
        [MPNowPlayingInfoCenter defaultCenter].playbackState = MPNowPlayingPlaybackStateStopped;
        owner->artwork = nil;
        owner->artworkBytes = nil;
        return;
    }
    if (owner->token.load() != frame.token) return;
    if (owner->installedToken != frame.token) {
        removeHandlers(owner);
        owner->handlers = [NSMutableArray arrayWithCapacity:6];
        MPRemoteCommandCenter *commands = [MPRemoteCommandCenter sharedCommandCenter];
        addHandler(owner, commands.playCommand, frame.token, 1, (frame.commands & 1) != 0);
        addHandler(owner, commands.pauseCommand, frame.token, 2, (frame.commands & 2) != 0);
        addHandler(owner, commands.previousTrackCommand, frame.token, 3, (frame.commands & 4) != 0);
        addHandler(owner, commands.nextTrackCommand, frame.token, 4, (frame.commands & 8) != 0);
        addHandler(owner, commands.changePlaybackPositionCommand, frame.token, 5, (frame.commands & 16) != 0);
        const jint toggle = frame.playing ? 2 : 1;
        addHandler(owner, commands.togglePlayPauseCommand, frame.token, toggle, (frame.commands & toggle) != 0);
        owner->installedToken = frame.token;
    }
    if (![owner->artworkBytes isEqualToData:frame.png]) {
        owner->artwork = nil;
        owner->artworkBytes = frame.png;
        if (frame.png) {
            NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc] initWithData:frame.png];
            if (bitmap && bitmap.pixelsWide == frame.width && bitmap.pixelsHigh == frame.height) {
                NSImage *image = [[NSImage alloc] initWithSize:NSMakeSize(frame.width, frame.height)];
                [image addRepresentation:bitmap];
                owner->artwork = [[MPMediaItemArtwork alloc] initWithBoundsSize:image.size requestHandler:^NSImage *(CGSize size) {
                    NSImage *copy = [image copy];
                    const double width = std::isfinite(size.width) ? size.width : frame.width;
                    const double height = std::isfinite(size.height) ? size.height : frame.height;
                    copy.size = NSMakeSize(std::clamp(width, 1.0, double(frame.width)),
                        std::clamp(height, 1.0, double(frame.height)));
                    return copy;
                }];
            }
        }
    }
    NSMutableDictionary<NSString *, id> *info = [NSMutableDictionary dictionaryWithCapacity:7];
    info[MPMediaItemPropertyTitle] = frame.title;
    info[MPMediaItemPropertyArtist] = frame.artist;
    info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = @(double(frame.position) / 1000.0);
    if (frame.duration >= 0) info[MPMediaItemPropertyPlaybackDuration] = @(double(frame.duration) / 1000.0);
    info[MPNowPlayingInfoPropertyPlaybackRate] = @(frame.playing ? 1.0 : 0.0);
    if (owner->artwork) info[MPMediaItemPropertyArtwork] = owner->artwork;
#if __MAC_OS_X_VERSION_MAX_ALLOWED >= 150000
    if (@available(macOS 15.0, *)) info[MPNowPlayingInfoPropertyExcludeFromSuggestions] = @YES;
#endif
    MPNowPlayingInfoCenter *center = [MPNowPlayingInfoCenter defaultCenter];
    center.nowPlayingInfo = info;
    center.playbackState = frame.playing ? MPNowPlayingPlaybackStatePlaying : MPNowPlayingPlaybackStatePaused;
}

static void drainOnMain(const std::shared_ptr<Owner>& owner) {
    @autoreleasepool {
        APSystemMediaFrame *frame = nil;
        {
            std::lock_guard<std::mutex> guard(gMutex);
            owner->scheduled = false;
            frame = owner->pending;
            owner->pending = nil;
        }
        if (!owner->active.load()) return;
        @try { applyOnMain(owner, frame); }
        @catch (NSException *exception) {
            (void)exception;
            Environment connection;
            if (connection.env) retire(connection.env, owner);
            else { owner->active.store(false); owner->token.store(0); }
        }
    }
}

static std::shared_ptr<Owner> findOwner(jlong lease) {
    std::lock_guard<std::mutex> guard(gMutex);
    return gOwner && gOwner->lease == lease ? gOwner : nullptr;
}
} // namespace

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *) {
    gVm = vm;
    return JNI_VERSION_1_8;
}

extern "C" JNIEXPORT jint JNICALL Java_app_autplay_desktop_systemmedia_MacSystemMediaJni_abiVersion(JNIEnv *, jobject) {
    return kAbi;
}

extern "C" JNIEXPORT jlong JNICALL Java_app_autplay_desktop_systemmedia_MacSystemMediaJni_acquire(
    JNIEnv *env, jobject, jint expectedAbi, jobject listener) {
    @autoreleasepool {
        if (expectedAbi != kAbi || !listener || !NSApp) return 0;
        if (@available(macOS 13.0, *)) {} else return 0;
        jclass type = env->GetObjectClass(listener);
        if (!type) return 0;
        jmethodID callback = env->GetMethodID(type, "onCommand", "(IJJ)Z");
        env->DeleteLocalRef(type);
        if (clearException(env) || !callback) return 0;
        std::lock_guard<std::mutex> guard(gMutex);
        if (gOwner || gNextLease == std::numeric_limits<jlong>::max()) return 0;
        jobject reference = env->NewGlobalRef(listener);
        if (!reference) return 0;
        auto owner = std::make_shared<Owner>();
        owner->lease = ++gNextLease;
        owner->listener = reference;
        owner->callback = callback;
        gOwner = owner;
        return owner->lease;
    }
}

extern "C" JNIEXPORT jboolean JNICALL Java_app_autplay_desktop_systemmedia_MacSystemMediaJni_publish(
    JNIEnv *env, jobject, jlong lease, jlong token, jstring title, jstring artist,
    jlong position, jlong duration, jboolean playing, jint commands, jbyteArray png, jint width, jint height) {
    @autoreleasepool {
        const auto owner = findOwner(lease);
        if (!owner || !owner->active.load() || token <= 0 || position < 0 || position > kMaxPosition ||
            duration < -1 || duration > kMaxPosition || (duration >= 0 && position > duration) ||
            commands < 0 || (commands & 31) != commands || ((commands & 16) && duration <= 0)) return JNI_FALSE;
        APSystemMediaFrame *frame = [APSystemMediaFrame new];
        frame.title = readText(env, title, false);
        if (env->ExceptionCheck()) return JNI_FALSE;
        frame.artist = readText(env, artist, true);
        if (env->ExceptionCheck() || !frame.title || !frame.artist) return JNI_FALSE;
        if (png) {
            const jsize length = env->GetArrayLength(png);
            if (length < 1 || length > kMaxPng) return JNI_FALSE;
            NSMutableData *data = [NSMutableData dataWithLength:length];
            env->GetByteArrayRegion(png, 0, length, static_cast<jbyte *>(data.mutableBytes));
            if (env->ExceptionCheck()) return JNI_FALSE;
            frame.png = data;
        }
        if (!canonicalPng(frame.png, width, height)) return JNI_FALSE;
        frame.token = token; frame.position = position; frame.duration = duration;
        frame.playing = playing; frame.commands = commands; frame.width = width; frame.height = height;
        bool schedule = false;
        {
            std::lock_guard<std::mutex> guard(gMutex);
            if (!owner->active.load()) return JNI_FALSE;
            const jint toggle = (commands & (playing ? 2 : 1)) ? (playing ? 2 : 1) : 0;
            if (token == owner->token.load()) {
                if (commands != owner->mask || toggle != owner->toggle) return JNI_FALSE;
            } else if (token <= owner->highWater) return JNI_FALSE;
            owner->highWater = token;
            owner->mask = commands; owner->toggle = toggle;
            owner->token.store(token);
            owner->pending = frame;
            if (!owner->scheduled) { owner->scheduled = true; schedule = true; }
        }
        if (schedule) dispatch_async(dispatch_get_main_queue(), ^{ drainOnMain(owner); });
        return JNI_TRUE;
    }
}

extern "C" JNIEXPORT jboolean JNICALL Java_app_autplay_desktop_systemmedia_MacSystemMediaJni_clear(
    JNIEnv *, jobject, jlong lease) {
    const auto owner = findOwner(lease);
    if (!owner) return JNI_FALSE;
    bool schedule = false;
    {
        std::lock_guard<std::mutex> guard(gMutex);
        if (!owner->active.load()) return JNI_FALSE;
        owner->token.store(0);
        owner->pending = nil;
        if (!owner->scheduled) { owner->scheduled = true; schedule = true; }
    }
    if (schedule) dispatch_async(dispatch_get_main_queue(), ^{ drainOnMain(owner); });
    return JNI_TRUE;
}

extern "C" JNIEXPORT void JNICALL Java_app_autplay_desktop_systemmedia_MacSystemMediaJni_retire(
    JNIEnv *env, jobject, jlong lease) {
    const auto owner = findOwner(lease);
    if (owner) retire(env, owner);
}

extern "C" JNIEXPORT jboolean JNICALL Java_app_autplay_desktop_systemmedia_MacSystemMediaJni_isReleased(
    JNIEnv *, jobject, jlong lease) {
    return findOwner(lease) ? JNI_FALSE : JNI_TRUE;
}
