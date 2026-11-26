package app.autplay.work

import androidx.work.Data
import androidx.work.workDataOf
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

internal data class CatalogDownloadProgress(
    val phase: String,
    val total: Int,
    val pending: Int,
    val saved: Int,
    val unavailable: Int,
) {
    val processed: Int get() = when (phase) {
        "inspect", "preview" -> total - pending
        else -> saved + unavailable
    }
    val savedFraction: Double? get() = if (phase == "discovery" || total <= 0) null else
        saved.coerceIn(0, total).toDouble() / total

    fun asData() = workDataOf("phase" to phase, "total" to total, "pending" to pending,
        "saved" to saved, "unavailable" to unavailable)

    companion object {
        fun from(data: Data): CatalogDownloadProgress? = data.getString("phase")?.let { phase ->
            CatalogDownloadProgress(phase, data.getInt("total", 0), data.getInt("pending", 0),
                data.getInt("saved", 0), data.getInt("unavailable", 0))
        }

        fun from(state: JsonObject): CatalogDownloadProgress? {
            val phase = state["phase"]?.jsonPrimitive?.contentOrNull ?: return null
            val counts = state["counts"]?.jsonObject ?: return null
            fun count(key: String) = counts[key]?.jsonPrimitive?.intOrNull ?: 0
            val total = state["total"]?.jsonPrimitive?.intOrNull ?: counts.values.sumOf { it.jsonPrimitive.intOrNull ?: 0 }
            return CatalogDownloadProgress(phase, total, count("pending"), count("vault"), count("missing") + count("failed"))
        }
    }
}
