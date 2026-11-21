package app.autplay

import androidx.test.platform.app.InstrumentationRegistry

internal fun saveUiScreenshot(name: String) {
    if (InstrumentationRegistry.getArguments().getString("captureHome") != "true") return
    val instrumentation = InstrumentationRegistry.getInstrumentation()
    val image = androidx.test.core.app.takeScreenshot()
    java.io.File(instrumentation.targetContext.cacheDir, "$name.png").outputStream().use {
        check(image.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it))
    }
    image.recycle()
}
