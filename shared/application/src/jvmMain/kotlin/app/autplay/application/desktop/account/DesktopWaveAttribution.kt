package app.autplay.application.desktop.account

import app.autplay.application.desktop.DesktopWaveRecommendation
import kotlinx.serialization.json.*

fun DesktopWaveRecommendation.attribution(): JsonObject = buildJsonObject {
    put("recommendation_request_id", requestId)
    put("recording_id", recordingId)
    put("source_rank", sourceRank)
    section?.let { put("section_key", it) }
    put("source", "online")
    put("surface", "personal_wave")
}

fun DesktopWaveRecommendation.queueDocument(): JsonObject {
    require(context in setOf("GENERAL", "WORK", "SLEEP"))
    return JsonObject(attribution() + ("context" to JsonPrimitive(context)))
}

fun restoredWaveRecommendation(row: JsonObject): DesktopWaveRecommendation {
    AccountDomainWire.attribution(row)
    val context = row.getValue("context").jsonPrimitive.content
    require(context in setOf("GENERAL", "WORK", "SLEEP"))
    return DesktopWaveRecommendation(row.getValue("recommendation_request_id").jsonPrimitive.content,
        row.getValue("recording_id").jsonPrimitive.content, row.getValue("source_rank").jsonPrimitive.int,
        row["section_key"]?.jsonPrimitive?.content, context)
}
