// The community signer is a reviewed build input, never an environment/user-file override.
#pragma once
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <CommonCrypto/CommonDigest.h>

namespace autplay_community {
struct Rejected {};
inline void check(bool value) { if (!value) throw Rejected{}; }
template<class T> struct Owned {
    T value = nullptr;
    ~Owned() { if (value) CFRelease(value); }
    T *out() { return &value; }
};
inline NSString *pin() { return @AUTPLAY_COMMUNITY_SIGNER_SHA256; }
inline NSString *hex(const unsigned char *bytes, size_t count) {
    NSMutableString *result = [NSMutableString stringWithCapacity:count * 2];
    for (size_t index = 0; index < count; ++index) [result appendFormat:@"%02x", bytes[index]];
    return result;
}
inline NSDictionary *signing(SecStaticCodeRef code, NSString *identifier, bool launcher) {
    Owned<CFDictionaryRef> raw;
    check(SecCodeCopySigningInformation(code, kSecCSSigningInformation, raw.out()) == errSecSuccess);
    NSDictionary *info = (__bridge NSDictionary *)raw.value;
    check([info[(__bridge id)kSecCodeInfoIdentifier] isEqual:identifier]);
    unsigned int flags = [info[(__bridge id)kSecCodeInfoFlags] unsignedIntValue];
    check((flags & kSecCodeSignatureRuntime) && !(flags & kSecCodeSignatureAdhoc));
    NSArray *certificates = info[(__bridge id)kSecCodeInfoCertificates];
    check([certificates isKindOfClass:NSArray.class] && certificates.count > 0);
    Owned<CFDataRef> der;
    der.value = SecCertificateCopyData((__bridge SecCertificateRef)certificates[0]);
    check(der.value != nullptr);
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(CFDataGetBytePtr(der.value), (CC_LONG)CFDataGetLength(der.value), digest);
    check([hex(digest, sizeof(digest)) isEqual:pin()]);
    NSDictionary *entitlements = info[(__bridge id)kSecCodeInfoEntitlementsDict];
    check(!entitlements || [entitlements isKindOfClass:NSDictionary.class]);
    for (NSString *forbidden in @[@"get-task-allow", @"com.apple.security.get-task-allow",
        @"com.apple.security.cs.allow-dyld-environment-variables", @"com.apple.security.cs.debugger",
        @"com.apple.security.cs.allow-unsigned-executable-memory"])
        check(![entitlements[forbidden] boolValue]);
    // No Apple Team ID is available for the bundled JVM. Only the JVM launcher gets this
    // exception; native helpers keep system library validation. Bundle sealing still applies.
    if (!launcher) check(![entitlements[@"com.apple.security.cs.disable-library-validation"] boolValue]);
    check(entitlements[@"keychain-access-groups"] == nil && entitlements[@"com.apple.application-identifier"] == nil);
    return info;
}
inline SecRequirementRef requirement(NSString *identifier, NSString *expectedPin) {
    check([expectedPin isEqual:pin()]);
    Owned<SecCodeRef> running;
    check(SecCodeCopySelf(kSecCSDefaultFlags, running.out()) == errSecSuccess);
    NSDictionary *info = signing(running.value, @"app.autplay.desktop", true);
    NSArray *certificates = info[(__bridge id)kSecCodeInfoCertificates];
    Owned<CFDataRef> der;
    der.value = SecCertificateCopyData((__bridge SecCertificateRef)certificates[0]);
    unsigned char digest[CC_SHA1_DIGEST_LENGTH];
    // Apple's requirement language uses a SHA-1 certificate locator. The certificate was
    // independently pinned with SHA-256 above, and every accepted code item is checked again.
    CC_SHA1(CFDataGetBytePtr(der.value), (CC_LONG)CFDataGetLength(der.value), digest);
    NSString *expression = [NSString stringWithFormat:@"identifier \"%@\" and certificate leaf = H\"%@\"", identifier, hex(digest, sizeof(digest))];
    SecRequirementRef value = nullptr;
    check(SecRequirementCreateWithString((__bridge CFStringRef)expression, 0, &value) == errSecSuccess);
    return value;
}
inline void validate(NSURL *url, NSString *identifier, bool launcher = false) {
    Owned<SecStaticCodeRef> code;
    Owned<SecRequirementRef> expected;
    expected.value = requirement(identifier, pin());
    check(SecStaticCodeCreateWithPath((__bridge CFURLRef)url, 0, code.out()) == errSecSuccess);
    check(SecStaticCodeCheckValidity(code.value, kSecCSStrictValidate | kSecCSCheckAllArchitectures | kSecCSCheckNestedCode,
        expected.value) == errSecSuccess);
    signing(code.value, identifier, launcher);
}
}
#endif
