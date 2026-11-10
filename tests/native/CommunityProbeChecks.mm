#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <jni.h>
#include <cstdio>

static void failed(JNIEnv *env) { env->ThrowNew(env->FindClass("java/lang/AssertionError"), "KEY_EXPORT_OR_ACL_CHECK_FAILED"); }
extern "C" JNIEXPORT void JNICALL Java_app_autplay_desktop_security_CommunityKeychainProbe_checkNonExportable(JNIEnv *env, jclass, jstring pin) {
    @autoreleasepool {
        const char *raw = env->GetStringUTFChars(pin, nullptr);
        NSString *label = [@"app.autplay.desktop.community-key.v1." stringByAppendingString:[NSString stringWithUTF8String:raw]];
        env->ReleaseStringUTFChars(pin, raw);
        SecKeychainRef keychain = nullptr;
        if (SecKeychainCopyDefault(&keychain) != errSecSuccess || !keychain) { failed(env); return; }
        CFTypeRef key = nullptr;
        NSDictionary *query = @{(__bridge id)kSecClass:(__bridge id)kSecClassKey,
            (__bridge id)kSecAttrLabel:label, (__bridge id)kSecAttrKeyClass:(__bridge id)kSecAttrKeyClassPrivate,
            (__bridge id)kSecAttrKeyType:(__bridge id)kSecAttrKeyTypeECSECPrimeRandom,
            (__bridge id)kSecMatchSearchList:@[(__bridge id)keychain],
            (__bridge id)kSecReturnRef:@YES};
        OSStatus lookup = SecItemCopyMatching((__bridge CFDictionaryRef)query, &key);
        CFRelease(keychain);
        if (lookup != errSecSuccess || !key) {
            fprintf(stderr, "AUTPLAY_EXPORT_LOOKUP_FAILED status=%d\n", (int)lookup);
            if (key) CFRelease(key); failed(env); return;
        }
        CFDataRef exported = nullptr;
        SecItemImportExportKeyParameters parameters = {};
        parameters.version = SEC_KEY_IMPORT_EXPORT_PARAMS_VERSION;
        parameters.passphrase = CFSTR("synthetic-export-test-password");
        OSStatus result = SecItemExport(key, kSecFormatWrappedPKCS8, 0, &parameters, &exported);
        // Test encrypted and raw export, as well as immutable attributes in production.
        CFErrorRef error = nullptr;
        CFDataRef rawKey = SecKeyCopyExternalRepresentation((SecKeyRef)key, &error);
        CFDataRef plain = nullptr;
        OSStatus plainResult = SecItemExport(key, kSecFormatOpenSSL, 0, nullptr, &plain);
        bool rejected = result != errSecSuccess && !exported && !rawKey && plainResult != errSecSuccess && !plain;
        if (!rejected) fprintf(stderr, "AUTPLAY_EXPORT_CHECK_FAILED wrapped_status=%d wrapped_bytes=%ld raw_bytes=%ld raw_status=%ld plain_status=%d plain_bytes=%ld\n",
            (int)result, exported ? (long)CFDataGetLength(exported) : -1,
            rawKey ? (long)CFDataGetLength(rawKey) : -1, error ? (long)CFErrorGetCode(error) : 0,
            (int)plainResult, plain ? (long)CFDataGetLength(plain) : -1);
        if (exported) CFRelease(exported); if (rawKey) CFRelease(rawKey); if (plain) CFRelease(plain);
        if (error) CFRelease(error); CFRelease(key);
        if (!rejected) failed(env);
    }
}
extern "C" JNIEXPORT void JNICALL Java_app_autplay_desktop_security_CommunityKeychainProbe_lockKeychain(JNIEnv *env, jclass) {
    SecKeychainRef keychain = nullptr;
    if (SecKeychainCopyDefault(&keychain) != errSecSuccess || !keychain) { failed(env); return; }
    OSStatus result = SecKeychainLock(keychain); CFRelease(keychain);
    if (result != errSecSuccess) failed(env);
}
