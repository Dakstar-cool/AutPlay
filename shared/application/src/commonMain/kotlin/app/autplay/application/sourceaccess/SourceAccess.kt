package app.autplay.application.sourceaccess

/** G1 location metadata is private; these projections contain no bookmark, path or grant. */
public object SourceAccessLimits {
    public const val SOURCES: Int = 256
    public const val BOOKMARK_BYTES: Int = 65_536
    public const val IDENTITY_BYTES: Int = 4_096
    public const val LABEL_BYTES: Int = 256
    public const val LEASES: Int = 8
    public const val FILES: Int = 2_048
    public const val ENTRIES: Int = 10_000
    public const val DEPTH: Int = 16
    public const val FILE_BYTES: Long = 2_147_483_648
    public const val BATCH_BYTES: Long = 8_589_934_592
    public const val ACTIVE_MS: Long = 900_000
    public const val MAIN_BYTES: Long = 33_554_432
    public const val COMBINED_BYTES: Long = 67_108_864
    public const val FREE_BYTES: Long = 268_435_456
}

public enum class SourceKind { FILE, DIRECTORY }
public enum class SourceAvailability { READY, DISABLED, REPAIR_REQUIRED, DRAINING, UNSUPPORTED }

public data class RememberedSource(
    public val id: String,
    public val revision: Long,
    public val label: String,
    public val kind: SourceKind?,
    public val enabled: Boolean,
    public val availability: SourceAvailability,
)

public data class SourceAccessState(
    public val revision: Long = 0,
    public val available: Boolean = false,
    public val busy: Boolean = false,
    public val fenced: Boolean = false,
    public val sources: List<RememberedSource> = emptyList(),
    public val errorCode: String? = null,
)

/** All actions capture the exact displayed state and row. Rescan leads to import review. */
public class SourceAccessActions(
    public val remember: (SourceAccessState) -> Unit,
    public val enable: (SourceAccessState, RememberedSource) -> Unit,
    public val disable: (SourceAccessState, RememberedSource) -> Unit,
    public val forget: (SourceAccessState, RememberedSource) -> Unit,
    public val rescan: (SourceAccessState, RememberedSource) -> Unit,
    public val repair: (SourceAccessState, RememberedSource) -> Unit,
)
