// Public M1 code verification only. Link beside AutPlaySecureState.mm in its existing JNI library.
// No Keychain access, entitlements, signing, process launch or arbitrary code identifier API.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <jni.h>
#include <sys/codesign.h>
#include <sys/stat.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <fcntl.h>
#include <unistd.h>
#include <cstring>

namespace {
template<class T> struct OwnedCF {
    T value = nullptr;
    ~OwnedCF() { if (value) CFRelease(value); }
    T *out() { return &value; }
};
struct Rejected {};
void requireIdentity(bool value) { if (!value) throw Rejected{}; }
NSString *text(JNIEnv *env, jstring input, jsize maximum) {
    requireIdentity(input != nullptr);
    jsize length = env->GetStringLength(input);
    requireIdentity(length > 0 && length <= maximum);
    const jchar *chars = env->GetStringChars(input, nullptr);
    requireIdentity(chars != nullptr);
    NSString *value = [[NSString alloc] initWithCharacters:reinterpret_cast<const unichar *>(chars) length:length];
    env->ReleaseStringChars(input, chars);
    requireIdentity(value != nil && [value rangeOfCharacterFromSet:[NSCharacterSet controlCharacterSet]].location == NSNotFound);
    return value;
}
NSURL *exactFile(NSString *path) {
    requireIdentity([path hasPrefix:@"/"]);
    NSURL *url = [NSURL fileURLWithPath:path];
    requireIdentity([url.URLByStandardizingPath.URLByResolvingSymlinksInPath.path isEqualToString:path]);
    return url;
}
SecRequirementRef requirement(NSString *identifier, NSString *team) {
    requireIdentity(team.length == 10 && [team rangeOfCharacterFromSet:
        [[NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"] invertedSet]].location == NSNotFound);
    NSString *expression = [NSString stringWithFormat:@"anchor apple generic and identifier \"%@\" and certificate leaf[subject.OU] = \"%@\" and certificate leaf[field.1.2.840.113635.100.6.1.13] exists", identifier, team];
    SecRequirementRef value = nullptr;
    requireIdentity(SecRequirementCreateWithString((__bridge CFStringRef)expression, kSecCSDefaultFlags, &value) == errSecSuccess);
    return value;
}
NSData *signedInfo(SecStaticCodeRef code, NSString *identifier, NSString *team, bool helper) {
    OwnedCF<CFDictionaryRef> raw;
    requireIdentity(SecCodeCopySigningInformation(code, kSecCSSigningInformation, raw.out()) == errSecSuccess);
    NSDictionary *info = (__bridge NSDictionary *)raw.value;
    requireIdentity([info[(__bridge id)kSecCodeInfoIdentifier] isEqual:identifier] &&
        [info[(__bridge id)kSecCodeInfoTeamIdentifier] isEqual:team] &&
        ([info[(__bridge id)kSecCodeInfoFlags] unsignedIntValue] & CS_RUNTIME) != 0);
    NSDictionary *entitlements = info[(__bridge id)kSecCodeInfoEntitlementsDict];
    if (entitlements) {
        requireIdentity([entitlements isKindOfClass:NSDictionary.class]);
        for (NSString *forbidden in @[@"get-task-allow", @"com.apple.security.get-task-allow",
            @"com.apple.security.cs.disable-library-validation", @"com.apple.security.cs.allow-dyld-environment-variables"])
            requireIdentity(![entitlements[forbidden] boolValue]);
        if (helper) requireIdentity(entitlements[@"keychain-access-groups"] == nil &&
            entitlements[@"com.apple.application-identifier"] == nil);
    }
    NSData *unique = info[(__bridge id)kSecCodeInfoUnique];
    requireIdentity([unique isKindOfClass:NSData.class] && unique.length >= 20 && unique.length <= 64);
    return unique;
}
NSData *staticWitness(NSString *bundle, NSString *executable, NSString *team) {
#if !defined(__arm64__)
    throw Rejected{};
#endif
    NSURL *bundleUrl = exactFile(bundle), *helperUrl = exactFile(executable);
    requireIdentity([executable isEqualToString:[bundle stringByAppendingPathComponent:@"Contents/app/resources/native/AutPlayRemoteMediaHelper"]]);
    int fd = open(executable.fileSystemRepresentation, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    requireIdentity(fd >= 0);
    struct stat before = {}, after = {};
    mach_header_64 header = {};
    bool executableValid = fstat(fd, &before) == 0 && S_ISREG(before.st_mode) && (before.st_mode & 0022) == 0 &&
        (before.st_mode & 0100) && pread(fd, &header, sizeof(header), 0) == sizeof(header) &&
        header.magic == MH_MAGIC_64 && header.cputype == CPU_TYPE_ARM64 && header.filetype == MH_EXECUTE;
    if (!executableValid) { close(fd); throw Rejected{}; }
    try { @try {
        OwnedCF<SecRequirementRef> appRequirement, helperRequirement;
        appRequirement.value = requirement(@"app.autplay.desktop", team);
        helperRequirement.value = requirement(@"app.autplay.desktop.remote-media.v1", team);
        OwnedCF<SecCodeRef> self;
        requireIdentity(SecCodeCopySelf(0, self.out()) == errSecSuccess &&
            SecCodeCheckValidity(self.value, kSecCSDefaultFlags, appRequirement.value) == errSecSuccess);
        OwnedCF<SecStaticCodeRef> app, helper;
        requireIdentity(SecStaticCodeCreateWithPath((__bridge CFURLRef)bundleUrl, 0, app.out()) == errSecSuccess &&
            SecStaticCodeCreateWithPath((__bridge CFURLRef)helperUrl, 0, helper.out()) == errSecSuccess);
        auto flags = kSecCSStrictValidate | kSecCSCheckAllArchitectures | kSecCSCheckNestedCode;
        requireIdentity(SecStaticCodeCheckValidity(app.value, flags, appRequirement.value) == errSecSuccess &&
            SecStaticCodeCheckValidity(helper.value, flags, helperRequirement.value) == errSecSuccess);
        NSData *appIdentity = signedInfo(app.value, @"app.autplay.desktop", team, false);
        NSData *helperIdentity = signedInfo(helper.value, @"app.autplay.desktop.remote-media.v1", team, true);
        requireIdentity(fstat(fd, &after) == 0 && before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
            before.st_size == after.st_size && before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec &&
            before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec);
        NSMutableData *witness = [NSMutableData data];
        uint8_t appLength = uint8_t(appIdentity.length), helperLength = uint8_t(helperIdentity.length);
        [witness appendBytes:&appLength length:1]; [witness appendData:appIdentity];
        [witness appendBytes:&helperLength length:1]; [witness appendData:helperIdentity];
        close(fd);
        return witness;
    } @catch (NSException *) { throw Rejected{}; }
    } catch (...) { close(fd); throw; }
}
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_app_autplay_desktop_media_MacRemoteMediaIdentityJni_staticWitness(JNIEnv *env, jobject, jstring bundle, jstring executable, jstring team) {
    @autoreleasepool {
        try {
            NSData *value = staticWitness(text(env, bundle, 4096), text(env, executable, 4096), text(env, team, 10));
            jbyteArray result = env->NewByteArray(jsize(value.length));
            if (result) env->SetByteArrayRegion(result, 0, jsize(value.length), reinterpret_cast<const jbyte *>(value.bytes));
            return result;
        } catch (...) { return nullptr; }
    }
}
extern "C" JNIEXPORT jboolean JNICALL
Java_app_autplay_desktop_media_MacRemoteMediaIdentityJni_runningMatches(JNIEnv *env, jobject, jstring bundle, jstring executable,
    jstring team, jlong pid, jbyteArray expected) {
    @autoreleasepool {
        try {
            requireIdentity(pid > 0 && pid <= INT32_MAX && expected != nullptr);
            NSString *bundlePath = text(env, bundle, 4096), *helperPath = text(env, executable, 4096), *developer = text(env, team, 10);
            NSData *fresh = staticWitness(bundlePath, helperPath, developer);
            jsize length = env->GetArrayLength(expected);
            requireIdentity(length == fresh.length && length <= 256);
            uint8_t original[256] = {};
            env->GetByteArrayRegion(expected, 0, length, reinterpret_cast<jbyte *>(original));
            requireIdentity(!env->ExceptionCheck() && memcmp(original, fresh.bytes, size_t(length)) == 0);
            NSDictionary *attributes = @{(__bridge id)kSecGuestAttributePid:@(pid)};
            OwnedCF<SecCodeRef> guest;
            OwnedCF<SecRequirementRef> required;
            required.value = requirement(@"app.autplay.desktop.remote-media.v1", developer);
            requireIdentity(SecCodeCopyGuestWithAttributes(nullptr, (__bridge CFDictionaryRef)attributes, 0, guest.out()) == errSecSuccess &&
                SecCodeCheckValidity(guest.value, kSecCSDefaultFlags, required.value) == errSecSuccess);
            OwnedCF<SecStaticCodeRef> code;
            requireIdentity(SecCodeCopyStaticCode(guest.value, 0, code.out()) == errSecSuccess);
            NSData *runningIdentity = signedInfo(code.value, @"app.autplay.desktop.remote-media.v1", developer, true);
            const uint8_t *bytes = reinterpret_cast<const uint8_t *>(fresh.bytes);
            size_t helperOffset = 1 + bytes[0];
            requireIdentity(helperOffset < fresh.length && runningIdentity.length == bytes[helperOffset] &&
                helperOffset + 1 + runningIdentity.length == fresh.length &&
                memcmp(runningIdentity.bytes, bytes + helperOffset + 1, runningIdentity.length) == 0);
            OwnedCF<CFURLRef> path;
            requireIdentity(SecCodeCopyPath(guest.value, 0, path.out()) == errSecSuccess &&
                [[(__bridge NSURL *)path.value path] isEqualToString:helperPath]);
            return JNI_TRUE;
        } catch (...) { return JNI_FALSE; }
    }
}
