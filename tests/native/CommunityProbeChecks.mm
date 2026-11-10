#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <jni.h>

static void failed(JNIEnv *env) { env->ThrowNew(env->FindClass("java/lang/AssertionError"), "KEY_EXPORT_OR_ACL_CHECK_FAILED"); }
extern "C" JNIEXPORT void JNICALL Java_app_autplay_desktop_security_CommunityKeychainProbe_checkNonExportable(JNIEnv *env, jclass, jstring pin) {
    @autoreleasepool {
        const char *raw = env->GetStringUTFChars(pin, nullptr);
        NSString *label = [@"app.autplay.desktop.community-key.v1." stringByAppendingString:[NSString stringWithUTF8String:raw]];
        env->ReleaseStringUTFChars(pin, raw);
        CFTypeRef key = nullptr;
        NSDictionary *query = @{(__bridge id)kSecClass:(__bridge id)kSecClassKey,
            (__bridge id)kSecAttrLabel:label, (__bridge id)kSecAttrKeyClass:(__bridge id)kSecAttrKeyClassPrivate,
            (__bridge id)kSecReturnRef:@YES};
        if (SecItemCopyMatching((__bridge CFDictionaryRef)query, &key) != errSecSuccess || !key) { failed(env); return; }
        CFDataRef exported = nullptr;
        OSStatus result = SecItemExport(key, kSecFormatWrappedPKCS8, 0, nullptr, &exported);
        // An invalid export configuration could fail even for an exportable key. Test both
        // supported raw export paths and also check the immutable key attributes in production.
        CFErrorRef error = nullptr;
        CFDataRef rawKey = SecKeyCopyExternalRepresentation((SecKeyRef)key, &error);
        CFDataRef plain = nullptr;
        OSStatus plainResult = SecItemExport(key, kSecFormatOpenSSL, 0, nullptr, &plain);
        bool rejected = result != errSecSuccess && !exported && !rawKey && plainResult != errSecSuccess && !plain;
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
