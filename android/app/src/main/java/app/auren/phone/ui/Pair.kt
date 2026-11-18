package app.auren.phone.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import app.auren.phone.Pairing
import app.auren.phone.Remote
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.launch
import java.util.concurrent.Executors

/**
 * Pairing: run `auren phone pair` on the PC, then scan its QR code (or paste the auren://pair link it
 * prints). A link that arrives from outside (a deep link) only fills the field: pairing always takes a tap.
 */
@Composable
fun PairScreen(prefill: String?, onPaired: () -> Unit, onBack: (() -> Unit)?) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var text by rememberSaveable { mutableStateOf(prefill.orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    var working by remember { mutableStateOf(false) }
    var camera by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(ctx, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    var scanning by rememberSaveable { mutableStateOf(prefill == null) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { camera = it }

    fun pair(raw: String) {
        if (working) return
        val p = Pairing.parse(raw)
        if (p == null) {
            error = "That isn't an Auren pairing code. Run auren phone pair on the PC and scan the code it shows."
            return
        }
        working = true
        error = null
        scope.launch {
            val why = Remote.pair(p)
            working = false
            if (why == null) onPaired() else error = why
        }
    }

    Column(
        Modifier.fillMaxSize().imePadding().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Caps("Auren", Au.Gold, size = 13)
            Caps("  · pair", Au.Faint, size = 13)
            if (onBack != null) {
                Box(Modifier.weight(1f))
                QuietButton("Back", onClick = onBack)
            }
        }
        Box(Modifier.fillMaxWidth().height(150.dp)) { Visor(Mood.IDLE, 0f, Modifier.fillMaxSize()) }
        Text("Talk to your PC from here.", color = Au.Text, fontSize = 20.sp)
        Text(
            "On the PC, run auren phone pair. Scan the code it shows, or paste the link under it. " +
                "Both devices need Tailscale.",
            color = Au.Dim, fontSize = 14.sp, lineHeight = 20.sp, modifier = Modifier.padding(top = 6.dp),
        )
        Text("auren phone pair", color = Au.Gold, fontFamily = FontFamily.Monospace, fontSize = 14.sp,
            modifier = Modifier.padding(top = 12.dp).clip(RoundedCornerShape(8.dp)).background(Au.Glass)
                .border(1.dp, Au.Edge, RoundedCornerShape(8.dp)).padding(horizontal = 12.dp, vertical = 8.dp))

        Section("Scan")
        when {
            !scanning -> QuietButton("Scan the QR code", Modifier.fillMaxWidth()) { scanning = true }
            !camera -> Column {
                Text("Auren needs the camera only to read the pairing code.", color = Au.Dim, fontSize = 13.sp)
                GoldButton("Allow camera", Modifier.padding(top = 8.dp)) { ask.launch(Manifest.permission.CAMERA) }
            }
            else -> Box(
                Modifier.fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(18.dp))
                    .border(1.dp, Au.Gold.copy(alpha = 0.5f), RoundedCornerShape(18.dp)),
            ) {
                QrScanner(Modifier.fillMaxSize()) { found ->
                    if (!working && Pairing.parse(found) != null) {
                        text = found
                        scanning = false
                        pair(found)
                    }
                }
            }
        }

        Section("Or paste")
        OutlinedTextField(
            value = text, onValueChange = { text = it; error = null },
            placeholder = { Text("auren://pair?…", color = Au.Faint) },
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp), maxLines = 3,
            textStyle = androidx.compose.ui.text.TextStyle(fontFamily = FontFamily.Monospace, fontSize = 13.sp, color = Au.Text),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Au.Gold.copy(alpha = 0.7f), unfocusedBorderColor = Au.Edge,
                focusedContainerColor = Au.Glass, unfocusedContainerColor = Au.Glass, cursorColor = Au.Gold,
            ),
        )
        error?.let {
            Text(it, color = Au.Red, fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.padding(top = 10.dp))
        }
        Row(Modifier.padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            GoldButton("Pair", enabled = text.isNotBlank() && !working) { pair(text) }
            if (working) CircularProgressIndicator(color = Au.Gold, strokeWidth = 2.dp, modifier = Modifier.padding(4.dp))
        }
    }
}

/** The back camera, looking for QR codes. Calls [onCode] with each one it reads. */
@Composable
fun QrScanner(modifier: Modifier, onCode: (String) -> Unit) {
    val ctx = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val found by rememberUpdatedState(onCode)
    val preview = remember { PreviewView(ctx).apply { scaleType = PreviewView.ScaleType.FILL_CENTER } }
    DisposableEffect(owner) {
        val executor = Executors.newSingleThreadExecutor()
        val scanner = BarcodeScanning.getClient(BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build())
        val future = ProcessCameraProvider.getInstance(ctx)
        future.addListener({
            val provider = future.get()
            val p = Preview.Builder().build().also { it.surfaceProvider = preview.surfaceProvider }
            val analysis = ImageAnalysis.Builder().setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
            analysis.setAnalyzer(executor) { proxy ->
                @androidx.annotation.OptIn(androidx.camera.core.ExperimentalGetImage::class)
                val image = proxy.image
                if (image == null) {
                    proxy.close()
                    return@setAnalyzer
                }
                scanner.process(InputImage.fromMediaImage(image, proxy.imageInfo.rotationDegrees))
                    .addOnSuccessListener { codes ->
                        codes.firstNotNullOfOrNull { it.rawValue }?.let { v -> ContextCompat.getMainExecutor(ctx).execute { found(v) } }
                    }
                    .addOnCompleteListener { proxy.close() }
            }
            runCatching {
                provider.unbindAll()
                provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, p, analysis)
            }
        }, ContextCompat.getMainExecutor(ctx))
        onDispose {
            runCatching { future.get().unbindAll() }
            scanner.close()
            executor.shutdown()
        }
    }
    AndroidView({ preview }, modifier)
}
