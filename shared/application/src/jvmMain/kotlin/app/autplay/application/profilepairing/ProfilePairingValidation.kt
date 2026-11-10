package app.autplay.application.profilepairing

import java.util.UUID

fun requireCanonicalUuid(value: String): Unit { require(runCatching { UUID.fromString(value) }.getOrNull()?.toString() == value) }
fun requireSha256(value: String): Unit { require(Regex("^[0-9a-f]{64}$").matches(value)) }
