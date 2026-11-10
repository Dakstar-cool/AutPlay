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
        return denied ? 0 : 1;
    }
}
