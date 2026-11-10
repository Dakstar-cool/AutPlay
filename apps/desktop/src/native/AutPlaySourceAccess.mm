// G1 JNI ABI1. ARC, C++17, Foundation/AppKit/Security; separate from secret/audio libraries.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <Security/Security.h>
#include <jni.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/param.h>
#include <sys/codesign.h>
#include <fcntl.h>
#include <unistd.h>
#include <pwd.h>
#include <dirent.h>
#include <dlfcn.h>
#include <cerrno>
#include <atomic>
#include <chrono>
#include <cstring>
#include <map>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <type_traits>
#include <vector>

namespace {
struct Failure { const char *code; };
void require(bool value, const char *code = "source_repair_required") { if(!value) throw Failure{code}; }
using Clock = std::chrono::steady_clock;
constexpr int64_t kActiveMs = 900000, kFileBytes = 2147483648LL, kBatchBytes = 8589934592LL;
std::mutex gate;
std::atomic<jlong> liveToken{0};
std::atomic_bool admissionFenced{false};
// A close error has an uncertain OS outcome. Preserve the installation owner and evidence;
// never retry an fd number that the OS might already have reused.
struct UncertainDescriptors {
    int values[64] = {}; size_t count = 0;
    void push_back(int fd) noexcept { if(count<64) values[count++]=fd; }
    bool empty() const noexcept { return count==0; }
} uncertainDescriptors;
NSOpenPanel *__strong currentPanel = nil; // Main queue only.
jlong nextToken = 1;
struct FD {
    int value = -1; bool poisoned = false;
    explicit FD(int value = -1): value(value) {}
    FD(const FD&) = delete; FD& operator=(const FD&) = delete;
    FD(FD&& other) noexcept : value(other.value), poisoned(other.poisoned) { other.value = -1; }
    ~FD() { if(value >= 0 && !poisoned && ::close(value)!=0) uncertainDescriptors.push_back(value); }
    void settle() {
        require(!poisoned, "source_drain_pending");
        if(value >= 0) {
            if(::close(value) != 0) { poisoned = true; uncertainDescriptors.push_back(value); throw Failure{"source_drain_pending"}; }
            value = -1;
        }
    }
};
struct Selected {
    NSURL *__strong url; NSData *__strong identity; NSData *__strong bookmark;
    NSString *__strong label; NSString *__strong kind;
};
struct File {
    std::vector<std::string> relative; NSString *__strong name; struct stat baseline = {};
};
struct Batch {
    FD root; NSURL *__strong url; NSData *__strong identity; NSString *__strong kind;
    struct stat rootStat = {}; std::vector<File> files; int64_t preparationMs = 0;
    Clock::time_point activeStart; int64_t chargedMs = 0; bool copying = false;
};
struct Read {
    FD descriptor; jlong batch; size_t index; int64_t offset = 0;
};
struct Owner {
    jlong token; NSString *__strong team; NSString *__strong home; NSArray<NSString*> *__strong exclusions;
    NSString *__strong directory; FD directoryFd; FD lock; struct stat directoryStat = {}, lockStat = {};
    bool fenced = false; bool chooser = false;
    std::map<jlong, Selected> selections;
    std::map<jlong, std::unique_ptr<Batch>> batches;
    std::map<jlong, std::unique_ptr<Read>> reads;
};
std::unique_ptr<Owner> lifetime;

NSString *text(JNIEnv *env, jstring value, size_t max = 4096) {
    require(value, "source_selection_invalid"); jsize n = env->GetStringLength(value);
    require(n > 0 && size_t(n) <= max, "source_selection_invalid");
    const jchar *chars = env->GetStringChars(value, nullptr); require(chars, "source_unavailable");
    NSString *result = [[NSString alloc] initWithCharacters:reinterpret_cast<const unichar*>(chars) length:n];
    env->ReleaseStringChars(value, chars); require(result && [result rangeOfString:@"\0"].location == NSNotFound, "source_selection_invalid"); return result;
}
NSData *bytes(JNIEnv *env, jbyteArray input, size_t max) {
    require(input, "source_selection_invalid"); jsize n = env->GetArrayLength(input);
    require(n > 0 && size_t(n) <= max, "source_selection_invalid");
    NSMutableData *result = [NSMutableData dataWithLength:n];
    env->GetByteArrayRegion(input, 0, n, reinterpret_cast<jbyte*>(result.mutableBytes));
    require(!env->ExceptionCheck(), "source_unavailable"); return result;
}
jbyteArray output(JNIEnv *env, NSData *value, size_t max = 2097152) {
    require(value && value.length <= max, "source_limit");
    jbyteArray result = env->NewByteArray(jsize(value.length)); require(result, "source_unavailable");
    if(value.length) env->SetByteArrayRegion(result, 0, jsize(value.length), reinterpret_cast<const jbyte*>(value.bytes));
    require(!env->ExceptionCheck(), "source_unavailable"); return result;
}
jstring outputString(JNIEnv *env, NSString *value) {
    require(value, "source_unavailable"); std::vector<unichar> chars(value.length);
    [value getCharacters:chars.data() range:NSMakeRange(0, value.length)];
    return env->NewString(reinterpret_cast<const jchar*>(chars.data()), jsize(chars.size()));
}
NSData *json(id value) {
    NSData *result = [NSJSONSerialization dataWithJSONObject:value options:0 error:nullptr];
    require(result, "source_unavailable"); return result;
}
template<class T> struct CF {
    T value = nullptr; ~CF() { if(value) CFRelease(value); }
    T *out() { return &value; }
};
bool sameObject(const struct stat &a, const struct stat &b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && (a.st_mode & S_IFMT) == (b.st_mode & S_IFMT) &&
        a.st_birthtimespec.tv_sec == b.st_birthtimespec.tv_sec && a.st_birthtimespec.tv_nsec == b.st_birthtimespec.tv_nsec;
}
bool sameFile(const struct stat &a, const struct stat &b) {
    return sameObject(a,b) && a.st_nlink == b.st_nlink && a.st_size == b.st_size && a.st_mtimespec.tv_sec == b.st_mtimespec.tv_sec &&
        a.st_mtimespec.tv_nsec == b.st_mtimespec.tv_nsec && a.st_ctimespec.tv_sec == b.st_ctimespec.tv_sec && a.st_ctimespec.tv_nsec == b.st_ctimespec.tv_nsec;
}
struct stat statFd(int fd) { struct stat s = {}; require(fd >= 0 && fstat(fd, &s) == 0); return s; }
NSString *pathOf(int fd) {
    char path[MAXPATHLEN] = {}; require(fcntl(fd,F_GETPATH,path)==0);
    NSString *value=[NSString stringWithUTF8String:path]; require(value && [value hasPrefix:@"/"]); return value;
}
/** All components, including ancestors, are opened read-only with no-follow. Never realpath through links. */
FD openAbsolute(NSString *path) {
    require([path hasPrefix:@"/"] && [path isEqualToString:path.stringByStandardizingPath]);
    NSArray<NSString*> *parts = path.pathComponents; require(parts.count <= 256);
    FD cursor(::open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)); require(cursor.value >= 0);
    for(NSUInteger i=1; i<parts.count; ++i) {
        NSString *part = parts[i]; require(part.length && ![part isEqual:@"."] && ![part isEqual:@".."]);
        bool last = i + 1 == parts.count;
        FD opened(openat(cursor.value, part.fileSystemRepresentation,
            O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | (last ? 0 : O_DIRECTORY)));
        require(opened.value >= 0); cursor.settle(); cursor.value = opened.value; opened.value = -1;
        struct stat s = statFd(cursor.value); require(S_ISDIR(s.st_mode) || (last && S_ISREG(s.st_mode)));
    }
    return cursor;
}
bool within(NSString *path, NSString *root) { return [path isEqual:root] || [path hasPrefix:[root stringByAppendingString:@"/"]]; }
void external(Owner &o, NSString *path) {
    for(NSString *root in o.exclusions) require(!within(path,root) && !within(root,path), "source_internal_rejected");
}
void preflight(NSString *team) {
    require(team.length == 10 && [team rangeOfCharacterFromSet:[[NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"] invertedSet]].location == NSNotFound, "source_identity_unavailable");
    NSBundle *bundle = NSBundle.mainBundle;
    require([bundle.bundleIdentifier isEqual:@"app.autplay.desktop"] && [bundle.bundleURL.pathExtension isEqual:@"app"], "source_identity_unavailable");
    FD bundleFd = openAbsolute(bundle.bundleURL.path); require(S_ISDIR(statFd(bundleFd.value).st_mode), "source_identity_unavailable");
    NSString *requirementText = [NSString stringWithFormat:@"anchor apple generic and identifier \"app.autplay.desktop\" and certificate leaf[subject.OU] = \"%@\" and certificate leaf[field.1.2.840.113635.100.6.1.13] exists",team];
    CF<SecRequirementRef> requirement; CF<SecCodeRef> running; CF<SecStaticCodeRef> sealed;
    require(SecRequirementCreateWithString((__bridge CFStringRef)requirementText,0,requirement.out()) == errSecSuccess &&
        SecCodeCopySelf(0,running.out()) == errSecSuccess && SecCodeCheckValidity(running.value,0,requirement.value) == errSecSuccess &&
        SecStaticCodeCreateWithPath((__bridge CFURLRef)bundle.bundleURL,0,sealed.out()) == errSecSuccess &&
        SecStaticCodeCheckValidity(sealed.value,kSecCSStrictValidate | kSecCSCheckAllArchitectures | kSecCSCheckNestedCode,requirement.value) == errSecSuccess, "source_identity_unavailable");
    CF<CFDictionaryRef> signing; require(SecCodeCopySigningInformation(running.value,kSecCSSigningInformation,signing.out()) == errSecSuccess, "source_identity_unavailable");
    NSDictionary *info = (__bridge NSDictionary*)signing.value;
    NSDictionary *entitlements = info[(__bridge id)kSecCodeInfoEntitlementsDict];
    require([info[(__bridge id)kSecCodeInfoTeamIdentifier] isEqual:team] &&
        ([info[(__bridge id)kSecCodeInfoFlags] unsignedIntValue] & CS_RUNTIME) &&
        ![entitlements[@"com.apple.security.app-sandbox"] boolValue], "source_identity_unavailable");
    for(NSString *key in @[@"com.apple.security.get-task-allow",@"get-task-allow",@"com.apple.security.cs.disable-library-validation",@"com.apple.security.cs.allow-dyld-environment-variables"])
        require(![entitlements[key] boolValue], "source_identity_unavailable");
    Dl_info libraryInfo = {}; require(dladdr(reinterpret_cast<const void*>(&preflight),&libraryInfo) && libraryInfo.dli_fname, "source_identity_unavailable");
    NSString *library = [NSString stringWithUTF8String:libraryInfo.dli_fname];
    NSString *expected = [bundle.bundlePath stringByAppendingPathComponent:@"Contents/app/resources/native/libAutPlaySourceAccess.dylib"];
    require([library isEqual:expected], "source_identity_unavailable"); FD libraryFd = openAbsolute(library);
    NSString *libraryRequirementText = [NSString stringWithFormat:@"anchor apple generic and identifier \"app.autplay.desktop.source-access.v1\" and certificate leaf[subject.OU] = \"%@\" and certificate leaf[field.1.2.840.113635.100.6.1.13] exists",team];
    CF<SecRequirementRef> libraryRequirement; CF<SecStaticCodeRef> libraryCode;
    require(SecRequirementCreateWithString((__bridge CFStringRef)libraryRequirementText,0,libraryRequirement.out()) == errSecSuccess &&
        SecStaticCodeCreateWithPath((__bridge CFURLRef)[NSURL fileURLWithPath:library],0,libraryCode.out()) == errSecSuccess &&
        SecStaticCodeCheckValidity(libraryCode.value,kSecCSStrictValidate | kSecCSCheckAllArchitectures,libraryRequirement.value) == errSecSuccess, "source_identity_unavailable");
}
Owner &owner(jlong token, bool work = false) {
    require(lifetime && lifetime->token == token, "source_closed");
    if(work) { require(!lifetime->fenced && !admissionFenced.load(), "source_fenced"); require(uncertainDescriptors.empty(),"source_drain_pending"); }
    return *lifetime;
}
void verify(Owner &o) {
    preflight(o.team);
    FD directory = openAbsolute(o.directory); struct stat current = statFd(directory.value);
    require(sameObject(current,o.directoryStat) && current.st_uid == getuid() && (current.st_mode & 0777) == 0700, "source_unavailable");
    struct stat lock = {}; require(fstatat(o.directoryFd.value,"source-access.lock",&lock,AT_SYMLINK_NOFOLLOW) == 0 &&
        sameObject(lock,o.lockStat) && lock.st_uid == getuid() && (lock.st_mode & 0777) == 0600 && lock.st_nlink == 1, "source_unavailable");
}
NSData *resourceIdentity(NSURL *url, const struct stat &s, NSString *kind) {
    NSString *volume = nil;
    require([url getResourceValue:&volume forKey:NSURLVolumeUUIDStringKey error:nullptr] && [volume isKindOfClass:NSString.class] && volume.length > 0);
    NSDictionary *identity = @{@"version":@1,@"volume":volume,@"inode":@(s.st_ino),
        @"birth_sec":@(s.st_birthtimespec.tv_sec),@"birth_nsec":@(s.st_birthtimespec.tv_nsec),@"scope":kind};
    NSData *encoded = [NSPropertyListSerialization dataWithPropertyList:identity format:NSPropertyListBinaryFormat_v1_0 options:0 error:nullptr];
    require(encoded && encoded.length <= 4096); return encoded;
}
bool sameIdentity(NSData *a,NSData *b) {
    // Property-list map order is not identity. The selected volume UUID/resource/birth/scope
    // are durable; st_dev is rechecked within a live batch rather than persisted across mounts.
    id left=[NSPropertyListSerialization propertyListWithData:a options:NSPropertyListImmutable format:nullptr error:nullptr];
    id right=[NSPropertyListSerialization propertyListWithData:b options:NSPropertyListImmutable format:nullptr error:nullptr];
    return [left isKindOfClass:NSDictionary.class] && [right isKindOfClass:NSDictionary.class] && [left isEqual:right];
}
NSData *makeBookmark(NSURL *url) {
    NSData *result = [url bookmarkDataWithOptions:NSURLBookmarkCreationWithoutImplicitSecurityScope includingResourceValuesForKeys:nil relativeToURL:nil error:nullptr];
    require(result && result.length > 0 && result.length <= 65536, "source_limit"); return result;
}
FD resolve(Owner &o, NSString *kind, NSData *identity, NSData *bookmark, NSURL **url, bool *stale = nullptr) {
    require([kind isEqual:@"FILE"] || [kind isEqual:@"DIRECTORY"], "source_selection_invalid");
    BOOL old = NO;
    NSURL *resolved = [NSURL URLByResolvingBookmarkData:bookmark options:NSURLBookmarkResolutionWithoutUI | NSURLBookmarkResolutionWithoutMounting | NSURLBookmarkResolutionWithoutImplicitStartAccessing
        relativeToURL:nil bookmarkDataIsStale:&old error:nullptr];
    require(resolved && resolved.isFileURL && !resolved.host.length); external(o,resolved.path);
    FD descriptor = openAbsolute(resolved.path); external(o,pathOf(descriptor.value)); struct stat s = statFd(descriptor.value);
    require(([kind isEqual:@"FILE"] ? (S_ISREG(s.st_mode) && s.st_nlink==1) : S_ISDIR(s.st_mode)) && sameIdentity(resourceIdentity(resolved,s,kind),identity));
    if(url) *url = resolved; if(stale) *stale = old; return descriptor;
}
NSString *safeLabel(NSString *name) {
    require(name.length > 0 && [name lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 256, "source_selection_invalid");
    NSCharacterSet *forbidden = [[NSCharacterSet controlCharacterSet] mutableCopy];
    require([name rangeOfCharacterFromSet:forbidden].location == NSNotFound && [name rangeOfString:@"/"].location == NSNotFound &&
        [name rangeOfString:@"\\"].location == NSNotFound && [name rangeOfCharacterFromSet:[NSCharacterSet characterSetWithRange:NSMakeRange(0x202a,5)]].location == NSNotFound &&
        [name rangeOfCharacterFromSet:[NSCharacterSet characterSetWithRange:NSMakeRange(0x2066,4)]].location == NSNotFound, "source_selection_invalid"); return name;
}
jlong selected(Owner &o, NSURL *url) {
    require(o.selections.size() < 256, "source_limit"); require(url.isFileURL && !url.host.length,"source_selection_invalid"); external(o,url.path);
    FD fd = openAbsolute(url.path); external(o,pathOf(fd.value)); struct stat s = statFd(fd.value);
    NSString *kind = S_ISDIR(s.st_mode) ? @"DIRECTORY" : @"FILE"; require(S_ISDIR(s.st_mode) || S_ISREG(s.st_mode));
    Selected value; value.url = url; value.kind = kind; value.label = safeLabel(url.lastPathComponent);
    value.identity = resourceIdentity(url,s,kind); value.bookmark = makeBookmark(url);
    // Creation/resolution must still denote the admitted resource under the same scope.
    NSURL *roundtrip = nil; FD verified = resolve(o,kind,value.identity,value.bookmark,&roundtrip);
    require(sameObject(s,statFd(verified.value))); jlong token = nextToken++; o.selections.emplace(token,value); return token;
}
Selected &selection(Owner &o,jlong token) {
    auto found = o.selections.find(token); require(found != o.selections.end(),"source_selection_invalid"); return found->second;
}
void checkSelection(Owner &o, Selected &s) { FD fd = resolve(o,s.kind,s.identity,s.bookmark,nullptr); }
int64_t elapsed(Clock::time_point start) { return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-start).count(); }
void budget(Batch &batch, bool preparing = false) {
    require(!admissionFenced.load(),"source_fenced");
    int64_t time = preparing ? elapsed(batch.activeStart) : batch.preparationMs + (batch.copying ? elapsed(batch.activeStart) : 0);
    require(time <= kActiveMs,"source_limit");
}
void validateRoot(Owner &o,Batch &b) {
    require(!o.fenced && !admissionFenced.load(),"source_fenced"); budget(b);
    external(o,b.url.path); FD live = openAbsolute(b.url.path); external(o,pathOf(live.value)); auto s = statFd(live.value);
    require(sameObject(s,b.rootStat) && sameObject(statFd(b.root.value),b.rootStat) && sameIdentity(resourceIdentity(b.url,s,b.kind),b.identity));
}
bool supported(NSString *name) {
    return [@[@"mp3",@"flac",@"m4a",@"m4b",@"aac",@"wav",@"aif",@"aiff",@"ogg",@"opus",@"alac"] containsObject:name.pathExtension.lowercaseString];
}
void addFile(Batch &b,const std::vector<std::string> &relative,NSString *name,const struct stat &s,int64_t &total) {
    if(!supported(name)) return;
    require(s.st_nlink==1,"source_internal_rejected");
    require(b.files.size() < 2048 && s.st_size > 0 && s.st_size <= kFileBytes && total <= kBatchBytes-s.st_size,"source_limit");
    require([name lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 1024 && [name rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location == NSNotFound,"source_selection_invalid");
    File file; file.relative = relative; file.name = name; file.baseline = s; total += s.st_size; b.files.push_back(file);
}
void walk(Owner &o,Batch &b,int dir,std::vector<std::string> &relative,int depth,int &visited,int64_t &total) {
    budget(b,true); require(depth <= 16,"source_limit"); struct stat before = statFd(dir);
    FD duplicate(dup(dir)); require(duplicate.value >= 0);
    DIR *listing = fdopendir(duplicate.value); require(listing); duplicate.value = -1;
    try {
        while(true) {
            errno = 0; struct dirent *entry = readdir(listing); if(!entry) { require(errno == 0); break; }
            if(!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
            require(++visited <= 10000,"source_limit"); budget(b,true);
            NSString *name = [[NSString alloc] initWithBytes:entry->d_name length:strlen(entry->d_name) encoding:NSUTF8StringEncoding]; require(name);
            struct stat s = {}; require(fstatat(dir,entry->d_name,&s,AT_SYMLINK_NOFOLLOW) == 0);
            require(!S_ISLNK(s.st_mode) && s.st_dev == b.rootStat.st_dev);
            require(relative.size()<16,"source_limit");
            relative.push_back(entry->d_name);
            if(S_ISDIR(s.st_mode)) {
                require(depth < 16,"source_limit"); FD child(openat(dir,entry->d_name,O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
                require(child.value >= 0 && sameObject(s,statFd(child.value)));
                walk(o,b,child.value,relative,depth+1,visited,total);
            } else if(S_ISREG(s.st_mode)) addFile(b,relative,name,s,total);
            else require(false);
            relative.pop_back();
        }
        require(sameFile(before,statFd(dir)));
    } catch(...) { int fd=dirfd(listing); if(closedir(listing)!=0) uncertainDescriptors.push_back(fd); throw; }
    int listedFd=dirfd(listing);
    if(closedir(listing)!=0) { uncertainDescriptors.push_back(listedFd); throw Failure{"source_drain_pending"}; }
}
Batch &batch(Owner &o,jlong token) { auto it=o.batches.find(token); require(it!=o.batches.end(),"source_closed"); return *it->second; }
File &file(Batch &b,jint index) { require(index>=0 && size_t(index)<b.files.size(),"source_selection_invalid"); return b.files[index]; }
FD openFile(Owner &o,Batch &b,File &f) {
    validateRoot(o,b);
    if(f.relative.empty()) {
        FD result(openAbsolute(b.url.path)); require(sameFile(statFd(result.value),f.baseline)); return result;
    }
    FD cursor(dup(b.root.value)); require(cursor.value>=0);
    for(size_t i=0;i<f.relative.size();++i) {
        bool last=i+1==f.relative.size();
        FD child(openat(cursor.value,f.relative[i].c_str(),O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | (last ? 0 : O_DIRECTORY)));
        require(child.value>=0); auto s=statFd(child.value); require(s.st_dev==b.rootStat.st_dev && (last ? S_ISREG(s.st_mode) : S_ISDIR(s.st_mode)));
        cursor.settle(); cursor.value=child.value; child.value=-1;
    }
    require(sameFile(statFd(cursor.value),f.baseline)); return cursor;
}
int64_t nanos(struct timespec time) {
    __int128 value=__int128(time.tv_sec)*1000000000LL+time.tv_nsec;
    require(value>=std::numeric_limits<int64_t>::min() && value<=std::numeric_limits<int64_t>::max(),"source_selection_invalid");
    return int64_t(value);
}
NSDictionary *attributes(const struct stat &s) {
    return @{@"size":@(s.st_size),@"modified_ns":@(nanos(s.st_mtimespec)),
        @"created_ns":@(nanos(s.st_birthtimespec)),
        @"key":[NSString stringWithFormat:@"%llu:%llu:%lld:%ld",(unsigned long long)s.st_dev,(unsigned long long)s.st_ino,(long long)s.st_birthtimespec.tv_sec,s.st_birthtimespec.tv_nsec]};
}
template<class F> auto call(JNIEnv *env,F fn) -> decltype(fn()) {
    using T=decltype(fn()); @autoreleasepool {
        try { std::lock_guard<std::mutex> lock(gate);
            if constexpr(std::is_void_v<T>) { fn(); require(uncertainDescriptors.empty(),"source_drain_pending"); return; }
            else { T result=fn(); require(uncertainDescriptors.empty(),"source_drain_pending"); return result; }
        }
        catch(const Failure &f) { if(env->ExceptionCheck()) env->ExceptionClear(); env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),f.code); }
        catch(...) { if(env->ExceptionCheck()) env->ExceptionClear(); env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),"source_unavailable"); }
        if constexpr(!std::is_void_v<T>) return T{};
    }
}
} // namespace

#define JNI(name) Java_app_autplay_desktop_sourceaccess_SourceAccessJni_##name
extern "C" JNIEXPORT jint JNICALL JNI(abiVersion)(JNIEnv*,jobject) { return 1; }
extern "C" JNIEXPORT void JNICALL JNI(settleInitialization)(JNIEnv *e,jobject) { call(e,[&] { require(uncertainDescriptors.empty(),"source_drain_pending"); }); }
extern "C" JNIEXPORT jlong JNICALL JNI(open)(JNIEnv *e,jobject,jint abi,jstring team,jstring managed) {
    return call(e,[&]() -> jlong {
        require(abi==1 && !lifetime,"source_busy"); require(uncertainDescriptors.empty(),"source_drain_pending"); NSString *expectedTeam=text(e,team,10); preflight(expectedTeam);
        struct passwd *account=getpwuid(getuid()); require(account && account->pw_dir,"source_unavailable");
        NSString *home=[NSString stringWithUTF8String:account->pw_dir]; FD cursor=openAbsolute(home);
        auto state=std::make_unique<Owner>(); state->team=expectedTeam; state->home=home;
        NSString *managedRoot=text(e,managed); FD managedFd=openAbsolute(managedRoot);
        FD bundleFd=openAbsolute(NSBundle.mainBundle.bundlePath);
        NSString *path=home;
        for(NSString *component in @[@"Library",@"Application Support",@"AutPlay",@"source-access-v1"]) {
            path=[path stringByAppendingPathComponent:component];
            if(mkdirat(cursor.value,component.fileSystemRepresentation,0700)!=0) require(errno==EEXIST,"source_unavailable");
            FD child(openat(cursor.value,component.fileSystemRepresentation,O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
            struct stat s=statFd(child.value); require(s.st_uid==getuid() && !(s.st_mode & 0022),"source_unavailable");
            if([component isEqual:@"AutPlay"] || [component isEqual:@"source-access-v1"]) require((s.st_mode & 0777)==0700,"source_unavailable");
            cursor.settle(); cursor.value=child.value; child.value=-1;
        }
        state->directory=path; state->directoryStat=statFd(cursor.value); state->directoryFd.value=cursor.value; cursor.value=-1;
        state->exclusions=@[pathOf(state->directoryFd.value).stringByDeletingLastPathComponent,pathOf(managedFd.value),pathOf(bundleFd.value)];
        int lock=openat(state->directoryFd.value,"source-access.lock",O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC,0600);
        require(lock>=0,"source_unavailable"); state->lock.value=lock; state->lockStat=statFd(lock);
        require(S_ISREG(state->lockStat.st_mode) && state->lockStat.st_uid==getuid() && (state->lockStat.st_mode & 0777)==0600 && state->lockStat.st_nlink==1,"source_unavailable");
        require(flock(lock,LOCK_EX | LOCK_NB)==0,"source_busy"); verify(*state);
        // No fallible temporary descriptor close remains after publishing the primitive token.
        managedFd.settle(); bundleFd.settle(); require(uncertainDescriptors.empty(),"source_drain_pending");
        state->token=nextToken++; jlong token=state->token; lifetime=std::move(state); admissionFenced=false; liveToken=token; return token;
    });
}
extern "C" JNIEXPORT void JNICALL JNI(verify)(JNIEnv *e,jobject,jlong h) { call(e,[&] { verify(owner(h)); }); }
extern "C" JNIEXPORT jstring JNICALL JNI(indexPath)(JNIEnv *e,jobject,jlong h) { return call(e,[&] { auto &o=owner(h); verify(o); return outputString(e,[o.directory stringByAppendingPathComponent:@"source-access-v1.db"]); }); }
extern "C" JNIEXPORT jlong JNICALL JNI(choose)(JNIEnv *e,jobject,jlong h) {
    // Never hold the descriptor gate while the main thread is running the chooser.
    bool admitted=call(e,[&] { auto &o=owner(h,true); verify(o); require(!o.chooser,"source_busy"); o.chooser=true; return true; });
    if(!admitted) return 0;
    __block NSURL *chosen=nil;
    void (^choose)(void)=^{ @autoreleasepool { if(admissionFenced.load()) return;
        NSOpenPanel *panel=[NSOpenPanel openPanel]; currentPanel=panel; panel.canChooseFiles=YES; panel.canChooseDirectories=YES;
        panel.allowsMultipleSelection=NO; panel.resolvesAliases=NO; panel.canCreateDirectories=NO;
        panel.title=@"Remember original source"; panel.message=@"Remember this location for explicit read-only rescans. OS permission is checked each time. Originals are never changed.";
        if([panel runModal]==NSModalResponseOK) chosen=panel.URL; currentPanel=nil; } };
    if(NSThread.isMainThread) choose(); else dispatch_sync(dispatch_get_main_queue(),choose);
    return call(e,[&]() -> jlong { auto &o=owner(h); o.chooser=false; require(!o.fenced && !admissionFenced.load(),"source_fenced"); return chosen ? selected(o,chosen) : 0; });
}
extern "C" JNIEXPORT jlong JNICALL JNI(admitDrop)(JNIEnv *e,jobject,jlong h,jstring path) { return call(e,[&] { auto &o=owner(h,true); verify(o); return selected(o,[NSURL fileURLWithPath:text(e,path)]); }); }
extern "C" JNIEXPORT jbyteArray JNICALL JNI(selectionInfo)(JNIEnv *e,jobject,jlong h,jlong t) { return call(e,[&] { auto &o=owner(h,true); auto &s=selection(o,t); checkSelection(o,s); return output(e,json(@{@"kind":s.kind,@"label":s.label}),2048); }); }
extern "C" JNIEXPORT jbyteArray JNICALL JNI(selectionIdentity)(JNIEnv *e,jobject,jlong h,jlong t) { return call(e,[&] { auto &o=owner(h,true); auto &s=selection(o,t); checkSelection(o,s); return output(e,s.identity,4096); }); }
extern "C" JNIEXPORT jbyteArray JNICALL JNI(selectionBookmark)(JNIEnv *e,jobject,jlong h,jlong t) { return call(e,[&] { auto &o=owner(h,true); auto &s=selection(o,t); checkSelection(o,s); return output(e,s.bookmark,65536); }); }
extern "C" JNIEXPORT void JNICALL JNI(discardSelection)(JNIEnv *e,jobject,jlong h,jlong t) { call(e,[&] { owner(h).selections.erase(t); }); }
extern "C" JNIEXPORT jbyteArray JNICALL JNI(refreshedBookmark)(JNIEnv *e,jobject,jlong h,jstring kind,jbyteArray id,jbyteArray bm) {
    return call(e,[&]() -> jbyteArray { auto &o=owner(h,true); verify(o); NSString *k=text(e,kind,16); NSData *identity=bytes(e,id,4096); NSURL *url=nil; bool stale=false;
        FD descriptor=resolve(o,k,identity,bytes(e,bm,65536),&url,&stale); if(!stale) return nullptr;
        NSData *fresh=makeBookmark(url); FD roundtrip=resolve(o,k,identity,fresh,nullptr); require(sameObject(statFd(descriptor.value),statFd(roundtrip.value)));
        return output(e,fresh,65536); });
}
extern "C" JNIEXPORT jlong JNICALL JNI(scan)(JNIEnv *e,jobject,jlong h,jstring kind,jbyteArray id,jbyteArray bm) {
    return call(e,[&]() -> jlong { auto &o=owner(h,true); verify(o); require(o.batches.size()<8,"source_busy");
        auto b=std::make_unique<Batch>(); b->kind=text(e,kind,16); b->identity=bytes(e,id,4096); b->activeStart=Clock::now(); NSURL *url=nil;
        FD root=resolve(o,b->kind,b->identity,bytes(e,bm,65536),&url); b->url=url; b->root.value=root.value; root.value=-1;
        b->rootStat=statFd(b->root.value); int visited=1; int64_t total=0; std::vector<std::string> relative;
        if(S_ISDIR(b->rootStat.st_mode)) walk(o,*b,b->root.value,relative,0,visited,total);
        else addFile(*b,relative,url.lastPathComponent,b->rootStat,total);
        b->preparationMs=elapsed(b->activeStart); budget(*b,true); validateRoot(o,*b);
        jlong token=nextToken++; o.batches.emplace(token,std::move(b)); return token; });
}
extern "C" JNIEXPORT jbyteArray JNICALL JNI(batchInfo)(JNIEnv *e,jobject,jlong h,jlong t) { return call(e,[&] { auto &o=owner(h,true); auto &b=batch(o,t); validateRoot(o,b); NSMutableArray *files=[NSMutableArray array]; for(auto &f:b.files) [files addObject:@{@"name":f.name}]; return output(e,json(@{@"preparation_ms":@(b.preparationMs),@"files":files})); }); }
extern "C" JNIEXPORT jbyteArray JNICALL JNI(attributes)(JNIEnv *e,jobject,jlong h,jlong t,jint index) { return call(e,[&] { auto &o=owner(h,true); auto &b=batch(o,t); auto &f=file(b,index); FD descriptor=openFile(o,b,f); return output(e,json(attributes(statFd(descriptor.value))),4096); }); }
extern "C" JNIEXPORT jlong JNICALL JNI(openFile)(JNIEnv *e,jobject,jlong h,jlong t,jint index) { return call(e,[&]() -> jlong { auto &o=owner(h,true); require(o.reads.size()<8,"source_busy"); auto &b=batch(o,t); auto &f=file(b,index);
    if(!b.copying) { b.copying=true; b.activeStart=Clock::now(); }
    FD fd=openFile(o,b,f); auto r=std::make_unique<Read>(); r->descriptor.value=fd.value; fd.value=-1; r->batch=t; r->index=index;
    jlong token=nextToken++; o.reads.emplace(token,std::move(r)); return token; }); }
extern "C" JNIEXPORT jbyteArray JNICALL JNI(read)(JNIEnv *e,jobject,jlong h,jlong t,jint maximum) {
    return call(e,[&]() -> jbyteArray { auto &o=owner(h,true); auto it=o.reads.find(t); require(it!=o.reads.end(),"source_closed"); require(maximum>0 && maximum<=65536,"source_limit");
        auto &r=*it->second; auto &b=batch(o,r.batch); auto &f=file(b,jint(r.index)); validateRoot(o,b);
        FD pathname=openFile(o,b,f); require(sameFile(statFd(r.descriptor.value),f.baseline));
        NSMutableData *data=[NSMutableData dataWithLength:maximum]; ssize_t count=::read(r.descriptor.value,data.mutableBytes,maximum);
        require(count>=0 && r.offset+count<=f.baseline.st_size); r.offset+=count;
        require(sameFile(statFd(r.descriptor.value),f.baseline)); validateRoot(o,b);
        if(count==0) { require(r.offset==f.baseline.st_size); return nullptr; }
        data.length=NSUInteger(count); return output(e,data,65536); });
}
extern "C" JNIEXPORT void JNICALL JNI(closeFile)(JNIEnv *e,jobject,jlong h,jlong t) { call(e,[&] { auto &o=owner(h); auto it=o.reads.find(t); require(it!=o.reads.end(),"source_closed"); it->second->descriptor.settle(); o.reads.erase(it); }); }
extern "C" JNIEXPORT void JNICALL JNI(closeBatch)(JNIEnv *e,jobject,jlong h,jlong t) { call(e,[&] { auto &o=owner(h); auto &b=batch(o,t); for(auto &r:o.reads) require(r.second->batch!=t,"source_drain_pending"); b.root.settle(); o.batches.erase(t); }); }
extern "C" JNIEXPORT void JNICALL JNI(fence)(JNIEnv *e,jobject,jlong h) {
    if(h>0 && liveToken.load()==h) { admissionFenced=true; dispatch_async(dispatch_get_main_queue(),^{ [currentPanel cancel:nil]; }); return; }
    call(e,[&] { owner(h).fenced=true; admissionFenced=true; });
}
extern "C" JNIEXPORT void JNICALL JNI(resume)(JNIEnv *e,jobject,jlong h) { call(e,[&] { auto &o=owner(h); verify(o); require(o.batches.empty() && o.reads.empty() && o.selections.empty() && !o.chooser,"source_drain_pending"); o.fenced=false; admissionFenced=false; }); }
extern "C" JNIEXPORT void JNICALL JNI(close)(JNIEnv *e,jobject,jlong h) { call(e,[&] { auto &o=owner(h); o.fenced=true; require(uncertainDescriptors.empty() && o.batches.empty() && o.reads.empty() && o.selections.empty() && !o.chooser,"source_drain_pending");
    // Close the actual descriptors before dropping the unique lifetime. Never unlink its lock.
    o.directoryFd.settle(); o.lock.settle(); liveToken=0; lifetime.reset(); }); }
