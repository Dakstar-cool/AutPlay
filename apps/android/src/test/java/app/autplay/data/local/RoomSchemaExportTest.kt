package app.autplay.data.local

import java.io.File
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RoomSchemaExportTest {
    @Test
    fun exportedSchemasMatchExactApprovedManifests() {
        APPROVED_SCHEMA_SHA256.forEach { (version, approvedHash) ->
            val schema = File("schemas/app.autplay.data.local.AutPlayDatabase/$version.json")
            assertTrue("Room schema export is missing: ${schema.absolutePath}", schema.isFile)
            val text = schema.readText()
            val tableNames = Regex("\\\"tableName\\\": \\\"([^\\\"]+)\\\"")
                .findAll(text)
                .map { match -> match.groupValues[1] }
                .toSet()

            val expectedTables = when (version) {
                8 -> APPROVED_TABLES + P09_TABLES + P10_TABLES
                9 -> APPROVED_TABLES + P09_TABLES + P10_TABLES + P11_TABLES
                else -> APPROVED_TABLES
            }
            assertEquals(expectedTables, tableNames)
            assertEquals(approvedHash, text.normalizedSha256())
            assertTrue(text.contains("USING FTS5"))
            assertTrue(text.contains("content=`track_search_content`"))
            assertFalse(text.contains("compatibility_probe"))
        }
        val v2 = File("schemas/app.autplay.data.local.AutPlayDatabase/2.json").readText()
        assertTrue(v2.contains("active_listening_event_id"))
        assertTrue(v2.contains("recommendation_attribution_json"))
        assertTrue(v2.contains("server_profile_id"))
        val v9 = File("schemas/app.autplay.data.local.AutPlayDatabase/9.json").readText()
        assertTrue(v9.contains("owner_user_id"))
        assertTrue(v9.contains("recommendation_presentation"))
        assertTrue(v9.contains("impression_event_id"))
    }

    private companion object {
        val APPROVED_SCHEMA_SHA256 = mapOf(
            1 to "f063c8ec14ecf8c1fbd7d926f5e9322021e1187c2bf6c486c6b9a6aed88924d2",
            2 to "c69acd49acceadf9c1c92874ab2eca9069c6958f1bd4c313136ed8a5e80d3acf",
            8 to "7639eb1f005957e057a76812ec4a1a7a2699ed5c451443b4883dda309d73f82c",
            9 to "f7764762cdc29efe25c285e53b0cce6c513dfba0e4a491dfc9ffd2bdcb915d62",
        )

        val APPROVED_TABLES = setOf(
            "recording_projection",
            "release_projection",
            "release_track_projection",
            "user_track_ref",
            "user_track_external_ref",
            "library_entry",
            "user_track_preference",
            "playlist",
            "playlist_entry",
            "local_audio_state",
            "download_intent",
            "queue_snapshot",
            "queue_entry",
            "listening_event",
            "journal_lineage",
            "offline_journal_event",
            "local_mutation_outbox",
            "sync_cursor",
            "tombstone",
            "sync_conflict",
            "recommendation_pack",
            "track_search_content",
            "track_search_fts",
            "applied_server_event",
            "deferred_server_event",
            "aggregate_redirect",
        )

        val P09_TABLES = setOf(
            "sync_runtime_status",
            "sync_bootstrap_state",
            "recommendation_interaction_fact",
        )

        val P10_TABLES = setOf(
            "local_import_job",
            "local_import_entry",
            "match_decision",
            "match_candidate",
        )

        val P11_TABLES = setOf("recommendation_presentation")
    }

    private fun String.normalizedSha256(): String = MessageDigest.getInstance("SHA-256")
        .digest(replace("\r\n", "\n").toByteArray(StandardCharsets.UTF_8))
        .joinToString(separator = "") { byte -> "%02x".format(byte.toInt() and 0xff) }
}
