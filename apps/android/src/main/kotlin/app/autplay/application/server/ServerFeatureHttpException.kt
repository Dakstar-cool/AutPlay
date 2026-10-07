package app.autplay.application.server

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/** Keeps the legacy worker message while exposing bounded public error codes to product UI. */
class ServerFeatureHttpException private constructor(
    val status: Int,
    val errorCode: String?,
    val retryable: Boolean?,
) : IllegalStateException("SERVER_HTTP_$status") {
    companion object {
        internal fun from(status: Int, body: String?): ServerFeatureHttpException {
            val error = body?.takeIf { it.length <= 8_192 }?.let { value ->
                runCatching { (Json.parseToJsonElement(value) as? JsonObject)?.get("error") as? JsonObject }.getOrNull()
            }
            val code = (error?.get("code") as? JsonPrimitive)?.takeIf { it.isString }?.content
                ?.takeIf { it.matches(Regex("[a-z][a-z0-9_]{0,99}")) }
            val retry = (error?.get("retryable") as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull
            return ServerFeatureHttpException(status, code, retry)
        }
    }
}
