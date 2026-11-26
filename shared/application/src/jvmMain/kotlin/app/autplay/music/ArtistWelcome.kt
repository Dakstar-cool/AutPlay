package app.autplay.application.library

import kotlinx.serialization.json.*

data class WelcomeArtist(val id: String, val name: String, val track: String) {
    companion object {
        fun browse(parent: String?, query: String, seen: List<String>) = buildJsonObject {
            parent?.let { put("parent", it) }
            put("query", query)
            put("seen", JsonArray(seen.map(::JsonPrimitive)))
        }

        fun choices(selected: List<String>) = buildJsonObject {
            put("artists", JsonArray(selected.map(::JsonPrimitive)))
        }

        fun decode(page: JsonObject): List<WelcomeArtist> = page.getValue("artists").jsonArray.map {
            val row = it.jsonObject
            WelcomeArtist(row.getValue("id").jsonPrimitive.content, row.getValue("name").jsonPrimitive.content,
                row.getValue("track").jsonPrimitive.content)
        }
    }
}
