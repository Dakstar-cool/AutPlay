package app.autplay.application.desktop.account

import kotlinx.serialization.json.*

data class AccountDomainReferences(val trackServerId:AccountUuid?=null,val playlistServerId:AccountUuid?=null,val beforeEntryServerId:AccountUuid?=null)

/** Supported v1 commands. Queue/checkpoint/policy descriptors never enter this event language. */
object AccountDomainWire {
    fun type(command: AccountDomainCommand): Pair<String, String> = when (command) {
        is AccountDomainCommand.PatchTrack -> "USER_TRACK_REF_PATCHED" to "USER_TRACK_REF"
        is AccountDomainCommand.Library -> "LIBRARY_ENTRY_UPSERTED" to "LIBRARY_ENTRY"
        is AccountDomainCommand.Preference -> "USER_TRACK_PREFERENCE_SET" to "USER_TRACK_PREFERENCE"
        is AccountDomainCommand.Playlist -> (if (command.create) "PLAYLIST_CREATED" else "PLAYLIST_METADATA_PATCHED") to "PLAYLIST"
        is AccountDomainCommand.Entry -> (if (command.move) "PLAYLIST_ENTRY_MOVED" else "PLAYLIST_ENTRY_UPSERTED") to "PLAYLIST_ENTRY"
        is AccountDomainCommand.Delete -> {
            require(command.aggregateType in setOf("USER_TRACK_REF", "LIBRARY_ENTRY", "PLAYLIST", "PLAYLIST_ENTRY"))
            "AGGREGATE_DELETED" to command.aggregateType
        }
        is AccountDomainCommand.Listen -> "LISTENING_EVENT_RECORDED" to "LISTENING_EVENT"
        is AccountDomainCommand.Interaction -> command.payload.getValue("interaction_type").jsonPrimitive.content to "USER_INTERACTION_EVENT"
    }
    fun payload(command: AccountDomainCommand,references:AccountDomainReferences=AccountDomainReferences()): JsonObject = when (command) {
        is AccountDomainCommand.PatchTrack -> command.fields.also {
            require(it.isNotEmpty() && it.keys.all { key -> key in setOf("title", "artist", "album") })
            it.values.forEach { value -> nullableText(value, 4_096) }
        }
        is AccountDomainCommand.Library -> buildJsonObject {
            put(if(references.trackServerId==null)"local_user_track_ref_id" else "user_track_ref_id",(references.trackServerId?:command.trackId).value)
            // Existing upsert removes on any non-null integer and restores on explicit null.
            put("removed_at_ms", if (command.removed) JsonPrimitive(0) else JsonNull)
        }
        is AccountDomainCommand.Preference -> buildJsonObject {
            require(command.value in setOf("NEUTRAL", "LIKED", "DISLIKED"))
            put("local_user_track_ref_id", command.trackId.value); put("server_user_track_ref_id",references.trackServerId?.value?.let(::JsonPrimitive)?:JsonNull)
            put("preference", command.value); put("excluded_from_taste", command.excludedFromTaste)
            command.attribution?.let { attribution(it); put("attribution", it) }
        }
        is AccountDomainCommand.Playlist -> buildJsonObject {
            text(command.name, 500); nullableText(command.description?.let(::JsonPrimitive) ?: JsonNull, 2_000)
            put("name", command.name); put("description", command.description?.let(::JsonPrimitive) ?: JsonNull)
        }
        is AccountDomainCommand.Entry -> buildJsonObject {
            text(command.positionKey, 128); put(if(references.playlistServerId==null)"local_playlist_id" else "playlist_id",(references.playlistServerId?:command.playlistId).value)
            put(if(references.trackServerId==null)"local_user_track_ref_id" else "user_track_ref_id",(references.trackServerId?:command.trackId).value)
            if(command.serverOrdering)put(if(references.beforeEntryServerId==null)"before_local_playlist_entry_id" else "before_playlist_entry_id",(references.beforeEntryServerId?:command.beforeEntryId)?.value?.let(::JsonPrimitive)?:JsonNull)
            else put("position_key", command.positionKey)
            command.attribution?.let { attribution(it); put("attribution", it) }
        }
        is AccountDomainCommand.Delete -> buildJsonObject { type(command) }
        is AccountDomainCommand.Listen -> {
            listening(command.payload)
            if(references.trackServerId==null)command.payload else JsonObject(command.payload+("server_user_track_ref_id" to JsonPrimitive(references.trackServerId.value)))
        }
        is AccountDomainCommand.Interaction -> command.payload.also(::interaction)
    }
    fun listening(p: JsonObject): Unit {
        val required = setOf("interaction_type", "local_user_track_ref_id", "server_user_track_ref_id", "recording_id",
            "played_ms", "track_duration_ms", "completion_ratio", "event_origin", "context", "explicit_feedback", "excluded_from_taste", "recommendation")
        require(p.keys.containsAll(required) && p["interaction_type"] == JsonPrimitive("LISTENING_EVENT_RECORDED"))
        id(p.getValue("local_user_track_ref_id")); listOf("server_user_track_ref_id", "recording_id").forEach { if (p[it] != JsonNull) id(p.getValue(it)) }
        number(p.getValue("played_ms"), 0, 604_800_000)
        if (p["track_duration_ms"] != JsonNull) number(p.getValue("track_duration_ms"), 1, 604_800_000)
        if (p["completion_ratio"] != JsonNull) {
            val v = p["completion_ratio"] as? JsonPrimitive ?: invalid()
            require(!v.isString && v.doubleOrNull?.let { it.isFinite() && it in 0.0..1.0 } == true)
        }
        require(p["event_origin"]?.jsonPrimitive?.content in setOf("ORGANIC", "RECOMMENDED", "PLAYLIST", "SEARCH", "WAVE"))
        require(p["context"]?.jsonPrimitive?.content in setOf("GENERAL", "WORKOUT", "CYCLING", "WORK", "SLEEP", "PARTY"))
        require(p["explicit_feedback"]?.jsonPrimitive?.content in setOf("NONE", "LIKE", "DISLIKE"))
        val excluded = p["excluded_from_taste"] as? JsonPrimitive ?: invalid(); require(!excluded.isString && excluded.booleanOrNull != null)
        if (p["recommendation"] != JsonNull) attribution(p["recommendation"] as? JsonObject ?: invalid())
        require(p["event_origin"] != JsonPrimitive("RECOMMENDED") || p["recommendation"] != JsonNull)
    }
    fun attribution(p: JsonObject): Unit {
        id(p.getValue("recommendation_request_id")); id(p.getValue("recording_id")); number(p.getValue("source_rank"), 1, 1_000)
        listOf("source", "surface","section_key").forEach { if(it in p || it!="section_key")require(p[it]?.jsonPrimitive?.let { v -> v.isString && Regex("[a-z][a-z0-9_]{0,99}").matches(v.content) } == true) }
        listOf("presentation_id", "impression_event_local_id","offline_pack_id").forEach { if (it in p) id(p.getValue(it)) }
        if ("impression_event_server_id" in p && p["impression_event_server_id"] != JsonNull) id(p.getValue("impression_event_server_id"))
        if ("display_position" in p) number(p.getValue("display_position"), 1, 1_000)
    }
    fun interaction(p:JsonObject):Unit {
        val kind=p["interaction_type"] as? JsonPrimitive?:invalid();require(kind.isString)
        val r=p["recommendation"] as? JsonObject?:invalid();attribution(r)
        when(kind.content){
            "RECOMMENDATION_IMPRESSION_RECORDED"->{id(r.getValue("presentation_id"));number(r.getValue("display_position"),1,1_000)}
            "RECOMMENDATION_FEEDBACK_RECORDED"->{id(r.getValue("impression_event_local_id"));require(p["feedback_type"]?.jsonPrimitive?.let{it.isString&&it.content in setOf("SELECTED","DISMISSED")}==true)}
            else->invalid()
        }
    }
    private fun id(e: JsonElement): Unit { val p = e as? JsonPrimitive ?: invalid(); require(p.isString); AccountUuid(p.content) }
    private fun number(e: JsonElement, min: Long, max: Long): Unit {
        val p = e as? JsonPrimitive ?: invalid(); require(!p.isString && p.longOrNull?.let { it in min..max } == true)
    }
    private fun nullableText(e: JsonElement, max: Int): Unit { if (e != JsonNull) { val p = e as? JsonPrimitive ?: invalid(); require(p.isString); text(p.content, max, true) } }
    private fun text(value: String, max: Int, empty: Boolean = false): Unit {
        require(value.codePointCount(0, value.length) in (if (empty) 0 else 1)..max && '\u0000' !in value)
    }
    private fun invalid(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)
}
