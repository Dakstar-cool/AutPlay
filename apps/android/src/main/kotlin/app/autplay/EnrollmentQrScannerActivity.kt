package app.autplay

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
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
    private lateinit var hint: TextView

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCamera() else hint.setText(R.string.qr_scan_camera_permission_required)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        val previewView = PreviewView(this)
        hint = TextView(this).apply {
            setText(R.string.qr_scan_hint)
            setTextColor(Color.WHITE)
            setBackgroundColor(0xCC000000.toInt())
            setPadding(16.dp, 16.dp, 16.dp, 16.dp)
        }
        val cancel = Button(this).apply {
            setText(R.string.qr_scan_cancel)
            setOnClickListener { finish() }
        }
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(previewView, FrameLayout.LayoutParams(-1, -1))
            addView(hint, FrameLayout.LayoutParams(-1, -2, Gravity.TOP))
            addView(cancel, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
                bottomMargin = 24.dp
            })
        })
        this.previewView = previewView
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
            }.onFailure { hint.setText(R.string.qr_scan_camera_unavailable) }
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

    private val Int.dp: Int get() = (this * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_PAYLOAD = "app.autplay.QR_PAYLOAD"
        private const val MAX_QR_PAYLOAD_CHARS = 16_384
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
