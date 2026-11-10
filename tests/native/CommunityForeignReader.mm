#import <Foundation/Foundation.h>
#import <Security/Security.h>

// Deliberately has a different designated requirement and no production preflight. This
// proves the OS Keychain ACL itself rejects a different program, independent of our checks.
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 2 || SecKeychainSetUserInteractionAllowed(false) != errSecSuccess) return 2;
        NSString *service = [@"app.autplay.desktop.community-state.v1." stringByAppendingString:[NSString stringWithUTF8String:argv[1]]];
        CFTypeRef value = nullptr;
        NSDictionary *query = @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
            (__bridge id)kSecAttrService:service, (__bridge id)kSecAttrAccount:@"k1.control", (__bridge id)kSecReturnData:@YES};
        OSStatus result = SecItemCopyMatching((__bridge CFDictionaryRef)query, &value);
        bool denied = !value && (result == errSecInteractionNotAllowed || result == errSecAuthFailed || result == errSecNoAccessForItem);
        if (value) CFRelease(value);
        if (!denied) return 1;
        NSString *label = [@"app.autplay.desktop.community-key.v1." stringByAppendingString:[NSString stringWithUTF8String:argv[1]]];
        NSDictionary *keyQuery = @{(__bridge id)kSecClass:(__bridge id)kSecClassKey,
            (__bridge id)kSecAttrKeyClass:(__bridge id)kSecAttrKeyClassPrivate,
            (__bridge id)kSecAttrLabel:label, (__bridge id)kSecReturnRef:@YES};
        CFTypeRef key = nullptr;
        OSStatus keyStatus = SecItemCopyMatching((__bridge CFDictionaryRef)keyQuery, &key);
        if (keyStatus != errSecSuccess || !key || CFGetTypeID(key) != SecKeyGetTypeID()) {
            if (key) CFRelease(key);
            return 2;
        }
        NSData *message = [@"synthetic-foreign-signature-attempt" dataUsingEncoding:NSASCIIStringEncoding];
        CFErrorRef error = nullptr;
        CFDataRef signature = SecKeyCreateSignature((SecKeyRef)key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,
            (__bridge CFDataRef)message, &error);
        bool signDenied = signature == nullptr && error != nullptr;
        if (signature) CFRelease(signature); if (error) CFRelease(error); CFRelease(key);
        return signDenied ? 0 : 3;
    }
}
