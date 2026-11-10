// Included inside the secure-state owner namespace, after its bounded error/CF helpers.
#ifdef AUTPLAY_COMMUNITY_SIGNER_SHA256
void openCommunityKeychain(Owner &o) {
    status(SecKeychainSetUserInteractionAllowed(false));
    status(SecKeychainCopyDefault(o.keychain.out()));
    char path[4096] = {}; UInt32 length = sizeof(path);
    status(SecKeychainGetPath(o.keychain.value, &length, path));
    require(length > 0 && length < sizeof(path), "IDENTITY_UNAVAILABLE");
    NSURL *url = realURL([NSURL fileURLWithPath:[NSString stringWithUTF8String:path]]);
    struct passwd *pw = getpwuid(getuid());
    require(pw && pw->pw_dir, "IDENTITY_UNAVAILABLE");
    NSString *root = [[NSString stringWithUTF8String:pw->pw_dir] stringByAppendingPathComponent:@"Library/Keychains/"];
    require([url.path hasPrefix:root], "IDENTITY_UNAVAILABLE");
    struct stat st = {};
    require(lstat(url.path.fileSystemRepresentation, &st) == 0 && S_ISREG(st.st_mode) &&
        st.st_uid == getuid() && (st.st_mode & 0077) == 0 && st.st_nlink == 1, "IDENTITY_UNAVAILABLE");
    SecKeychainStatus flags = 0;
    status(SecKeychainGetStatus(o.keychain.value, &flags));
    require((flags & kSecUnlockStateStatus) != 0, "LOCKED");
}
SecAccessRef communityAccess() {
    CF<SecTrustedApplicationRef> self;
    status(SecTrustedApplicationCreateFromPath(nullptr, self.out()));
    SecAccessRef access = nullptr;
    // A null trusted-application array grants ALL applications access. Never use one.
    status(SecAccessCreate((__bridge CFStringRef)kService, (__bridge CFArrayRef)@[(__bridge id)self.value], &access));
    return access;
}
void communityACL(SecKeychainItemRef item, CFStringRef operation) {
    require(item != nullptr);
    CF<SecAccessRef> access; status(SecKeychainItemCopyAccess(item, access.out()));
    CF<CFArrayRef> list(SecAccessCopyMatchingACLList(access.value, operation));
    require(list.value && CFArrayGetCount(list.value) > 0, "ACCESS_DENIED");
    for (id entry in (__bridge NSArray *)list.value) {
        CF<CFArrayRef> apps; CF<CFStringRef> description; SecKeychainPromptSelector prompt = 0;
        status(SecACLCopyContents((__bridge SecACLRef)entry, apps.out(), description.out(), &prompt));
        require(apps.value && CFArrayGetCount(apps.value) == 1, "ACCESS_DENIED");
    }
}
void communityAddQuery(Owner &o, NSMutableDictionary *query, SecAccessRef access) {
    [query removeObjectForKey:(__bridge id)kSecMatchSearchList];
    [query removeObjectForKey:(__bridge id)kSecUseAuthenticationUI];
    query[(__bridge id)kSecUseKeychain] = (__bridge id)o.keychain.value;
    query[(__bridge id)kSecAttrAccess] = (__bridge id)access;
}
#endif
