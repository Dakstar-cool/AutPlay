// Native signature/seal gate runs before loading any JVM, jar or application JNI.
#include "AutPlayCommunityIdentity.h"
#include <unistd.h>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
#include <dlfcn.h>
#include <jni.h>
extern char **environ;

using Launch = int (*)(int, char **, int, const char **, int, const char **,
    const char *, const char *, const char *, const char *, jboolean, jboolean, jboolean, jint);
static Launch launch = nullptr;
static std::vector<std::string> arguments;
static std::vector<char *> pointers;
static int startJvm() {
    return launch(int(pointers.size() - 1), pointers.data(), 0, nullptr, 0, nullptr,
        "17.0.20+8-LTS", "17.0.20", "AutPlay", "AutPlay", JNI_FALSE, JNI_FALSE, JNI_FALSE, 0);
}

int main(int argc, char **argv) {
    // The pinned JDK's macOS JLI re-enters main on its JVM thread, just as jpackage does.
    if (launch) return startJvm();
    @autoreleasepool {
        try {
            NSBundle *bundle = NSBundle.mainBundle;
            NSURL *url = bundle.bundleURL;
            autplay_community::check([url.path isEqual:url.URLByResolvingSymlinksInPath.path]);
            autplay_community::validate(url, @"app.autplay.desktop", true);
            // This is the only account-authorized executable. Do not ship a second JVM
            // launcher that could bypass these checks by being executed directly.
            std::vector<std::string> remove;
            for (char **entry = environ; *entry; ++entry) {
                std::string key(*entry, strcspn(*entry, "="));
                if (key.rfind("DYLD_", 0) == 0 || key.rfind("JAVA_", 0) == 0 ||
                    key.rfind("JDK_", 0) == 0 || key.rfind("_JAVA_", 0) == 0 || key == "CLASSPATH") remove.push_back(key);
            }
            for (const auto &key : remove) unsetenv(key.c_str());
            NSString *appDir = [url.path stringByAppendingPathComponent:@"Contents/app"];
            NSString *cfg = [appDir stringByAppendingPathComponent:@"AutPlay.cfg"];
            NSNumber *length = [NSFileManager.defaultManager attributesOfItemAtPath:cfg error:nullptr][NSFileSize];
            autplay_community::check(length && length.unsignedLongLongValue <= 1048576);
            NSString *text = [NSString stringWithContentsOfFile:cfg encoding:NSUTF8StringEncoding error:nullptr];
            autplay_community::check(text != nil && argc <= 128);
            NSMutableArray<NSString *> *classpath = [NSMutableArray array], *options = [NSMutableArray array];
            NSString *mainClass = nil, *section = nil;
            for (NSString *raw in [text componentsSeparatedByCharactersInSet:NSCharacterSet.newlineCharacterSet]) {
                NSString *line = [raw stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
                if ([line hasPrefix:@"["]) { section = line; continue; }
                NSRange split = [line rangeOfString:@"="]; if (split.location == NSNotFound) continue;
                NSString *key = [line substringToIndex:split.location], *value = [line substringFromIndex:split.location + 1];
                value = [value stringByReplacingOccurrencesOfString:@"$APPDIR" withString:appDir];
                value = [value stringByReplacingOccurrencesOfString:@"$BINDIR" withString:[url.path stringByAppendingPathComponent:@"Contents/MacOS"]];
                value = [value stringByReplacingOccurrencesOfString:@"$ROOTDIR" withString:url.path];
                if ([section isEqual:@"[Application]"] && [key isEqual:@"app.classpath"]) [classpath addObject:value];
                if ([section isEqual:@"[Application]"] && [key isEqual:@"app.mainclass"]) { autplay_community::check(mainClass == nil); mainClass = value; }
                if ([section isEqual:@"[JavaOptions]"] && [key isEqual:@"java-options"]) [options addObject:value];
            }
            autplay_community::check(mainClass.length > 0 && classpath.count > 0 && options.count <= 128);
            arguments.emplace_back(bundle.executableURL.path.fileSystemRepresentation);
            arguments.emplace_back("-classpath"); arguments.emplace_back([classpath componentsJoinedByString:@":"].UTF8String);
            for (NSString *option in options) arguments.emplace_back(option.UTF8String);
            arguments.emplace_back(std::string("-Djpackage.app-path=") + bundle.executableURL.path.fileSystemRepresentation);
            arguments.emplace_back(mainClass.UTF8String);
            for (int index = 1; index < argc; ++index) {
                autplay_community::check(strlen(argv[index]) <= 4096); arguments.emplace_back(argv[index]);
            }
            for (auto &argument : arguments) pointers.push_back(argument.data());
            pointers.push_back(nullptr);
            NSString *jli = [url.path stringByAppendingPathComponent:@"Contents/runtime/Contents/Home/lib/libjli.dylib"];
            void *library = dlopen(jli.fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
            autplay_community::check(library != nullptr);
            launch = reinterpret_cast<Launch>(dlsym(library, "JLI_Launch"));
            autplay_community::check(launch != nullptr);
            return startJvm();
        } catch (...) { }
        fputs("AUTPLAY_PACKAGE_IDENTITY_REJECTED\n", stderr);
        return 78;
    }
}
