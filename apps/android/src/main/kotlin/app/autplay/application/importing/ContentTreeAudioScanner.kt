package app.autplay.application.importing

import android.content.ContentResolver
import android.provider.DocumentsContract
import androidx.core.net.toUri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class ContentTreeScanResult(
    val documentUris: List<String>,
    val truncated: Boolean,
)

/** Bounded SAF traversal. It never converts a document capability into a raw filesystem path. */
class ContentTreeAudioScanner(private val resolver: ContentResolver) {
    suspend fun scan(rawTreeUri: String, maxAudioFiles: Int = MAX_AUDIO_FILES): ContentTreeScanResult =
        withContext(Dispatchers.IO) {
            require(maxAudioFiles in 1..MAX_AUDIO_FILES)
            val treeUri = rawTreeUri.toUri()
            require(treeUri.scheme == ContentResolver.SCHEME_CONTENT && DocumentsContract.isTreeUri(treeUri)) {
                "LIBRARY_ROOT_INVALID"
            }
            val pending = ArrayDeque<String>()
            val seenDirectories = hashSetOf<String>()
            pending.add(DocumentsContract.getTreeDocumentId(treeUri))
            val audioUris = mutableListOf<String>()
            var visited = 0
            while (pending.isNotEmpty() && audioUris.size < maxAudioFiles) {
                val parentId = pending.removeFirst()
                if (!seenDirectories.add(parentId)) continue
                val children = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentId)
                resolver.query(children, PROJECTION, null, null, null)?.use { cursor ->
                    val idIndex = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
                    val nameIndex = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
                    val mimeIndex = cursor.getColumnIndexOrThrow(DocumentsContract.Document.COLUMN_MIME_TYPE)
                    while (cursor.moveToNext()) {
                        check(++visited <= MAX_VISITED_DOCUMENTS) { "LIBRARY_ROOT_TOO_LARGE" }
                        val documentId = cursor.getString(idIndex)
                        val name = cursor.getString(nameIndex).orEmpty()
                        val mime = cursor.getString(mimeIndex).orEmpty()
                        if (mime == DocumentsContract.Document.MIME_TYPE_DIR) {
                            pending.add(documentId)
                        } else if (mime.startsWith("audio/") || AUDIO_EXTENSIONS.any { name.endsWith(it, ignoreCase = true) }) {
                            audioUris += DocumentsContract.buildDocumentUriUsingTree(treeUri, documentId).toString()
                            if (audioUris.size == maxAudioFiles) break
                        }
                    }
                }
            }
            ContentTreeScanResult(
                audioUris,
                audioUris.size == maxAudioFiles || pending.isNotEmpty() || visited >= MAX_VISITED_DOCUMENTS,
            )
        }

    private companion object {
        const val MAX_AUDIO_FILES = 2_000
        const val MAX_VISITED_DOCUMENTS = 20_000
        val PROJECTION = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
        )
        val AUDIO_EXTENSIONS = setOf(".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav")
    }
}
