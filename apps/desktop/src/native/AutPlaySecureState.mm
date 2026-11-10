// Secure-state JNI ABI1. Build with ARC, Foundation/Security and the pinned JDK JNI headers.
// Independent of audio IPC1, system-media ABI1 and portable ROOT signing.
// Request Darwin's non-elidable memset_s before Foundation imports string.h.
#define __STDC_WANT_LIB_EXT1__ 1
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <Security/CMSDecoder.h>
#include <jni.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <pwd.h>
#include <dlfcn.h>
#include <cstring>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include "AutPlayCommunityIdentity.h"

namespace {
constexpr size_t kValue = 1048576, kControl = 65536, kTotal = 16777216;
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
NSString *const kService = @"app.autplay.desktop.community-state.v1." AUTPLAY_COMMUNITY_SIGNER_SHA256;
NSString *const kKeyLabel = @"app.autplay.desktop.community-key.v1." AUTPLAY_COMMUNITY_SIGNER_SHA256;
#else
NSString *const kService = @"app.autplay.desktop.secure-state.v1";
NSString *const kKeyLabel = @"app.autplay.desktop.device-key.v1";
#endif
NSString *const kBundle = @"app.autplay.desktop";
struct Failure { const char *code; unsigned site = 0; };
void require(bool condition, const char *code = "CORRUPT", unsigned site = __builtin_LINE()) { if (!condition) throw Failure{code, site}; }
void auditFailure(const Failure &failure) {
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    // Fixed status plus source location only; never log paths, identities or item contents.
    fprintf(stderr, "AUTPLAY_SECURE_STATE_CHECK_FAILED site=%u status=%s\n", failure.site, failure.code);
#endif
}
void status(OSStatus code) {
    if (code == errSecSuccess) return;
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    // The owner checks actual Keychain lock state before each protected operation.
    if (code == errSecInteractionNotAllowed) throw Failure{"ACCESS_DENIED"};
#endif
    if (code == errSecInteractionNotAllowed || code == errSecNotAvailable) throw Failure{"LOCKED"};
    if (code == errSecMissingEntitlement) throw Failure{"IDENTITY_UNAVAILABLE"};
    if (code == errSecAuthFailed || code == errSecUserCanceled || code == errSecNoAccessForItem) throw Failure{"ACCESS_DENIED"};
    throw Failure{"UNAVAILABLE"};
}
template<class T> struct CF {
    T value = nullptr;
    CF() = default; explicit CF(T input):value(input) {}
    ~CF() { if (value) CFRelease(value); }
    CF(const CF&) = delete; CF& operator=(const CF&) = delete;
    T *out() { if (value) { CFRelease(value); value = nullptr; } return &value; }
};
struct Bytes {
    std::vector<uint8_t> value;
    ~Bytes() { if (!value.empty()) (void)memset_s(value.data(), value.size(), 0, value.size()); }
};
Bytes input(JNIEnv *env, jbyteArray array, size_t maximum, size_t minimum = 0) {
    require(array != nullptr);
    jsize count = env->GetArrayLength(array); require(count >= 0 && size_t(count) >= minimum && size_t(count) <= maximum);
    Bytes result; result.value.resize(count);
    if (count) env->GetByteArrayRegion(array, 0, count, reinterpret_cast<jbyte *>(result.value.data()));
    require(!env->ExceptionCheck(), "UNAVAILABLE"); return result;
}
jbyteArray output(JNIEnv *env, NSData *data, size_t maximum) {
    require(data && data.length <= maximum);
    auto result = env->NewByteArray(jsize(data.length)); require(result != nullptr, "UNAVAILABLE");
    if (data.length) env->SetByteArrayRegion(result, 0, jsize(data.length), reinterpret_cast<const jbyte *>(data.bytes));
    require(!env->ExceptionCheck(), "UNAVAILABLE"); return result;
}
NSString *string(JNIEnv *env, jstring value, size_t maximum) {
    require(value != nullptr);
    jsize count = env->GetStringLength(value); require(count > 0 && size_t(count) <= maximum);
    const jchar *chars = env->GetStringChars(value, nullptr); require(chars != nullptr, "UNAVAILABLE");
    NSString *result = [[NSString alloc] initWithCharacters:reinterpret_cast<const unichar *>(chars) length:count];
    env->ReleaseStringChars(value, chars); require(result != nil); return result;
}
bool uuid(NSString *text) { return text.length == 36 && [[[NSUUID alloc] initWithUUIDString:text].UUIDString.lowercaseString isEqualToString:text]; }
void keyTag(NSString *tag) {
    NSString *prefix = @"app.autplay.desktop.device-key.v1.";
    require([tag hasPrefix:prefix] && uuid([tag substringFromIndex:prefix.length]));
}
NSString *valueAccount(NSString *id) { require(uuid(id)); return [@"k1.value." stringByAppendingString:id]; }
NSData *data(const Bytes &bytes) { return [NSData dataWithBytes:bytes.value.data() length:bytes.value.size()]; }
NSDictionary *json(NSData *bytes, size_t maximum) {
    require(bytes.length >= 2 && bytes.length <= maximum);
    id result = [NSJSONSerialization JSONObjectWithData:bytes options:0 error:nullptr];
    require([result isKindOfClass:NSDictionary.class]); return result;
}
void throwJava(JNIEnv *env, const char *code) {
    if (env->ExceptionCheck()) env->ExceptionClear();
    char safe[80]; snprintf(safe, sizeof(safe), "SECURE_STATE_%s", code);
    jclass exception = env->FindClass("java/lang/IllegalStateException");
    if (exception) env->ThrowNew(exception, safe);
}

struct Owner {
    jlong handle;
    int lock = -1;
    NSString *__strong group;
    NSString *__strong team;
    NSString *__strong profile;
    NSDate *__strong profileExpiration;
    std::thread::id thread;
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    CF<SecKeychainRef> keychain;
#endif
    ~Owner() { if (lock >= 0) { flock(lock, LOCK_UN); ::close(lock); } }
};
std::mutex gMutex;
std::unique_ptr<Owner> gOwner;
jlong gNext = 1;
struct ValidatedIdentity { NSString *__strong group; NSDate *__strong expiration; };
ValidatedIdentity preflight(NSString *team, NSString *profileUuid);
Owner& owner(jlong handle, bool requireIdentity = true) {
    require(gOwner && gOwner->handle == handle && gOwner->thread == std::this_thread::get_id(), "CLOSED");
    if (requireIdentity) {
        // Full sealed-bundle/profile/team preflight is performed before acquiring the lease.
        // The running task identity is immutable; DP Keychain also checks its entitlements on
        // every operation. Do not re-hash all nested JVM code for every protected value read.
        CF<SecCodeRef> running;
        require(gOwner->profileExpiration && gOwner->profileExpiration.timeIntervalSinceNow > 0, "IDENTITY_UNAVAILABLE");
        require(SecCodeCopySelf(kSecCSDefaultFlags, running.out()) == errSecSuccess &&
            SecCodeCheckValidity(running.value, kSecCSDefaultFlags, nullptr) == errSecSuccess, "IDENTITY_UNAVAILABLE");
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
        SecKeychainStatus flags = 0;
        status(SecKeychainGetStatus(gOwner->keychain.value, &flags));
        require((flags & kSecUnlockStateStatus) != 0, "LOCKED");
#endif
    }
    return *gOwner;
}

NSURL *realURL(NSURL *url) {
    require(url.isFileURL, "IDENTITY_UNAVAILABLE");
    NSURL *resolved = url.URLByStandardizingPath.URLByResolvingSymlinksInPath;
    require([resolved.path isEqualToString:url.URLByStandardizingPath.path], "IDENTITY_UNAVAILABLE"); return resolved;
}
void validateStatic(NSURL *url, SecRequirementRef requirement) {
    CF<SecStaticCodeRef> code;
    status(SecStaticCodeCreateWithPath((__bridge CFURLRef)url, kSecCSDefaultFlags, code.out()));
    require(SecStaticCodeCheckValidity(code.value, kSecCSStrictValidate | kSecCSCheckAllArchitectures | kSecCSCheckNestedCode, requirement) == errSecSuccess, "IDENTITY_UNAVAILABLE");
}

/** Code validity plus exact sealed profile/certificate/entitlement matching. The OS remains
 * authoritative for profile authorization when DP Keychain is used; no fake entitlement grants.
 */
ValidatedIdentity preflight(NSString *team, NSString *profileUuid) {
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    require([team isEqual:autplay_community::pin()] && [profileUuid isEqual:@"community-keychain-1"], "IDENTITY_UNAVAILABLE");
    NSBundle *bundle = NSBundle.mainBundle;
    require([bundle.bundleIdentifier isEqual:kBundle] && [bundle.bundleURL.pathExtension isEqual:@"app"], "IDENTITY_UNAVAILABLE");
    NSURL *bundleURL = realURL(bundle.bundleURL), *executable = realURL(bundle.executableURL);
    require([executable.URLByDeletingLastPathComponent.path isEqual:[[bundleURL URLByAppendingPathComponent:@"Contents/MacOS"] path]], "IDENTITY_UNAVAILABLE");
    autplay_community::validate(bundleURL, kBundle, true);
    Dl_info libraryInfo = {};
    require(dladdr(reinterpret_cast<const void *>(&preflight), &libraryInfo) && libraryInfo.dli_fname, "IDENTITY_UNAVAILABLE");
    NSURL *library = realURL([NSURL fileURLWithPath:[NSString stringWithUTF8String:libraryInfo.dli_fname]]);
    require([library.path isEqual:[[bundleURL URLByAppendingPathComponent:@"Contents/app/resources/native/libAutPlaySecureState.dylib"] path]], "IDENTITY_UNAVAILABLE");
    autplay_community::validate(library, @"app.autplay.desktop.secure-state.v1");
    return {kService, NSDate.distantFuture};
#else
    require(team.length == 10 && [team rangeOfCharacterFromSet:[[NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"] invertedSet]].location == NSNotFound, "IDENTITY_UNAVAILABLE");
    require([[NSUUID alloc] initWithUUIDString:profileUuid] != nil, "IDENTITY_UNAVAILABLE");
    NSBundle *bundle = NSBundle.mainBundle;
    require([bundle.bundleIdentifier isEqualToString:kBundle] && [bundle.bundleURL.pathExtension isEqualToString:@"app"], "IDENTITY_UNAVAILABLE");
    NSURL *bundleURL = realURL(bundle.bundleURL);
    NSURL *executable = realURL(bundle.executableURL);
    require([executable.URLByDeletingLastPathComponent.path isEqualToString:[[bundleURL URLByAppendingPathComponent:@"Contents/MacOS"] path]], "IDENTITY_UNAVAILABLE");
    NSString *requirementText = [NSString stringWithFormat:@"anchor apple generic and identifier \"app.autplay.desktop\" and certificate leaf[subject.OU] = \"%@\" and certificate leaf[field.1.2.840.113635.100.6.1.13] exists", team];
    CF<SecRequirementRef> requirement;
    require(SecRequirementCreateWithString((__bridge CFStringRef)requirementText, kSecCSDefaultFlags, requirement.out()) == errSecSuccess, "IDENTITY_UNAVAILABLE");
    CF<SecCodeRef> running;
    require(SecCodeCopySelf(kSecCSDefaultFlags, running.out()) == errSecSuccess && SecCodeCheckValidity(running.value, kSecCSDefaultFlags, requirement.value) == errSecSuccess, "IDENTITY_UNAVAILABLE");
    validateStatic(bundleURL, requirement.value);
    CF<CFDictionaryRef> info;
    require(SecCodeCopySigningInformation(running.value, kSecCSSigningInformation, info.out()) == errSecSuccess, "IDENTITY_UNAVAILABLE");
    NSDictionary *signing = (__bridge NSDictionary *)info.value;
    require(([signing[(__bridge id)kSecCodeInfoFlags] unsignedIntValue] & kSecCodeSignatureRuntime) != 0, "IDENTITY_UNAVAILABLE");
    require([signing[(__bridge id)kSecCodeInfoIdentifier] isEqual:kBundle] && [signing[(__bridge id)kSecCodeInfoTeamIdentifier] isEqual:team], "IDENTITY_UNAVAILABLE");
    NSDictionary *entitlements = signing[(__bridge id)kSecCodeInfoEntitlementsDict];
    NSString *group = [NSString stringWithFormat:@"%@.%@", team, kBundle];
    require([entitlements isKindOfClass:NSDictionary.class] && [entitlements[@"com.apple.application-identifier"] isEqual:group] &&
        [entitlements[@"com.apple.developer.team-identifier"] isEqual:team] && [entitlements[@"keychain-access-groups"] isEqual:@[group]], "IDENTITY_UNAVAILABLE");
    for (NSString *forbidden in @[@"com.apple.security.get-task-allow", @"get-task-allow", @"com.apple.security.cs.disable-library-validation", @"com.apple.security.cs.allow-dyld-environment-variables"]) require(![entitlements[forbidden] boolValue], "IDENTITY_UNAVAILABLE");
    Dl_info libraryInfo = {};
    require(dladdr(reinterpret_cast<const void *>(&preflight), &libraryInfo) != 0 && libraryInfo.dli_fname, "IDENTITY_UNAVAILABLE");
    NSURL *library = realURL([NSURL fileURLWithPath:[NSString stringWithUTF8String:libraryInfo.dli_fname]]);
    NSURL *expected = [bundleURL URLByAppendingPathComponent:@"Contents/app/resources/native/libAutPlaySecureState.dylib"];
    require([library.path isEqualToString:expected.path], "IDENTITY_UNAVAILABLE");
    // Dylib identifier is separately fixed; same Developer ID team, valid sealed code.
    NSString *libraryRequirementText = [NSString stringWithFormat:@"anchor apple generic and identifier \"app.autplay.desktop.secure-state.v1\" and certificate leaf[subject.OU] = \"%@\" and certificate leaf[field.1.2.840.113635.100.6.1.13] exists", team];
    CF<SecRequirementRef> libraryRequirement;
    require(SecRequirementCreateWithString((__bridge CFStringRef)libraryRequirementText, 0, libraryRequirement.out()) == errSecSuccess, "IDENTITY_UNAVAILABLE");
    validateStatic(library, libraryRequirement.value);
    NSURL *profileURL = realURL([bundleURL URLByAppendingPathComponent:@"Contents/embedded.provisionprofile"]);
    NSNumber *profileSize = nil; require([profileURL getResourceValue:&profileSize forKey:NSURLFileSizeKey error:nullptr] && profileSize.unsignedLongLongValue <= kValue, "IDENTITY_UNAVAILABLE");
    NSData *encoded = [NSData dataWithContentsOfURL:profileURL options:0 error:nullptr]; require(encoded != nil, "IDENTITY_UNAVAILABLE");
    CF<CMSDecoderRef> decoder;
    require(CMSDecoderCreate(decoder.out()) == errSecSuccess && CMSDecoderUpdateMessage(decoder.value, encoded.bytes, encoded.length) == errSecSuccess && CMSDecoderFinalizeMessage(decoder.value) == errSecSuccess, "IDENTITY_UNAVAILABLE");
    size_t signers = 0; require(CMSDecoderGetNumSigners(decoder.value, &signers) == errSecSuccess && signers == 1, "IDENTITY_UNAVAILABLE");
    CF<SecPolicyRef> policy(SecPolicyCreateBasicX509()); CMSSignerStatus signer = kCMSSignerUnsigned; OSStatus verify = errSecSuccess;
    CF<SecTrustRef> trust;
    require(CMSDecoderCopySignerStatus(decoder.value, 0, policy.value, false, &signer, trust.out(), &verify) == errSecSuccess && signer == kCMSSignerValid && trust.value, "IDENTITY_UNAVAILABLE");
    require(SecTrustSetNetworkFetchAllowed(trust.value, false) == errSecSuccess && SecTrustEvaluateWithError(trust.value, nullptr), "IDENTITY_UNAVAILABLE");
    CF<CFDataRef> content;
    require(CMSDecoderCopyContent(decoder.value, content.out()) == errSecSuccess, "IDENTITY_UNAVAILABLE");
    id profile = [NSPropertyListSerialization propertyListWithData:(__bridge NSData *)content.value options:NSPropertyListImmutable format:nullptr error:nullptr];
    require([profile isKindOfClass:NSDictionary.class], "IDENTITY_UNAVAILABLE");
    NSDictionary *allowed = profile[@"Entitlements"];
    require([profile[@"UUID"] isEqual:profileUuid] && [profile[@"TeamIdentifier"] isEqual:@[team]] &&
        [allowed[@"com.apple.application-identifier"] isEqual:group] && [allowed[@"keychain-access-groups"] isEqual:@[group]] &&
        [profile[@"ExpirationDate"] isKindOfClass:NSDate.class] && [profile[@"ExpirationDate"] timeIntervalSinceNow] > 0, "IDENTITY_UNAVAILABLE");
    NSArray *certificates = signing[(__bridge id)kSecCodeInfoCertificates]; require(certificates.count > 0, "IDENTITY_UNAVAILABLE");
    CF<CFDataRef> certificate(SecCertificateCopyData((__bridge SecCertificateRef)certificates[0]));
    require([profile[@"DeveloperCertificates"] containsObject:(__bridge NSData *)certificate.value], "IDENTITY_UNAVAILABLE");
    return {group, profile[@"ExpirationDate"]};
#endif
}

int checkedDirectory(int parent, const char *name, bool create) {
    if (create && mkdirat(parent, name, 0700) != 0) require(errno == EEXIST, "UNAVAILABLE");
    int fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC); require(fd >= 0, "UNAVAILABLE");
    struct stat st = {}; if (fstat(fd, &st) != 0 || !S_ISDIR(st.st_mode) || st.st_uid != getuid() || (st.st_mode & 0022) != 0) { ::close(fd); throw Failure{"UNAVAILABLE"}; }
    return fd;
}
int acquireLock() {
    long size = sysconf(_SC_GETPW_R_SIZE_MAX); require(size > 0 && size <= 1048576, "UNAVAILABLE");
    std::vector<char> buffer(static_cast<size_t>(size)); struct passwd pw = {}, *found = nullptr;
    require(getpwuid_r(getuid(), &pw, buffer.data(), buffer.size(), &found) == 0 && found && pw.pw_dir, "UNAVAILABLE");
    int home = open(pw.pw_dir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC); require(home >= 0, "UNAVAILABLE");
    int library = -1, support = -1, directory = -1, lock = -1;
    try {
        struct stat homeStat = {}; require(fstat(home, &homeStat) == 0 && homeStat.st_uid == getuid() && (homeStat.st_mode & 0022) == 0, "UNAVAILABLE");
        library = checkedDirectory(home, "Library", false);
        support = checkedDirectory(library, "Application Support", false);
        directory = checkedDirectory(support, "app.autplay.desktop.secure-state.v1", true);
        lock = openat(directory, "writer.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600); require(lock >= 0, "UNAVAILABLE");
        struct stat st = {}; require(fstat(lock, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == getuid() && st.st_nlink == 1 && (st.st_mode & 0777) == 0600, "UNAVAILABLE");
        if (flock(lock, LOCK_EX | LOCK_NB) != 0) throw Failure{errno == EWOULDBLOCK ? "IN_USE" : "UNAVAILABLE"};
        ::close(directory); ::close(support); ::close(library); ::close(home); return lock;
    } catch (...) { if (lock >= 0) ::close(lock); if (directory >= 0) ::close(directory); if (support >= 0) ::close(support); if (library >= 0) ::close(library); ::close(home); throw; }
}

#include "AutPlayCommunityKeychain.h"

NSMutableDictionary *genericQuery(Owner &o, NSString *account = nil) {
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    NSMutableDictionary *query = [@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService:kService,
        (__bridge id)kSecMatchSearchList:@[(__bridge id)o.keychain.value]} mutableCopy];
#else
    NSMutableDictionary *query = [@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService:kService, (__bridge id)kSecAttrAccessGroup:o.group,
        (__bridge id)kSecUseDataProtectionKeychain:@YES, (__bridge id)kSecAttrSynchronizable:@NO,
        (__bridge id)kSecUseAuthenticationUI:(__bridge id)kSecUseAuthenticationUIFail} mutableCopy];
#endif
    if (account) query[(__bridge id)kSecAttrAccount] = account; return query;
}
NSMutableDictionary *keyQuery(Owner &o, NSString *tag = nil) {
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    NSMutableDictionary *query = [@{(__bridge id)kSecClass:(__bridge id)kSecClassKey,
        (__bridge id)kSecAttrKeyClass:(__bridge id)kSecAttrKeyClassPrivate,
        (__bridge id)kSecAttrKeyType:(__bridge id)kSecAttrKeyTypeECSECPrimeRandom,
        (__bridge id)kSecAttrLabel:kKeyLabel,
        (__bridge id)kSecMatchSearchList:@[(__bridge id)o.keychain.value]} mutableCopy];
#else
    NSMutableDictionary *query = [@{(__bridge id)kSecClass:(__bridge id)kSecClassKey,
        (__bridge id)kSecAttrKeyClass:(__bridge id)kSecAttrKeyClassPrivate,
        (__bridge id)kSecAttrKeyType:(__bridge id)kSecAttrKeyTypeECSECPrimeRandom,
        (__bridge id)kSecAttrTokenID:(__bridge id)kSecAttrTokenIDSecureEnclave,
        (__bridge id)kSecAttrAccessGroup:o.group, (__bridge id)kSecUseDataProtectionKeychain:@YES,
        (__bridge id)kSecAttrSynchronizable:@NO, (__bridge id)kSecUseAuthenticationUI:(__bridge id)kSecUseAuthenticationUIFail} mutableCopy];
#endif
    if (tag) { keyTag(tag); query[(__bridge id)kSecAttrApplicationTag] = [tag dataUsingEncoding:NSASCIIStringEncoding]; }
    else query[(__bridge id)kSecAttrLabel] = kKeyLabel;
    return query;
}
void genericAttributes(Owner &o, NSDictionary *attributes, NSString *account) {
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    require([attributes isKindOfClass:NSDictionary.class] && [attributes[(__bridge id)kSecAttrService] isEqual:kService] &&
        [attributes[(__bridge id)kSecAttrAccount] isEqual:account] && ![attributes[(__bridge id)kSecAttrSynchronizable] boolValue]);
#else
    require([attributes isKindOfClass:NSDictionary.class] && [attributes[(__bridge id)kSecAttrService] isEqual:kService] &&
        [attributes[(__bridge id)kSecAttrAccessGroup] isEqual:o.group] && [attributes[(__bridge id)kSecAttrAccount] isEqual:account] &&
        [attributes[(__bridge id)kSecAttrAccessible] isEqual:(__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly] &&
        ![attributes[(__bridge id)kSecAttrSynchronizable] boolValue]);
#endif
}
NSMutableData *read(Owner &o, NSString *account, size_t maximum) {
    NSMutableDictionary *query = genericQuery(o, account);
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    query[(__bridge id)kSecReturnAttributes] = @YES; query[(__bridge id)kSecReturnData] = @YES;
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    query[(__bridge id)kSecReturnRef] = @YES;
#endif
    CF<CFTypeRef> result; OSStatus code = SecItemCopyMatching((__bridge CFDictionaryRef)query, result.out());
    if (code == errSecItemNotFound) return nil; status(code);
    NSDictionary *attributes = (__bridge NSDictionary *)result.value; genericAttributes(o, attributes, account);
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    communityACL((__bridge SecKeychainItemRef)attributes[(__bridge id)kSecValueRef], kSecACLAuthorizationDecrypt);
#endif
    NSData *bytes = attributes[(__bridge id)kSecValueData]; require([bytes isKindOfClass:NSData.class] && bytes.length <= maximum);
    return [bytes mutableCopy];
}
void wipe(NSMutableData *bytes) { if (bytes.length) (void)memset_s(bytes.mutableBytes, bytes.length, 0, bytes.length); }
NSDictionary *control(Owner &o) {
    NSMutableData *bytes = read(o, @"k1.control", kControl); require(bytes != nil);
    @try { NSDictionary *result = json(bytes, kControl); require([result[@"format"] isEqual:@"autplay-secure-control-1"]); return result; }
    @finally { wipe(bytes); }
}
void exactRead(Owner &o, NSString *account, NSData *expected, size_t maximum) {
    NSMutableData *actual = read(o, account, maximum);
    bool equal = actual && [actual isEqualToData:expected]; wipe(actual); require(equal, "OUTCOME_PENDING");
}
void add(Owner &o, NSString *account, NSData *bytes, size_t maximum) {
    require(bytes.length >= 2 && bytes.length <= maximum);
    NSMutableDictionary *query = genericQuery(o, account);
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    CF<SecAccessRef> access(communityAccess());
    communityAddQuery(o, query, access.value);
#else
    query[(__bridge id)kSecAttrAccessible] = (__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly;
#endif
    query[(__bridge id)kSecValueData] = bytes;
    OSStatus code = SecItemAdd((__bridge CFDictionaryRef)query, nullptr);
    if (code != errSecDuplicateItem) status(code);
    exactRead(o, account, bytes, maximum);
}
NSArray *attributes(NSMutableDictionary *query, size_t maximum) {
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitAll; query[(__bridge id)kSecReturnAttributes] = @YES;
    CF<CFTypeRef> result; OSStatus code = SecItemCopyMatching((__bridge CFDictionaryRef)query, result.out());
    if (code == errSecItemNotFound) return @[]; status(code);
    id value = (__bridge id)result.value; require([value isKindOfClass:NSArray.class] && [value count] <= maximum, "CAPACITY_REQUIRED"); return value;
}
NSDictionary *inventory(Owner &o) {
    NSArray *rows = attributes(genericQuery(o), 65);
    NSMutableArray *values = [NSMutableArray array], *keys = [NSMutableArray array]; bool hasControl = false; size_t total = 0;
    for (NSDictionary *row in rows) {
        NSString *account = row[(__bridge id)kSecAttrAccount]; genericAttributes(o, row, account);
        if ([account isEqual:@"k1.control"]) { require(!hasControl); hasControl = true; continue; }
        require([account isKindOfClass:NSString.class] && [account hasPrefix:@"k1.value."]);
        NSString *id = [account substringFromIndex:9]; require(uuid(id));
        // Attributes inventory is bounded before any value data is requested.
        NSMutableData *bytes = read(o, account, kValue); require(bytes != nil && bytes.length >= 18);
        total += bytes.length; [values addObject:@{@"id":id, @"length":@(bytes.length)}]; wipe(bytes);
        require(values.count <= 64 && total <= kTotal, "CAPACITY_REQUIRED");
    }
    for (NSDictionary *row in attributes(keyQuery(o), 32)) {
#ifndef AUTPLAY_COMMUNITY_SIGNER_SHA256
        require([row[(__bridge id)kSecAttrAccessGroup] isEqual:o.group] && ![row[(__bridge id)kSecAttrSynchronizable] boolValue] && [row[(__bridge id)kSecAttrLabel] isEqual:kKeyLabel]);
#else
        require(![row[(__bridge id)kSecAttrSynchronizable] boolValue] && [row[(__bridge id)kSecAttrLabel] isEqual:kKeyLabel]);
#endif
        NSData *tagData = row[(__bridge id)kSecAttrApplicationTag]; require([tagData isKindOfClass:NSData.class] && tagData.length <= 80);
        NSString *tag = [[NSString alloc] initWithData:tagData encoding:NSASCIIStringEncoding]; keyTag(tag); require(![keys containsObject:tag]); [keys addObject:tag];
    }
    return @{@"has_control":@(hasControl), @"values":values, @"keys":keys};
}
NSDictionary *intent(Owner &o, NSString *tag, NSString *state) {
    keyTag(tag); NSDictionary *found = nil;
    for (NSDictionary *entry in control(o)[@"key_intents"]) if ([entry[@"tag"] isEqual:tag]) { require(found == nil); found = entry; }
    require(found != nil && [found[@"state"] isEqual:state], "CONTEXT_CHANGED"); return found;
}
SecKeyRef existingKey(Owner &o, NSString *tag, bool allowAbsent = false) {
    NSMutableDictionary *query = keyQuery(o, tag); query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    query[(__bridge id)kSecReturnRef] = @YES; query[(__bridge id)kSecReturnAttributes] = @YES;
    CF<CFTypeRef> result; OSStatus code = SecItemCopyMatching((__bridge CFDictionaryRef)query, result.out());
    if (code == errSecItemNotFound) { if (allowAbsent) return nullptr; throw Failure{"KEY_MISSING"}; } status(code);
    NSDictionary *attributes = (__bridge NSDictionary *)result.value;
    require([attributes isKindOfClass:NSDictionary.class]);
    SecKeyRef key = (__bridge SecKeyRef)attributes[(__bridge id)kSecValueRef];
    require(key && CFGetTypeID(key) == SecKeyGetTypeID());
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    require([attributes isKindOfClass:NSDictionary.class] &&
        [attributes[(__bridge id)kSecAttrApplicationTag] isEqual:[tag dataUsingEncoding:NSASCIIStringEncoding]] &&
        [attributes[(__bridge id)kSecAttrKeyClass] isEqual:(__bridge id)kSecAttrKeyClassPrivate] &&
        [attributes[(__bridge id)kSecAttrKeyType] isEqual:(__bridge id)kSecAttrKeyTypeECSECPrimeRandom] &&
        [attributes[(__bridge id)kSecAttrLabel] isEqual:kKeyLabel] &&
        [attributes[(__bridge id)kSecAttrKeySizeInBits] intValue] == 256);
    // Legacy SecItem results omit sensitive/extractable. SecKeyCopyAttributes reads
    // the actual key properties, including the provider's immutable export flags.
    CF<CFDictionaryRef> properties(SecKeyCopyAttributes(key));
    require(properties.value != nullptr);
    NSDictionary *keyProperties = (__bridge NSDictionary *)properties.value;
    require([keyProperties[(__bridge id)kSecAttrIsExtractable] isEqual:@NO]);
    require([keyProperties[(__bridge id)kSecAttrIsSensitive] isEqual:@YES]);
    require([keyProperties[(__bridge id)kSecAttrIsPermanent] isEqual:@YES]);
    require([keyProperties[(__bridge id)kSecAttrCanSign] isEqual:@YES] &&
        [keyProperties[(__bridge id)kSecAttrCanDerive] isEqual:@NO] &&
        [keyProperties[(__bridge id)kSecAttrCanDecrypt] isEqual:@NO] &&
        [keyProperties[(__bridge id)kSecAttrCanUnwrap] isEqual:@NO]);
    communityACL((SecKeychainItemRef)key, kSecACLAuthorizationSign);
#else
    require([attributes isKindOfClass:NSDictionary.class] && [attributes[(__bridge id)kSecAttrAccessGroup] isEqual:o.group] &&
        [attributes[(__bridge id)kSecAttrApplicationTag] isEqual:[tag dataUsingEncoding:NSASCIIStringEncoding]] &&
        [attributes[(__bridge id)kSecAttrTokenID] isEqual:(__bridge id)kSecAttrTokenIDSecureEnclave] &&
        [attributes[(__bridge id)kSecAttrKeyClass] isEqual:(__bridge id)kSecAttrKeyClassPrivate] &&
        [attributes[(__bridge id)kSecAttrKeyType] isEqual:(__bridge id)kSecAttrKeyTypeECSECPrimeRandom] &&
        [attributes[(__bridge id)kSecAttrLabel] isEqual:kKeyLabel] &&
        [attributes[(__bridge id)kSecAttrAccessible] isEqual:(__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly] &&
        [attributes[(__bridge id)kSecAttrKeySizeInBits] intValue] == 256 && ![attributes[(__bridge id)kSecAttrSynchronizable] boolValue]);
#endif
    CFRetain(key); return key;
}
NSData *point(SecKeyRef privateKey) {
    CF<SecKeyRef> publicKey(SecKeyCopyPublicKey(privateKey)); require(publicKey.value != nullptr, "KEY_MISSING");
    CF<CFErrorRef> error; CF<CFDataRef> representation(SecKeyCopyExternalRepresentation(publicKey.value, error.out()));
    require(representation.value != nullptr, "UNAVAILABLE");
    NSData *result = (__bridge NSData *)representation.value;
    require(result.length == 65 && static_cast<const uint8_t *>(result.bytes)[0] == 4); return result;
}
const char *const domains[] = {
    "autplay:device-compatibility:enrollment:exchange:v2\n",
    "autplay:device-compatibility:admission:submit:v2\n", "autplay:device-compatibility:admission:poll:v2\n",
    "autplay:device-compatibility:admission:recover:v2\n", "autplay:device-compatibility:admission:exchange:v2\n",
    "autplay:device-compatibility:trusted:challenge:v2\n", "autplay:device-compatibility:trusted:exchange:v2\n",
    "autplay:device-compatibility:session:rotate:v2\n", "autplay:device-compatibility:self:claim:v2\n",
    "autplay:device-compatibility:self:poll:v2\n", "autplay:device-compatibility:self:exchange:v2\n",
    "autplay:device-compatibility:recovery:preview:v2\n", "autplay:device-compatibility:recovery:recover:v2\n",
    "autplay:device-compatibility:deletion:request:v2\n", "autplay:device-compatibility:deletion:preview:v2\n",
    "autplay:device-compatibility:deletion:cancel:v2\n"
};
}

#define APJNI(name) Java_app_autplay_desktop_security_MacSecureStateJni_##name
#define APBEGIN std::lock_guard<std::mutex> lock(gMutex); @autoreleasepool { @try { try {
#define APEND(value) } catch (const Failure &failure) { auditFailure(failure); throwJava(env, failure.code); } catch (...) { throwJava(env, "UNAVAILABLE"); } } @catch (NSException *) { throwJava(env, "CORRUPT"); } } return value;
#define APENDVOID } catch (const Failure &failure) { auditFailure(failure); throwJava(env, failure.code); } catch (...) { throwJava(env, "UNAVAILABLE"); } } @catch (NSException *) { throwJava(env, "CORRUPT"); } }

extern "C" JNIEXPORT jint JNICALL APJNI(abiVersion)(JNIEnv *, jobject) { return 1; }
// Only the explicit UI reopen action calls this while the account graph is closed. macOS
// owns the password/approval UI; no password or private key enters the JVM. No item is reset.
extern "C" JNIEXPORT void JNICALL APJNI(authorizeCommunity)(JNIEnv *env, jobject, jstring signer) {
    APBEGIN
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    require(!gOwner, "IN_USE");
    preflight(string(env, signer, 64), @"community-keychain-1");
    Owner o; o.group = kService; o.lock = acquireLock(); openCommunityKeychain(o);
    struct Interaction {
        Interaction() { status(SecKeychainSetUserInteractionAllowed(true)); }
        ~Interaction() { SecKeychainSetUserInteractionAllowed(false); }
    } interaction;
    for (NSDictionary *row in attributes(genericQuery(o), 65)) {
        NSString *account = row[(__bridge id)kSecAttrAccount];
        genericAttributes(o, row, account);
        require([account isEqual:@"k1.control"] || ([account hasPrefix:@"k1.value."] && uuid([account substringFromIndex:9])));
        NSMutableData *bytes = read(o, account, [account isEqual:@"k1.control"] ? kControl : kValue);
        require(bytes != nil); wipe(bytes);
    }
    for (NSDictionary *row in attributes(keyQuery(o), 32)) {
        NSData *tagData = row[(__bridge id)kSecAttrApplicationTag];
        require([tagData isKindOfClass:NSData.class] && tagData.length <= 80);
        NSString *tag = [[NSString alloc] initWithData:tagData encoding:NSASCIIStringEncoding];
        CF<SecKeyRef> key(existingKey(o, tag)); CF<CFErrorRef> error;
        // A separate local-only domain cannot authorize any server operation.
        NSData *message = [@"autplay:local-keychain-reauthorization:v1\n" dataUsingEncoding:NSASCIIStringEncoding];
        CF<CFDataRef> proof(SecKeyCreateSignature(key.value, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,
            (__bridge CFDataRef)message, error.out()));
        require(proof.value != nullptr, "ACCESS_DENIED");
    }
#else
    throw Failure{"IDENTITY_UNAVAILABLE"};
#endif
    APENDVOID
}
extern "C" JNIEXPORT jlong JNICALL APJNI(open)(JNIEnv *env, jobject, jint abi, jstring teamValue, jstring profileValue) {
    APBEGIN
    require(abi == 1 && !gOwner, "IN_USE");
    NSString *team = string(env, teamValue, 64), *profile = string(env, profileValue, 36);
    ValidatedIdentity identity = preflight(team, profile);
    auto instance = std::make_unique<Owner>(); instance->group = identity.group; instance->profileExpiration = identity.expiration; instance->team = team; instance->profile = profile;
    instance->lock = acquireLock(); instance->thread = std::this_thread::get_id();
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    openCommunityKeychain(*instance);
#endif
    require(gNext > 0 && gNext < INT64_MAX, "UNAVAILABLE"); instance->handle = gNext++;
    jlong result = instance->handle; gOwner = std::move(instance); return result;
    APEND(0)
}
extern "C" JNIEXPORT jbyteArray JNICALL APJNI(inventory)(JNIEnv *env, jobject, jlong handle) {
    APBEGIN
    NSDictionary *result = inventory(owner(handle)); NSData *encoded = [NSJSONSerialization dataWithJSONObject:result options:0 error:nullptr]; return output(env, encoded, 32768);
    APEND(nullptr)
}
extern "C" JNIEXPORT jbyteArray JNICALL APJNI(readControl)(JNIEnv *env, jobject, jlong handle) {
    APBEGIN
    NSMutableData *bytes = read(owner(handle), @"k1.control", kControl); if (!bytes) return nullptr;
    @try { return output(env, bytes, kControl); } @finally { wipe(bytes); }
    APEND(nullptr)
}
extern "C" JNIEXPORT void JNICALL APJNI(initializeControl)(JNIEnv *env, jobject, jlong handle, jbyteArray value) {
    APBEGIN
    Owner &o = owner(handle); auto bytes = input(env, value, kControl, 2);
    NSDictionary *state = inventory(o); require(![state[@"has_control"] boolValue] && [state[@"values"] count] == 0 && [state[@"keys"] count] == 0, "CONTEXT_CHANGED");
    NSDictionary *document = json(data(bytes), kControl); require([document[@"format"] isEqual:@"autplay-secure-control-1"] && [document[@"revision"] longLongValue] == 0);
    add(o, @"k1.control", data(bytes), kControl);
    APENDVOID
}
extern "C" JNIEXPORT void JNICALL APJNI(publishControl)(JNIEnv *env, jobject, jlong handle, jbyteArray oldValue, jbyteArray newValue) {
    APBEGIN
    Owner &o = owner(handle); auto before = input(env, oldValue, kControl, 2); auto after = input(env, newValue, kControl, 2);
    NSDictionary *old = json(data(before), kControl), *next = json(data(after), kControl);
    require([old[@"format"] isEqual:@"autplay-secure-control-1"] && [next[@"format"] isEqual:old[@"format"]] && [next[@"revision"] longLongValue] == [old[@"revision"] longLongValue] + 1);
    exactRead(o, @"k1.control", data(before), kControl);
    NSMutableDictionary *query = genericQuery(o, @"k1.control");
    status(SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)@{(__bridge id)kSecValueData:data(after)}));
    exactRead(o, @"k1.control", data(after), kControl);
    APENDVOID
}
extern "C" JNIEXPORT jbyteArray JNICALL APJNI(readValue)(JNIEnv *env, jobject, jlong handle, jstring idValue) {
    APBEGIN
    NSMutableData *bytes = read(owner(handle), valueAccount(string(env, idValue, 36)), kValue); if (!bytes) return nullptr;
    @try { return output(env, bytes, kValue); } @finally { wipe(bytes); }
    APEND(nullptr)
}
extern "C" JNIEXPORT void JNICALL APJNI(stageValue)(JNIEnv *env, jobject, jlong handle, jstring idValue, jbyteArray value) {
    APBEGIN
    Owner &o = owner(handle); NSString *id = string(env, idValue, 36); auto bytes = input(env, value, kValue, 18);
    NSDictionary *state = control(o); bool registered = false;
    for (NSDictionary *entry in state[@"cleanup"]) if ([entry[@"value_id"] isEqual:id] && [entry[@"length"] unsignedLongLongValue] == bytes.value.size()) registered = true;
    require(registered, "CONTEXT_CHANGED"); add(o, valueAccount(id), data(bytes), kValue);
    APENDVOID
}
extern "C" JNIEXPORT void JNICALL APJNI(deleteValue)(JNIEnv *env, jobject, jlong handle, jstring idValue) {
    APBEGIN
    Owner &o = owner(handle); NSString *id = string(env, idValue, 36); NSDictionary *state = control(o); bool registered = false;
    for (NSDictionary *entry in state[@"selected_slots"]) require(![entry[@"value_id"] isEqual:id], "CONTEXT_CHANGED");
    for (NSDictionary *entry in state[@"cleanup"]) if ([entry[@"value_id"] isEqual:id]) registered = true;
    require(registered, "CONTEXT_CHANGED");
    status(SecItemDelete((__bridge CFDictionaryRef)genericQuery(o, valueAccount(id))));
    NSMutableData *remaining = read(o, valueAccount(id), kValue); bool absent = remaining == nil; wipe(remaining); require(absent, "OUTCOME_PENDING");
    APENDVOID
}
extern "C" JNIEXPORT jbyteArray JNICALL APJNI(createNewKey)(JNIEnv *env, jobject, jlong handle, jstring tagValue) {
    APBEGIN
    Owner &o = owner(handle); NSString *tag = string(env, tagValue, 80); intent(o, tag, @"CREATING");
    CF<SecKeyRef> found(existingKey(o, tag, true));
    if (found.value) return output(env, point(found.value), 65);
    require([inventory(o)[@"keys"] count] < 32, "CAPACITY_REQUIRED");
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
    CF<SecKeyRef> created(createCommunityKey(o, tag));
#else
    CF<CFErrorRef> error; CF<SecAccessControlRef> access(SecAccessControlCreateWithFlags(kCFAllocatorDefault, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, kSecAccessControlPrivateKeyUsage, error.out()));
    require(access.value != nullptr, "UNAVAILABLE");
    NSDictionary *attributes = @{(__bridge id)kSecAttrKeyType:(__bridge id)kSecAttrKeyTypeECSECPrimeRandom,
        (__bridge id)kSecAttrKeySizeInBits:@256, (__bridge id)kSecAttrTokenID:(__bridge id)kSecAttrTokenIDSecureEnclave,
        (__bridge id)kSecUseDataProtectionKeychain:@YES, (__bridge id)kSecAttrSynchronizable:@NO,
        (__bridge id)kSecPrivateKeyAttrs:@{(__bridge id)kSecAttrIsPermanent:@YES,
            (__bridge id)kSecAttrApplicationTag:[tag dataUsingEncoding:NSASCIIStringEncoding], (__bridge id)kSecAttrAccessGroup:o.group,
            (__bridge id)kSecAttrLabel:kKeyLabel, (__bridge id)kSecAttrAccessControl:(__bridge id)access.value},
        (__bridge id)kSecPublicKeyAttrs:@{(__bridge id)kSecAttrIsPermanent:@NO}};
    CF<SecKeyRef> created(SecKeyCreateRandomKey((__bridge CFDictionaryRef)attributes, error.out()));
#endif
    require(created.value != nullptr, "OUTCOME_PENDING");
    CF<SecKeyRef> verified(existingKey(o, tag));
    require([point(created.value) isEqualToData:point(verified.value)], "OUTCOME_PENDING"); return output(env, point(verified.value), 65);
    APEND(nullptr)
}
extern "C" JNIEXPORT jbyteArray JNICALL APJNI(publicKey)(JNIEnv *env, jobject, jlong handle, jstring tagValue) {
    APBEGIN
    Owner &o = owner(handle); NSString *tag = string(env, tagValue, 80);
    NSDictionary *state = control(o); bool registered = false;
    for (NSDictionary *entry in state[@"key_intents"]) if ([entry[@"tag"] isEqual:tag] && ![entry[@"state"] isEqual:@"RETIRED"]) registered = true;
    require(registered, "KEY_MISSING"); CF<SecKeyRef> key(existingKey(o, tag)); return output(env, point(key.value), 65);
    APEND(nullptr)
}
extern "C" JNIEXPORT jbyteArray JNICALL APJNI(sign)(JNIEnv *env, jobject, jlong handle, jstring tagValue, jint operation, jbyteArray digestValue) {
    APBEGIN
    Owner &o = owner(handle); NSString *tag = string(env, tagValue, 80); intent(o, tag, @"BOUND");
    auto digest = input(env, digestValue, 32, 32); require(operation >= 0 && size_t(operation) < sizeof(domains) / sizeof(domains[0]));
    CF<SecKeyRef> key(existingKey(o, tag)); require(SecKeyIsAlgorithmSupported(key.value, kSecKeyOperationTypeSign, kSecKeyAlgorithmECDSASignatureMessageX962SHA256), "UNAVAILABLE");
    const char *domain = domains[operation]; Bytes message; message.value.resize(strlen(domain) + 32);
    memcpy(message.value.data(), domain, strlen(domain)); memcpy(message.value.data() + strlen(domain), digest.value.data(), 32);
    CF<CFErrorRef> error; CF<CFDataRef> signature(SecKeyCreateSignature(key.value, kSecKeyAlgorithmECDSASignatureMessageX962SHA256, (__bridge CFDataRef)data(message), error.out()));
    require(signature.value != nullptr, "UNAVAILABLE"); return output(env, (__bridge NSData *)signature.value, 72);
    APEND(nullptr)
}
extern "C" JNIEXPORT void JNICALL APJNI(deleteKey)(JNIEnv *env, jobject, jlong handle, jstring tagValue) {
    APBEGIN
    Owner &o = owner(handle); NSString *tag = string(env, tagValue, 80); intent(o, tag, @"RETIRED");
    // JVM proves no selected/pending record still references this exact retired tag.
    OSStatus code = SecItemDelete((__bridge CFDictionaryRef)keyQuery(o, tag)); if (code != errSecItemNotFound) status(code);
    CF<SecKeyRef> remaining(existingKey(o, tag, true)); require(!remaining.value, "OUTCOME_PENDING");
    APENDVOID
}
extern "C" JNIEXPORT void JNICALL APJNI(close)(JNIEnv *env, jobject, jlong handle) {
    APBEGIN
    owner(handle, false); gOwner.reset();
    APENDVOID
}
