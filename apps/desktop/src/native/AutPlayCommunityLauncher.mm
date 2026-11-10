// Native signature/seal gate runs before loading any JVM, jar or application JNI.
#include "AutPlayCommunityIdentity.h"
#include <unistd.h>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
extern char **environ;

int main(int argc, char **argv) {
    @autoreleasepool {
        try {
            NSBundle *bundle = NSBundle.mainBundle;
            NSURL *url = bundle.bundleURL;
            autplay_community::check([url.path isEqual:url.URLByResolvingSymlinksInPath.path]);
            autplay_community::validate(url, @"app.autplay.desktop");
            NSURL *jvm = [url URLByAppendingPathComponent:@"Contents/MacOS/AutPlayJVM"];
            autplay_community::validate(jvm, @"app.autplay.desktop", true);
            // Hardened native bootstrap has no JIT/library-validation exceptions. Do not let
            // inherited JVM or loader configuration bypass the seal after crossing into Java.
            std::vector<std::string> remove;
            for (char **entry = environ; *entry; ++entry) {
                std::string key(*entry, strcspn(*entry, "="));
                if (key.rfind("DYLD_", 0) == 0 || key.rfind("JAVA_", 0) == 0 ||
                    key.rfind("JDK_", 0) == 0 || key == "_JAVA_OPTIONS" || key == "CLASSPATH") remove.push_back(key);
            }
            for (const auto &key : remove) unsetenv(key.c_str());
            std::vector<char *> forwarded;
            forwarded.push_back(const_cast<char *>(jvm.path.fileSystemRepresentation));
            for (int index = 1; index < argc; ++index) forwarded.push_back(argv[index]);
            forwarded.push_back(nullptr);
            execv(forwarded[0], forwarded.data());
        } catch (...) { }
        fputs("AUTPLAY_PACKAGE_IDENTITY_REJECTED\n", stderr);
        return 78;
    }
}
