package app.autplay

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.AutPlayTokens
import com.google.zxing.BinaryBitmap
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Live, in-app QR reader. No image or decoded invitation is saved to disk or the clipboard. */
class EnrollmentQrScannerActivity : ComponentActivity() {
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private val returned = AtomicBoolean(false)
    private var cameraProvider: ProcessCameraProvider? = null
    private var hintRes by mutableIntStateOf(R.string.qr_scan_hint)

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCamera() else hintRes = R.string.qr_scan_camera_permission_required
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        previewView = PreviewView(this)
        val settingsStore = applicationNonSecretSettingsStore(applicationContext)
        setContent {
            val loadedSettings by settingsStore.settings.collectAsState(initial = null)
            val settings = loadedSettings
            Box(Modifier.fillMaxSize().background(Color.Black)) {
                AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
                if (settings != null) AppLanguageProvider(settings.appLanguage) {
                    AutPlayTheme(appearanceFrom(settings)) {
                        QrScannerOverlay(hintRes, onCancel = { finish() })
                    }
                }
            }
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    private lateinit var previewView: PreviewView

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            runCatching {
                val provider = future.get()
                cameraProvider = provider
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                    .build()
                analysis.setAnalyzer(cameraExecutor) { image -> analyze(image) }
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            }.onFailure { hintRes = R.string.qr_scan_camera_unavailable }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(image: ImageProxy) {
        try {
            if (returned.get()) return
            val payload = runCatching { decodeQrFrame(image) }.getOrNull() ?: return
            if (payload.length !in 2..MAX_QR_PAYLOAD_CHARS || !returned.compareAndSet(false, true)) return
            runOnUiThread {
                setResult(Activity.RESULT_OK, Intent().putExtra(EXTRA_PAYLOAD, payload))
                finish()
            }
        } finally {
            image.close()
        }
    }

    override fun onDestroy() {
        cameraProvider?.unbindAll()
        cameraExecutor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_PAYLOAD = "app.autplay.QR_PAYLOAD"
        private const val MAX_QR_PAYLOAD_CHARS = 16_384
    }
}

@Composable
internal fun QrScannerOverlay(hintRes: Int, onCancel: () -> Unit) {
    app.autplay.ui.AutPlaySystemBarIconAppearance(
        useDarkStatusBarIcons = false,
        useDarkNavigationBarIcons = false,
    )
    Column(
        Modifier.fillMaxSize().systemBarsPadding().padding(AutPlayTokens.dimensions.screenPadding),
        verticalArrangement = Arrangement.SpaceBetween,
    ) {
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = MaterialTheme.shapes.extraLarge,
            color = AutPlayTokens.colors.glassSurface,
            contentColor = MaterialTheme.colorScheme.onSurface,
            border = BorderStroke(1.dp, AutPlayTokens.colors.glassBorder),
        ) {
            Text(stringResource(hintRes), Modifier.padding(20.dp), style = MaterialTheme.typography.bodyLarge)
        }
        FilledTonalButton(onClick = onCancel, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
            Text(stringResource(R.string.qr_scan_cancel))
        }
    }
}

/** Extracts only the luminance plane; camera frames never become photos or persisted files. */
internal fun decodeQrFrame(image: ImageProxy): String? {
    val width = image.width
    val height = image.height
    if (width !in 1..4096 || height !in 1..4096 || width.toLong() * height > 4_194_304) return null
    val plane = image.planes.firstOrNull() ?: return null
    val buffer = plane.buffer
    val luma = ByteArray(width * height)
    for (y in 0 until height) {
        val row = y * plane.rowStride
        for (x in 0 until width) {
            val index = row + x * plane.pixelStride
            if (index >= buffer.limit()) return null
            luma[y * width + x] = buffer.get(index)
        }
    }
    return decodeQrLuminance(luma, width, height)
}

internal fun decodeQrLuminance(luma: ByteArray, width: Int, height: Int): String {
    require(width in 1..4096 && height in 1..4096 && luma.size == width * height)
    val source = PlanarYUVLuminanceSource(luma, width, height, 0, 0, width, height, false)
    return QRCodeReader().decode(BinaryBitmap(HybridBinarizer(source))).text
}
