package app.autplay

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview

internal const val BOOTSTRAP_LABEL = "AutPlay"

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AutPlayBootstrap()
        }
    }
}

@Composable
internal fun AutPlayBootstrap() {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            Box(contentAlignment = Alignment.Center) {
                Text(text = BOOTSTRAP_LABEL)
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun AutPlayBootstrapPreview() {
    AutPlayBootstrap()
}
