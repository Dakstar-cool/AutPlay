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
    NSString *root = [[NSString stringWithUTF8String:pw->pw_dir] stringByAppendingPathComponent:@"Library/Keychains"];
    require([url.path hasPrefix:[root stringByAppendingString:@"/"]], "IDENTITY_UNAVAILABLE");
    realURL([NSURL fileURLWithPath:root isDirectory:YES]);
    int directory = open(root.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    require(directory >= 0, "IDENTITY_UNAVAILABLE");
    struct stat parent = {};
    bool ownedDirectory = fstat(directory, &parent) == 0 && S_ISDIR(parent.st_mode) &&
        parent.st_uid == getuid() && (parent.st_mode & 0022) == 0;
    ::close(directory); require(ownedDirectory, "IDENTITY_UNAVAILABLE");
    struct stat st = {};
    // Apple's encrypted Keychain database may be 0644 (its file DB defaults to 0666
    // before umask). Confidentiality comes from Keychain encryption and item access control;
    // require exclusive write ownership, rather than treating ciphertext as a plaintext key.
    require(lstat(url.path.fileSystemRepresentation, &st) == 0 && S_ISREG(st.st_mode) &&
        st.st_uid == getuid() && (st.st_mode & 0022) == 0 && st.st_nlink == 1, "IDENTITY_UNAVAILABLE");
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
SecKeyRef createCommunityKey(Owner &o, NSString *tag) {
    CF<SecAccessRef> access(communityAccess());
    CF<SecKeyRef> publicKey, privateKey;
    // The high-level legacy generator ignores the sensitive flag. Set both immutable
    // provider flags explicitly; no private bytes are generated in/exported to our process.
    status(SecKeyCreatePair(o.keychain.value, CSSM_ALGID_ECDSA, 256, 0,
        CSSM_KEYUSE_VERIFY, CSSM_KEYATTR_EXTRACTABLE,
        CSSM_KEYUSE_SIGN, CSSM_KEYATTR_PERMANENT | CSSM_KEYATTR_SENSITIVE,
        access.value, publicKey.out(), privateKey.out()));
    require(publicKey.value && privateKey.value, "OUTCOME_PENDING");
    NSData *label = [kKeyLabel dataUsingEncoding:NSUTF8StringEncoding];
    NSData *tagBytes = [tag dataUsingEncoding:NSASCIIStringEncoding];
    SecKeychainAttribute fields[] = {
        {kSecKeyPrintName, (UInt32)label.length, const_cast<void *>(label.bytes)},
        {kSecKeyApplicationTag, (UInt32)tagBytes.length, const_cast<void *>(tagBytes.bytes)}
    };
    SecKeychainAttributeList attributes = {2, fields};
    // As in Apple's high-level generator, metadata follows creation. The durable CREATING
    // intent already exists, and no public key/proof is returned before exact readback.
    OSStatus tagged = SecKeychainItemModifyAttributesAndData((SecKeychainItemRef)privateKey.value, &attributes, 0, nullptr);
    if (tagged != errSecSuccess) {
        // Only this just-created, never-published reference is eligible for cleanup.
        SecKeychainItemDelete((SecKeychainItemRef)privateKey.value);
        throw Failure{"OUTCOME_PENDING"};
    }
    CFRetain(privateKey.value); return privateKey.value;
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
